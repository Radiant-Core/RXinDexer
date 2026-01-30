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
    NAME = b'WN'      # WN + name_hash -> ref
    ZONE = b'WZ'      # WZ + ref -> zone records (CBOR)
    OWNER = b'WO'     # WO + ref -> owner scripthash
    HEIGHT = b'WH'    # WH + ref -> registration height


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
        
        # Get zone data from app.data.zone
        app_data = metadata.get('app', {}).get('data', {})
        zone = app_data.get('zone', {})
        
        records.address = zone.get('address')
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
            return
        
        # Check for valid WAVE structure (38 outputs)
        if len(tx.outputs) < WAVE_OUTPUT_COUNT:
            self.logger.debug(f'WAVE tx {hash_to_hex_str(tx_hash)} has insufficient outputs')
            return
        
        # Extract name and zone from metadata
        metadata = glyph_envelope.get('metadata', {})
        app_data = metadata.get('app', {}).get('data', {})
        name = app_data.get('name', '')
        parent_name = app_data.get('parent')
        
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
        self._index_name_in_tree(name, parent_ref, claim_ref)
        
        # Store name -> ref mapping
        name_hash = name_to_hash(name)
        self.name_cache[name_hash] = claim_ref
        
        # Store zone records
        zone = WaveZoneRecords.from_metadata(metadata)
        if HAS_CBOR:
            self.zone_cache[claim_ref] = cbor2.dumps(zone.to_dict())
        
        # Store owner (scripthash of claim output)
        from electrumx.lib.script import Script
        claim_script = tx.outputs[0].pk_script
        owner_scripthash = Script.hashX_from_script(Script.zero_refs(claim_script))
        self.owner_cache[claim_ref] = owner_scripthash
        
        self.logger.debug(f'Indexed WAVE name "{name}" at height {height}')
    
    def _index_name_in_tree(self, name: str, parent_ref: bytes, claim_ref: bytes):
        """
        Index a name in the prefix tree.
        
        For each character in the name, create a tree entry:
        parent_ref + output_index -> next_ref
        """
        current_ref = parent_ref
        normalized = normalize_name(name)
        
        for i, char in enumerate(normalized):
            output_idx = char_to_output_index(char)
            tree_key = current_ref + struct.pack('<B', output_idx)
            
            if i == len(normalized) - 1:
                # Last character points to claim ref
                self.tree_cache[tree_key] = claim_ref
            else:
                # Intermediate: we need to track the path
                # For now, store claim_ref for all steps (simplified)
                self.tree_cache[tree_key] = claim_ref
            
            # Move to next level (would need child ref from tx outputs)
            # This is simplified - full impl needs to track branch outputs
            current_ref = claim_ref
    
    def _resolve_name_to_ref(self, name: str) -> Optional[bytes]:
        """Resolve a name to its claim ref."""
        name_hash = name_to_hash(name)
        
        # Check cache
        if name_hash in self.name_cache:
            return self.name_cache[name_hash]
        
        # Check database
        key = WaveDBKeys.NAME + name_hash
        return self.db.utxo_db.get(key)
    
    def flush(self, batch):
        """Flush cached WAVE data to the database."""
        if not self.enabled:
            return
        
        # Flush tree entries
        for tree_key, child_ref in self.tree_cache.items():
            batch.put(WaveDBKeys.TREE + tree_key, child_ref)
        
        # Flush name -> ref mappings
        for name_hash, ref in self.name_cache.items():
            batch.put(WaveDBKeys.NAME + name_hash, ref)
        
        # Flush zone records
        for ref, zone_cbor in self.zone_cache.items():
            batch.put(WaveDBKeys.ZONE + ref, zone_cbor)
        
        # Flush owner mappings
        for ref, scripthash in self.owner_cache.items():
            batch.put(WaveDBKeys.OWNER + ref, scripthash)
        
        # Clear caches
        count = len(self.tree_cache) + len(self.name_cache)
        self.tree_cache.clear()
        self.name_cache.clear()
        self.zone_cache.clear()
        self.owner_cache.clear()
        
        if count > 0:
            self.logger.info(f'Flushed {count} WAVE entries')
    
    # ========================================================================
    # Query Methods (API)
    # ========================================================================
    
    def resolve(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Resolve a WAVE name to its zone records and owner.
        
        Returns None if name is not registered.
        """
        valid, error = validate_wave_name(name)
        if not valid:
            return {'error': error}
        
        # Check hot cache
        normalized = normalize_name(name)
        if normalized in self.hot_names:
            info = self.hot_names[normalized]
            return self._name_info_to_dict(info)
        
        # Look up ref
        ref = self._resolve_name_to_ref(name)
        if not ref:
            return None  # Name not registered
        
        # Get zone records
        zone = self._get_zone_records(ref)
        
        # Get owner
        owner = self._get_owner(ref)
        
        return {
            'name': normalized,
            'ref': self._format_ref(ref),
            'zone': zone.to_dict() if zone else {},
            'owner': owner.hex() if owner else None,
            'available': False,
        }
    
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
        """Find WAVE names owned by a scripthash."""
        results = []
        
        # Iterate over owner entries (this is inefficient without a reverse index)
        # A production implementation would maintain scripthash -> refs index
        prefix = WaveDBKeys.OWNER
        
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            if len(results) >= limit:
                break
            
            if value == scripthash:
                ref = key[len(prefix):]
                # Look up name for this ref (need reverse lookup)
                # Simplified: just return the ref
                results.append({
                    'ref': self._format_ref(ref),
                })
        
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
        return {
            'name': info.name,
            'ref': self._format_ref(info.ref),
            'zone': info.zone.to_dict() if info.zone else {},
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
    
    def stats(self) -> Dict[str, Any]:
        """Get WAVE index statistics."""
        return {
            'enabled': self.enabled,
            'genesis_configured': self.genesis_ref is not None,
            'tree_cache_size': len(self.tree_cache),
            'name_cache_size': len(self.name_cache),
            'hot_names': len(self.hot_names),
        }


# API method registration
WAVE_METHODS = {
    'wave.resolve': 'wave_resolve',
    'wave.check_available': 'wave_check_available',
    'wave.get_subdomains': 'wave_get_subdomains',
    'wave.reverse_lookup': 'wave_reverse_lookup',
}
