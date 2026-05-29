"""
WAVE Naming System Index for RXinDexer

Implements REP-3011: WAVE Protocol - A Peer-to-Peer Radiant Blockchain Name System

WAVE names are indexed using a prefix tree where each character maps to an output index:
- Character set: a-z (0-25), 0-9 (26-35), hyphen (36)
- Output 0: Claim Token
- Outputs 1-37: Branch outputs for child names
- Output index = char_index + 1

Database Schema:
- wave_tree: parent_ref + output_index -> child_ref (prefix tree)
- wave_names: normalized_name_hash -> ref (reverse lookup)
- wave_zones: ref -> zone records (cached metadata)
"""

import struct
from typing import Optional, Dict, Any, List, Tuple, Set
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash, sha256
from electrumx.lib.util import pack_be_uint32, encode_undo, decode_undo
from electrumx.lib.glyph import GlyphProtocol

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False


# WAVE character set (37 characters)
WAVE_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789-'
WAVE_OUTPUT_COUNT = 38  # 1 claim + 37 branches

# WAVE name limits
WAVE_MIN_NAME_LENGTH = 1
WAVE_MAX_NAME_LENGTH = 63
WAVE_MAX_SUBDOMAIN_DEPTH = 127


# Database key prefixes
class WaveDBKeys:
    TREE = b'WT'      # WT + parent_ref + output_index -> child_ref
    NAME = b'WN'      # WN + name_hash -> ref (CANONICAL - first registration only)
    ZONE = b'WZ'      # WZ + ref -> zone records (CBOR)
    OWNER = b'WO'     # WO + ref -> owner scripthash
    REVERSE_OWNER = b'WR'  # WR + scripthash + ref -> '' (reverse index)
    HEIGHT = b'WH'    # WH + ref -> registration height
    UNDO = b'WVU'      # WVU + height(be) -> repr([(key, prev_value_or_None), ...])
    DUPLICATE = b'WD' # WD + name_hash + height + tx_idx -> ref (duplicate registrations)


class WaveZoneRecords:
    """WAVE zone record storage."""
    __slots__ = ('address', 'avatar', 'display', 'description', 'url', 
                 'email', 'a_record', 'aaaa_record', 'cname', 'txt', 
                 'mx', 'ns', 'custom')
    
    def __init__(self):
        self.address = None      # Radiant payment address
        self.avatar = None       # Avatar URL or content hash
        self.display = None      # Display name (Unicode)
        self.description = None  # Profile description
        self.url = None          # Website URL
        self.email = None        # Contact email
        self.a_record = None     # IPv4 address
        self.aaaa_record = None  # IPv6 address
        self.cname = None        # Canonical name alias
        self.txt = None          # Text records (list)
        self.mx = None           # Mail exchange records (list)
        self.ns = None           # Nameserver records (list)
        self.custom = None       # Custom x-* records (dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to API dict."""
        result = {}
        if self.address:
            result['address'] = self.address
        if self.avatar:
            result['avatar'] = self.avatar
        if self.display:
            result['display'] = self.display
        if self.description:
            result['desc'] = self.description
        if self.url:
            result['url'] = self.url
        if self.email:
            result['email'] = self.email
        if self.a_record:
            result['A'] = self.a_record
        if self.aaaa_record:
            result['AAAA'] = self.aaaa_record
        if self.cname:
            result['CNAME'] = self.cname
        if self.txt:
            result['TXT'] = self.txt
        if self.mx:
            result['MX'] = self.mx
        if self.ns:
            result['NS'] = self.ns
        if self.custom:
            result.update(self.custom)
        return result
    
    @classmethod
    def from_metadata(cls, metadata: Dict[str, Any]) -> 'WaveZoneRecords':
        """Parse zone records from Glyph metadata."""
        records = cls()
        
        # Get zone data from app.data.zone (original format)
        app_data = metadata.get('app', {}).get('data', {})
        zone = app_data.get('zone', {})
        
        # Support Photonic wallet format: attrs.target holds the address
        attrs = metadata.get('attrs', {})
        attrs_address = attrs.get('target') if attrs.get('target_type', 'address') == 'address' else None
        
        records.address = zone.get('address') or attrs_address
        records.avatar = zone.get('avatar')
        records.display = zone.get('display')
        records.description = zone.get('desc')
        records.url = zone.get('url')
        records.email = zone.get('email')
        records.a_record = zone.get('A')
        records.aaaa_record = zone.get('AAAA')
        records.cname = zone.get('CNAME')
        records.txt = zone.get('TXT')
        records.mx = zone.get('MX')
        records.ns = zone.get('NS')
        
        # Collect custom x-* records
        records.custom = {k: v for k, v in zone.items() if k.startswith('x-')}
        
        return records


class WaveNameInfo:
    """Represents an indexed WAVE name."""
    __slots__ = ('ref', 'name', 'parent_ref', 'owner_scripthash', 
                 'registration_height', 'zone', 'is_spent')
    
    def __init__(self):
        self.ref = b''
        self.name = ''
        self.parent_ref = None
        self.owner_scripthash = b''
        self.registration_height = 0
        self.zone = None
        self.is_spent = False
    
    def to_bytes(self) -> bytes:
        """Serialize to CBOR bytes for database storage."""
        import cbor2
        data = {
            'ref': self.ref,
            'name': self.name,
            'parent_ref': self.parent_ref,
            'owner': self.owner_scripthash,
            'height': self.registration_height,
            'zone': self.zone,
            'spent': self.is_spent
        }
        return cbor2.dumps(data)
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'WaveNameInfo':
        """Deserialize from CBOR bytes."""
        import cbor2
        d = cbor2.loads(data)
        info = cls()
        info.ref = d.get('ref', b'')
        info.name = d.get('name', '')
        info.parent_ref = d.get('parent_ref')
        info.owner_scripthash = d.get('owner', b'')
        info.registration_height = d.get('height', 0)
        info.zone = d.get('zone')
        info.is_spent = d.get('spent', False)
        return info


def char_to_index(char: str) -> int:
    """Convert a character to its WAVE index (0-36)."""
    idx = WAVE_CHARS.find(char.lower())
    if idx == -1:
        raise ValueError(f'Invalid WAVE character: {char}')
    return idx


def index_to_char(index: int) -> str:
    """Convert a WAVE index (0-36) to its character."""
    if index < 0 or index >= len(WAVE_CHARS):
        raise ValueError(f'Invalid WAVE index: {index}')
    return WAVE_CHARS[index]


def char_to_output_index(char: str) -> int:
    """Get the output index for a character's branch (1-37)."""
    return char_to_index(char) + 1


def output_index_to_char(output_index: int) -> str:
    """Get the character for a branch output index."""
    if output_index < 1 or output_index > 37:
        raise ValueError(f'Invalid branch output index: {output_index}')
    return index_to_char(output_index - 1)


def validate_wave_name(name: str) -> Tuple[bool, Optional[str]]:
    """Validate a WAVE name. Returns (valid, error_message)."""
    if not name:
        return False, 'Name cannot be empty'
    
    if len(name) > WAVE_MAX_NAME_LENGTH:
        return False, f'Name exceeds maximum length of {WAVE_MAX_NAME_LENGTH}'
    
    if name.startswith('-'):
        return False, 'Name cannot start with hyphen'
    
    if name.endswith('-'):
        return False, 'Name cannot end with hyphen'
    
    # Check for consecutive hyphens (except Punycode prefix)
    if '--' in name and not name.lower().startswith('xn--'):
        return False, 'Name cannot contain consecutive hyphens (except Punycode prefix)'
    
    # Check all characters are valid
    for char in name.lower():
        if char not in WAVE_CHARS:
            return False, f'Invalid character: {char}'
    
    return True, None


def normalize_name(name: str) -> str:
    """Normalize a WAVE name (lowercase, strip whitespace)."""
    return name.lower().strip()


def name_to_hash(name: str) -> bytes:
    """Hash a normalized name for database lookup."""
    return sha256(normalize_name(name).encode('utf-8'))[:16]


class WaveIndex:
    """
    WAVE naming system index manager.
    
    Implements prefix tree indexing for O(n) name resolution where n is name length.
    Maintains reverse lookup index for address->name queries.
    """
    
    def __init__(self, db, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        self.enabled = getattr(env, 'wave_index', True)
        
        # Genesis ref (configured per network)
        genesis_ref_str = getattr(env, 'wave_genesis_ref', None)
        self.genesis_ref = None
        if genesis_ref_str:
            try:
                txid_hex, vout_str = genesis_ref_str.split('_')
                txid = bytes.fromhex(txid_hex)[::-1]
                vout = int(vout_str)
                self.genesis_ref = txid + struct.pack('<I', vout)
            except Exception as e:
                self.logger.warning(f'Invalid WAVE_GENESIS_REF: {e}')
        
        # In-memory caches for unflushed data
        self.tree_cache: Dict[bytes, bytes] = {}  # parent+idx -> child_ref
        self.name_cache: Dict[bytes, bytes] = {}  # name_hash -> ref
        self.zone_cache: Dict[bytes, bytes] = {}  # ref -> zone cbor
        self.owner_cache: Dict[bytes, bytes] = {}  # ref -> scripthash

        self.tree_height: Dict[bytes, int] = {}
        self.name_height: Dict[bytes, int] = {}
        self.zone_height: Dict[bytes, int] = {}
        self.owner_height: Dict[bytes, int] = {}

        # Per-height undo info for reorg safety
        self._undo_cache: Dict[int, List[Tuple[bytes, Optional[bytes]]]] = defaultdict(list)
        self._undo_seen: Dict[int, Set[bytes]] = defaultdict(set)
        self._pending_heights: Dict[str, int] = {}

        # Undo retention: keep at most env.reorg_limit heights of undo data.
        current_height = getattr(db, 'db_height', -1)
        reorg_limit = getattr(env, 'reorg_limit', 0)
        min_keep = max(0, current_height - reorg_limit + 1) if reorg_limit else 0
        self._last_undo_pruned = min_keep - 1
        
        # Hot name cache (frequently accessed names in memory)
        self.hot_names: Dict[str, WaveNameInfo] = {}
        self.hot_name_limit = getattr(env, 'wave_hot_names', 10000)
        
        if self.enabled:
            self.logger.info('WAVE name indexing enabled')
            if self.genesis_ref:
                self.logger.info(f'WAVE genesis ref: {genesis_ref_str}')
            else:
                self.logger.warning('WAVE_GENESIS_REF not configured')
    
    def process_tx(self, tx_hash: bytes, tx, height: int, tx_idx: int,
                   glyph_envelope: Dict[str, Any] = None):
        """
        Process a transaction for WAVE name registration/update.
        
        A WAVE registration has:
        - Protocol [2, 5, 11] = NFT + Mutable + WAVE
        - 38 outputs (1 claim + 37 branches)
        - Metadata with app.namespace = 'rxd.wave'
        """
        if not self.enabled:
            return
        
        if not glyph_envelope:
            return
        
        protocols = glyph_envelope.get('protocols', [])
        if GlyphProtocol.GLYPH_WAVE not in protocols:
            self.logger.debug(
                f'WAVE skip tx {hash_to_hex_str(tx_hash)}: '
                f'protocols={protocols} (no GLYPH_WAVE={GlyphProtocol.GLYPH_WAVE})'
            )
            return
        
        self.logger.info(
            f'WAVE candidate tx {hash_to_hex_str(tx_hash)} at height {height}, '
            f'protocols={protocols}'
        )
        
        # Extract name and zone from metadata
        metadata = glyph_envelope.get('metadata', {})
        
        # Support both Photonic wallet format (attrs.name) and
        # the original app.data.name format
        attrs = metadata.get('attrs', {})
        app_data = metadata.get('app', {}).get('data', {})
        name = attrs.get('name', '') or app_data.get('name', '')
        parent_name = attrs.get('domain') if not app_data.get('parent') else app_data.get('parent')
        # A domain value of 'rxd' is the root — no parent lookup needed
        if parent_name == 'rxd':
            parent_name = None
        
        if not name:
            self.logger.debug(f'WAVE tx {hash_to_hex_str(tx_hash)} has no name')
            return
        
        # Validate name
        valid, error = validate_wave_name(name)
        if not valid:
            self.logger.debug(f'Invalid WAVE name "{name}": {error}')
            return
        
        # Build ref for claim token (output 0)
        claim_ref = tx_hash + struct.pack('<I', 0)
        
        # Determine parent ref
        parent_ref = None
        if parent_name:
            # Look up parent ref
            parent_ref = self._resolve_name_to_ref(parent_name)
            if not parent_ref:
                self.logger.debug(f'Parent name "{parent_name}" not found for "{name}"')
                return
        else:
            # Top-level name, parent is genesis
            parent_ref = self.genesis_ref
        
        if not parent_ref:
            self.logger.debug(f'No parent ref for WAVE name "{name}"')
            return
        
        # Index the name in the prefix tree
        self._index_name_in_tree(name, parent_ref, claim_ref, height, tx_hash=tx_hash)
        
        # Store name -> ref mapping
        name_hash = name_to_hash(name)
        
        # Check if this name is already registered (first registration wins)
        existing_ref = self._resolve_name_to_ref(name)
        is_duplicate = existing_ref is not None
        
        if is_duplicate:
            # Store as duplicate - do NOT overwrite canonical mapping
            # Duplicate key: WD + name_hash + height(4B) + tx_idx(4B) -> claim_ref
            dup_key = WaveDBKeys.DUPLICATE + name_hash + struct.pack('<II', height, tx_idx)
            self.name_cache[dup_key] = claim_ref
            self.name_height[dup_key] = height
            self.logger.warning(
                f'WAVE duplicate registration for "{name}" at height {height}, tx_idx {tx_idx}. '
                f'Original: {existing_ref.hex()[:16]}..., Duplicate: {claim_ref.hex()[:16]}...'
            )
        else:
            # First registration - store as canonical
            self.name_cache[name_hash] = claim_ref
            self.name_height[name_hash] = height
        
        # Store zone records (for both canonical and duplicates)
        zone = WaveZoneRecords.from_metadata(metadata)
        if HAS_CBOR:
            self.zone_cache[claim_ref] = cbor2.dumps(zone.to_dict())
            self.zone_height[claim_ref] = height
        
        status = "DUPLICATE" if is_duplicate else "canonical"
        self.logger.info(f'Indexed WAVE name "{name}" ({status}) target={zone.address!r} at height {height}')
        
        # Mark duplicate in Glyph index if available
        if is_duplicate and hasattr(self.env, 'glyph_index') and self.env.glyph_index:
            try:
                # Mark the token as a duplicate in the Glyph index
                token = self.env.glyph_index.get_token(claim_ref)
                if token:
                    token.is_wave_duplicate = True
                    # Re-serialize and store back
                    self.env.glyph_index.token_cache[claim_ref] = token
                    self.env.glyph_index.token_height[claim_ref] = height
                    self.logger.info(f'Marked WAVE token {claim_ref.hex()[:16]}... as duplicate in Glyph index')
            except Exception as e:
                self.logger.warning(f'Failed to mark duplicate WAVE token in Glyph index: {e}')
        
        # Store owner (scripthash of claim output)
        from electrumx.lib.script import Script
        claim_script = tx.outputs[0].pk_script
        owner_scripthash = self.env.coin.hashX_from_script(Script.zero_refs(claim_script))
        self.owner_cache[claim_ref] = owner_scripthash
        self.owner_height[claim_ref] = height

        # Track height for flush/undo
        self._pending_heights[claim_ref.hex()] = height
        
    
    def _index_name_in_tree(self, name: str, parent_ref: bytes, claim_ref: bytes,
                             height: int, tx_hash: bytes = None):
        """
        Index a name in the prefix tree.
        
        For each character in the name, create a tree entry:
        parent_ref + output_index -> next_ref
        
        Branch outputs follow the WAVE convention:
        - Output 0: Claim token
        - Output 1-37: Branch outputs for child name characters
        
        Intermediate nodes point to the branch output ref (tx_hash + branch_vout)
        so that child name registrations can chain from them.
        The last character points to the claim ref (output 0).
        """
        current_ref = parent_ref
        normalized = normalize_name(name)
        
        for i, char in enumerate(normalized):
            output_idx = char_to_output_index(char)
            tree_key = current_ref + struct.pack('<B', output_idx)
            
            if i == len(normalized) - 1:
                # Last character points to claim ref (output 0)
                self.tree_cache[tree_key] = claim_ref
            else:
                # Intermediate node: points to the branch output for this character.
                # The branch output index in the tx is the same as the character's
                # output_idx. The ref is tx_hash + branch_vout.
                if tx_hash:
                    branch_ref = tx_hash + struct.pack('<I', output_idx)
                else:
                    branch_ref = claim_ref
                self.tree_cache[tree_key] = branch_ref
                # Next level starts from this branch output
                current_ref = branch_ref
                self.tree_height[tree_key] = height
                continue
            
            self.tree_height[tree_key] = height
    
    def _resolve_name_to_ref(self, name: str) -> Optional[bytes]:
        """Resolve a name to its claim ref."""
        name_hash = name_to_hash(name)
        
        # Check cache
        if name_hash in self.name_cache:
            return self.name_cache[name_hash]
        
        # Check database
        key = WaveDBKeys.NAME + name_hash
        return self.db.utxo_db.get(key)

    def _undo_key(self, height: int) -> bytes:
        return WaveDBKeys.UNDO + pack_be_uint32(height)

    def _record_undo(self, height: int, key: bytes):
        if not self.enabled:
            return
        if key in self._undo_seen[height]:
            return
        self._undo_seen[height].add(key)
        prev_value = self.db.utxo_db.get(key)
        self._undo_cache[height].append((key, prev_value))

    def backup(self, batch, height: int):
        """Revert WAVE keys written at the given height (reorg unwind)."""
        if not self.enabled:
            return
        raw = self.db.utxo_db.get(self._undo_key(height))
        if not raw:
            return
        entries = decode_undo(raw)  # R22
        for key, prev in entries:
            if prev is None:
                batch.delete(key)
            else:
                batch.put(key, prev)
        batch.delete(self._undo_key(height))

    def _prune_old_undo_keys(self, batch):
        """Delete undo keys that are older than the reorg window."""
        reorg_limit = getattr(self.env, 'reorg_limit', 0)
        if not reorg_limit:
            return

        min_keep = max(0, self.db.db_height - reorg_limit + 1)
        prune_to = min_keep - 1
        if prune_to <= self._last_undo_pruned:
            return

        for height in range(self._last_undo_pruned + 1, prune_to + 1):
            batch.delete(self._undo_key(height))
        self._last_undo_pruned = prune_to
    
    def memory_estimate(self) -> int:
        '''Approximate bytes held by unflushed in-memory caches.

        Used by block_processor.check_cache_size() to trigger a flush before
        these caches grow large enough to OOM the process.
        '''
        if not self.enabled:
            return 0
        undo_entries = sum(len(v) for v in self._undo_cache.values())
        return (
            len(self.tree_cache) * 190
            + len(self.name_cache) * 190
            + len(self.zone_cache) * 600
            + len(self.owner_cache) * 190
            + len(self.tree_height) * 140
            + len(self.name_height) * 140
            + len(self.zone_height) * 140
            + len(self.owner_height) * 140
            + undo_entries * 120
            + len(self._pending_heights) * 140
            + len(self.hot_names) * 400
        )

    def flush(self, batch):
        """Flush cached WAVE data to the database."""
        if not self.enabled:
            return
        # Important: record undo entries for keys touched during this flush
        # first, then persist undo records at the end.

        self._prune_old_undo_keys(batch)
        
        # Flush tree entries
        for tree_key, child_ref in self.tree_cache.items():
            height = self.tree_height.get(tree_key)
            key = WaveDBKeys.TREE + tree_key
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, child_ref)
        
        # Flush name -> ref mappings (both canonical and duplicates)
        for name_key, ref in self.name_cache.items():
            height = self.name_height.get(name_key)
            # name_key could be either:
            # 1. name_hash (16 bytes) - canonical registration -> prefix with NAME
            # 2. duplicate key (WD + name_hash + height + tx_idx) - already prefixed
            if len(name_key) == 16:
                # Canonical registration
                key = WaveDBKeys.NAME + name_key
            else:
                # Duplicate entry - key is already fully formed (starts with WD)
                key = name_key
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, ref)
        
        # Flush zone records
        for ref, zone_cbor in self.zone_cache.items():
            height = self.zone_height.get(ref)
            key = WaveDBKeys.ZONE + ref
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, zone_cbor)
        
        # Flush owner mappings + reverse owner index
        for ref, scripthash in self.owner_cache.items():
            height = self.owner_height.get(ref)
            key = WaveDBKeys.OWNER + ref
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, scripthash)
            # Write reverse index: scripthash + ref -> ''
            rev_key = WaveDBKeys.REVERSE_OWNER + scripthash + ref
            if height is not None:
                self._record_undo(height, rev_key)
            batch.put(rev_key, b'')

        # Persist undo information last so it includes keys written above.
        for height, entries in sorted(self._undo_cache.items()):
            batch.put(self._undo_key(height), encode_undo(entries))  # R22
        self._undo_cache.clear()
        self._undo_seen.clear()
        
        # Clear caches
        count = len(self.tree_cache) + len(self.name_cache)
        self.tree_cache.clear()
        self.name_cache.clear()
        self.zone_cache.clear()
        self.owner_cache.clear()
        self.tree_height.clear()
        self.name_height.clear()
        self.zone_height.clear()
        self.owner_height.clear()
        self._pending_heights.clear()
        
        if count > 0:
            self.logger.info(f'Flushed {count} WAVE entries')
    
    # ========================================================================
    # Backfill (run once when WAVE DB is empty but Glyph DB has WAVE tokens)
    # ========================================================================

    def backfill_from_glyph_db(self, glyph_index):
        """
        Backfill WAVE index from existing Glyph token database.
        
        Scans all tokens with GLYPH_WAVE protocol and indexes their names.
        Called at startup when the WAVE DB is empty but Glyph tokens exist.
        Returns the number of names indexed.
        """
        if not self.enabled or not self.genesis_ref:
            return 0

        # Check if WAVE DB already has data (avoid re-running)
        existing = self._count_db_prefix(WaveDBKeys.NAME, limit=1)
        if existing > 0 or len(self.name_cache) > 0:
            self.logger.info(f'WAVE backfill skipped: already have {existing} names in DB')
            return 0

        self.logger.info('Starting WAVE backfill from Glyph token database...')
        count = 0

        try:
            from electrumx.server.glyph_index import GlyphDBKeys, GlyphTokenInfo
        except ImportError:
            self.logger.warning('Cannot import GlyphIndex — backfill aborted')
            return 0

        # Scan all GT (token) entries
        for key, value in self.db.utxo_db.iterator(prefix=GlyphDBKeys.TOKEN):
            try:
                token = GlyphTokenInfo.from_bytes(value)
            except Exception:
                continue

            if GlyphProtocol.GLYPH_WAVE not in token.protocols:
                continue

            # Extract name — stored in token.name as "name.rxd"
            raw_name = token.name or ''
            # Strip .rxd suffix if present
            if raw_name.endswith('.rxd'):
                name = raw_name[:-4]
            else:
                name = raw_name

            if not name:
                continue

            valid, _ = validate_wave_name(name)
            if not valid:
                continue

            # Build ref from the GT key: GT + ref(36 bytes)
            ref = key[len(GlyphDBKeys.TOKEN):]
            if len(ref) < 36:
                continue
            claim_ref = ref[:36]
            tx_hash = ref[:32]
            height = token.deploy_height or 0

            # Determine parent — all top-level names use genesis
            parent_ref = self.genesis_ref

            # Index in tree
            self._index_name_in_tree(name, parent_ref, claim_ref, height, tx_hash=tx_hash)

            # Store name -> ref
            name_hash = name_to_hash(name)
            self.name_cache[name_hash] = claim_ref
            self.name_height[name_hash] = height

            # Store zone records from token metadata if available
            metadata_hash = token.metadata_hash
            zone = WaveZoneRecords()
            # Try to fetch full metadata from Glyph DB
            if metadata_hash and HAS_CBOR:
                meta_raw = self.db.utxo_db.get(GlyphDBKeys.METADATA + metadata_hash)
                if meta_raw:
                    try:
                        full_metadata = cbor2.loads(meta_raw)
                        zone = WaveZoneRecords.from_metadata(full_metadata)
                    except Exception:
                        pass

            if HAS_CBOR:
                self.zone_cache[claim_ref] = cbor2.dumps(zone.to_dict())
                self.zone_height[claim_ref] = height

            count += 1
            if count % 50 == 0:
                self.logger.info(f'WAVE backfill: indexed {count} names so far...')

        if count > 0:
            self.logger.info(f'WAVE backfill complete: indexed {count} names')
        else:
            self.logger.info('WAVE backfill: no WAVE tokens found in Glyph DB')

        return count

    # ========================================================================
    # Query Methods (API)
    # ========================================================================
    
    def resolve(self, name: str, include_duplicates: bool = False) -> Optional[Dict[str, Any]]:
        """
        Resolve a WAVE name to its zone records and owner.
        
        Always returns the CANONICAL (first) registration.
        Duplicate registrations are tracked but not returned unless requested.
        
        Args:
            name: The WAVE name to resolve
            include_duplicates: If True, include list of duplicate registrations
        
        Returns None if name is not registered.
        """
        valid, error = validate_wave_name(name)
        if not valid:
            return {'error': error}
        
        # Check hot cache
        normalized = normalize_name(name)
        if normalized in self.hot_names:
            info = self.hot_names[normalized]
            result = self._name_info_to_dict(info)
            if include_duplicates:
                result['duplicates'] = self._get_duplicate_registrations(name)
            return result
        
        # Look up canonical ref (first registration)
        ref = self._resolve_name_to_ref(name)
        if not ref:
            return None  # Name not registered
        
        # Get zone records
        zone = self._get_zone_records(ref)
        
        # Get owner
        owner = self._get_owner(ref)
        
        zone_dict = zone.to_dict() if zone else {}
        # Expose the payment address as top-level 'target' for wallet compatibility
        target = zone_dict.get('address')
        
        # Check if there are duplicates
        has_duplicates = self._has_duplicates(name)

        # Populate hot cache on successful resolve
        if len(self.hot_names) < self.hot_name_limit:
            info = WaveNameInfo()
            info.ref = ref
            info.name = normalized
            info.owner_scripthash = owner or b''
            info.zone = zone
            self.hot_names[normalized] = info

        result = {
            'name': normalized,
            'ref': self._format_ref(ref),
            'target': target,
            'zone': zone_dict,
            'owner': owner.hex() if owner else None,
            'available': False,
            'canonical': True,  # This is always the canonical (first) registration
            'has_duplicates': has_duplicates,
        }
        
        if include_duplicates:
            result['duplicates'] = self._get_duplicate_registrations(name)
        
        return result
    
    def _has_duplicates(self, name: str) -> bool:
        """Check if a name has any duplicate registrations."""
        name_hash = name_to_hash(name)
        prefix = WaveDBKeys.DUPLICATE + name_hash
        
        # Check cache first
        for key in self.name_cache:
            if key.startswith(prefix):
                return True
        
        # Check database
        for _key, _value in self.db.utxo_db.iterator(prefix=prefix):
            return True
        return False
    
    def _get_duplicate_registrations(self, name: str) -> List[Dict[str, Any]]:
        """Get all duplicate registrations for a name."""
        name_hash = name_to_hash(name)
        prefix = WaveDBKeys.DUPLICATE + name_hash
        duplicates = []
        
        # Check cache
        for key, ref in self.name_cache.items():
            if key.startswith(prefix):
                # Parse height and tx_idx from key: WD + name_hash(16) + height(4) + tx_idx(4)
                height = struct.unpack('<I', key[18:22])[0]
                tx_idx = struct.unpack('<I', key[22:26])[0]
                zone = self._get_zone_records(ref)
                owner = self._get_owner(ref)
                duplicates.append({
                    'ref': self._format_ref(ref),
                    'height': height,
                    'tx_idx': tx_idx,
                    'target': zone.address if zone else None,
                    'owner': owner.hex() if owner else None,
                    'is_duplicate': True,
                })
        
        # Check database
        for key, ref in self.db.utxo_db.iterator(prefix=prefix):
            # Skip if already in cache
            if key in self.name_cache:
                continue
            height = struct.unpack('<I', key[18:22])[0]
            tx_idx = struct.unpack('<I', key[22:26])[0]
            zone = self._get_zone_records(ref)
            owner = self._get_owner(ref)
            duplicates.append({
                'ref': self._format_ref(ref),
                'height': height,
                'tx_idx': tx_idx,
                'target': zone.address if zone else None,
                'owner': owner.hex() if owner else None,
                'is_duplicate': True,
            })
        
        # Sort by height, then tx_idx
        duplicates.sort(key=lambda x: (x['height'], x['tx_idx']))
        return duplicates
    
    def get_all_registrations(self, name: str) -> Dict[str, Any]:
        """Get canonical registration plus all duplicates for a name."""
        result = self.resolve(name, include_duplicates=True)
        if not result:
            return {'name': name, 'registered': False}
        return result
    
    def check_available(self, name: str) -> Dict[str, Any]:
        """Check if a WAVE name is available for registration."""
        valid, error = validate_wave_name(name)
        if not valid:
            return {'available': False, 'error': error}
        
        ref = self._resolve_name_to_ref(name)
        
        if ref:
            return {
                'available': False,
                'ref': self._format_ref(ref),
                'name': normalize_name(name),
            }
        else:
            return {
                'available': True,
                'name': normalize_name(name),
            }
    
    def get_subdomains(self, parent_name: str, limit: int = 100, 
                       offset: int = 0) -> List[Dict[str, Any]]:
        """Get subdomains of a parent name."""
        # This would iterate over the tree entries for the parent
        # Simplified implementation
        results = []
        
        parent_ref = self._resolve_name_to_ref(parent_name)
        if not parent_ref:
            return results
        
        # Iterate over all possible branch outputs
        count = 0
        for char_idx in range(37):
            output_idx = char_idx + 1
            tree_key = WaveDBKeys.TREE + parent_ref + struct.pack('<B', output_idx)
            child_ref = self.db.utxo_db.get(tree_key)
            
            if child_ref:
                if count >= offset and len(results) < limit:
                    char = index_to_char(char_idx)
                    results.append({
                        'char': char,
                        'ref': self._format_ref(child_ref),
                    })
                count += 1
        
        return results
    
    def reverse_lookup(self, scripthash: bytes, limit: int = 100) -> List[Dict[str, Any]]:
        """Find WAVE names owned by a scripthash.
        
        Uses REVERSE_OWNER index: WR + scripthash + ref -> ''
        """
        results = []
        prefix = WaveDBKeys.REVERSE_OWNER + scripthash
        
        for key, _value in self.db.utxo_db.iterator(prefix=prefix):
            if len(results) >= limit:
                break
            
            ref = key[len(prefix):]
            entry = {'ref': self._format_ref(ref)}
            
            # Try to resolve the name for this ref
            zone = self._get_zone_records(ref)
            owner_sh = self._get_owner(ref)
            if zone:
                entry['zone'] = zone.to_dict()
            if owner_sh:
                entry['owner'] = owner_sh.hex()
            
            results.append(entry)
        
        return results
    
    def _get_zone_records(self, ref: bytes) -> Optional[WaveZoneRecords]:
        """Get zone records for a ref."""
        # Check cache
        if ref in self.zone_cache:
            zone_cbor = self.zone_cache[ref]
        else:
            key = WaveDBKeys.ZONE + ref
            zone_cbor = self.db.utxo_db.get(key)
        
        if zone_cbor and HAS_CBOR:
            try:
                zone_dict = cbor2.loads(zone_cbor)
                records = WaveZoneRecords()
                records.address = zone_dict.get('address')
                records.avatar = zone_dict.get('avatar')
                records.display = zone_dict.get('display')
                records.description = zone_dict.get('desc')
                records.url = zone_dict.get('url')
                records.email = zone_dict.get('email')
                records.a_record = zone_dict.get('A')
                records.aaaa_record = zone_dict.get('AAAA')
                records.cname = zone_dict.get('CNAME')
                records.txt = zone_dict.get('TXT')
                records.mx = zone_dict.get('MX')
                records.ns = zone_dict.get('NS')
                records.custom = {k: v for k, v in zone_dict.items() if k.startswith('x-')}
                return records
            except Exception:
                pass
        return None
    
    def _get_owner(self, ref: bytes) -> Optional[bytes]:
        """Get owner scripthash for a ref."""
        if ref in self.owner_cache:
            return self.owner_cache[ref]
        
        key = WaveDBKeys.OWNER + ref
        return self.db.utxo_db.get(key)
    
    def _name_info_to_dict(self, info: WaveNameInfo) -> Dict[str, Any]:
        """Convert WaveNameInfo to API dict."""
        zone_dict = info.zone.to_dict() if info.zone else {}
        return {
            'name': info.name,
            'ref': self._format_ref(info.ref),
            'target': zone_dict.get('address'),
            'zone': zone_dict,
            'owner': info.owner_scripthash.hex() if info.owner_scripthash else None,
            'registration_height': info.registration_height,
            'available': False,
        }
    
    @staticmethod
    def _format_ref(ref: bytes) -> Optional[str]:
        """Format a ref bytes to string."""
        if not ref or len(ref) < 36:
            return None
        txid = ref[:32]
        vout = struct.unpack('<I', ref[32:36])[0]
        return hash_to_hex_str(txid) + '_' + str(vout)
    
    def list_names(self, limit: int = 500, cursor: Optional[bytes] = None) -> Dict[str, Any]:
        """
        List canonical WAVE names by iterating the WN (NAME) prefix directly.
        Returns only canonical (first-registration-wins) entries.
        Cursor is the raw DB key bytes for pagination.
        """
        results = []
        next_cursor = None
        count = 0

        # Iterate DB entries with WN prefix
        iter_kwargs: Dict[str, Any] = {'prefix': WaveDBKeys.NAME}
        if cursor:
            iter_kwargs['seek'] = cursor

        for key, ref_bytes in self.db.utxo_db.iterator(**iter_kwargs):
            if count >= limit:
                next_cursor = key
                break
            if len(ref_bytes) < 36:
                continue
            zone = self._get_zone_records(ref_bytes)
            zone_dict = zone.to_dict() if zone else {}
            results.append({
                'ref': ref_bytes,
                'target': zone_dict.get('address', ''),
            })
            count += 1

        return {'entries': results, 'next_cursor': next_cursor}

    def _count_db_prefix(self, prefix: bytes, limit: int = 0) -> int:
        """Count entries in the DB with a given key prefix.
        
        Args:
            prefix: Key prefix to count.
            limit: If > 0, stop counting after this many (for perf).
                   0 means count all.
        """
        count = 0
        for _key, _value in self.db.utxo_db.iterator(prefix=prefix):
            count += 1
            if limit and count >= limit:
                break
        return count

    def stats(self) -> Dict[str, Any]:
        """Get WAVE index statistics including DB counts."""
        # DB counts (authoritative — survives flush)
        db_names = self._count_db_prefix(WaveDBKeys.NAME)
        db_zones = self._count_db_prefix(WaveDBKeys.ZONE)
        db_owners = self._count_db_prefix(WaveDBKeys.OWNER)

        return {
            'enabled': self.enabled,
            'genesis_configured': self.genesis_ref is not None,
            'total_names': db_names + len(self.name_cache),
            'total_zones': db_zones + len(self.zone_cache),
            'total_owners': db_owners + len(self.owner_cache),
            'cache_tree': len(self.tree_cache),
            'cache_names': len(self.name_cache),
            'cache_zones': len(self.zone_cache),
            'hot_names': len(self.hot_names),
        }


# API method registration
WAVE_METHODS = {
    'wave.resolve': 'wave_resolve',
    'wave.check_available': 'wave_check_available',
    'wave.get_subdomains': 'wave_get_subdomains',
    'wave.reverse_lookup': 'wave_reverse_lookup',
}
