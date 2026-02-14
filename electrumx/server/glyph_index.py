"""
Glyph Token Index for RXinDexer

This module provides database storage and indexing for Glyph v1/v2 tokens.
Handles token registration, balance tracking, and history.
"""

import ast
import struct
from typing import Optional, Dict, Any, List, Tuple, Set
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash, sha256, HASHX_LEN
from electrumx.lib.util import pack_be_uint32
from electrumx.lib.glyph import (
    GLYPH_MAGIC,
    GlyphProtocol,
    GlyphTokenType,
    parse_glyph_envelope,
    parse_glyph_metadata,
    extract_token_info,
    get_token_type_id,
    get_token_type,
    contains_glyph_magic,
    parse_glyph_from_output,
    format_ref,
    parse_ref,
)

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False


# Database key prefixes for Glyph data
class GlyphDBKeys:
    """Database key prefixes for Glyph index."""
    TOKEN = b'GT'          # GT + ref -> token info
    METADATA = b'GM'       # GM + metadata_hash -> CBOR metadata
    BALANCE = b'GB'        # GB + scripthash + ref -> amount
    HISTORY = b'GH'        # GH + ref + height + tx_idx -> event
    BY_TYPE = b'GY'        # GY + type + ref -> (for type queries)
    BY_NAME = b'GN'        # GN + name_hash -> ref (for search)
    BY_TICKER = b'GK'      # GK + ticker -> ref (for FT lookup)
    SUPPLY = b'GS'         # GS + ref -> current supply (FT only)
    HOLDER_BY_REF = b'GR'  # GR + ref + scripthash -> amount (reverse of BALANCE)
    UNDO = b'GXU'          # GXU + height(be) -> repr([(key, prev_value_or_None), ...])


# History event types
class GlyphEventType:
    DEPLOY = 0
    MINT = 1
    TRANSFER = 2
    BURN = 3
    UPDATE = 4  # Mutable metadata update


def pack_ref(txid_bytes: bytes, vout: int) -> bytes:
    """Pack a ref into bytes (32 bytes txid + 4 bytes vout)."""
    return txid_bytes + struct.pack('<I', vout)


def unpack_ref(data: bytes) -> Tuple[bytes, int]:
    """Unpack a ref from bytes."""
    txid = data[:32]
    vout = struct.unpack('<I', data[32:36])[0]
    return txid, vout


def pack_balance_key(scripthash: bytes, ref: bytes) -> bytes:
    """Pack a balance key."""
    return GlyphDBKeys.BALANCE + scripthash + ref


def pack_holder_key(ref: bytes, scripthash: bytes) -> bytes:
    """Pack a holder-by-ref key (secondary index for token holder lookups)."""
    return GlyphDBKeys.HOLDER_BY_REF + ref + scripthash


def pack_token_key(ref: bytes) -> bytes:
    """Pack a token key."""
    return GlyphDBKeys.TOKEN + ref


def pack_history_key(ref: bytes, height: int, tx_idx: int) -> bytes:
    """Pack a history key."""
    return (GlyphDBKeys.HISTORY + ref + 
            struct.pack('>I', height) + struct.pack('>H', tx_idx))


class GlyphTokenInfo:
    """
    Represents indexed token information.
    
    Stores all fields needed by explorers, wallets, and exchanges.
    """
    __slots__ = (
        # Core identity
        'ref', 'protocols', 'token_type', 'glyph_version', 'name', 'ticker', 'decimals',
        'description', 'author', 'license',
        # Deployment info
        'deploy_height', 'deploy_txid', 'metadata_hash', 'is_spent',
        # Supply tracking
        'total_supply', 'current_supply', 'premine', 'mined_supply',
        # Image/content
        'icon_ref', 'icon_type', 'icon_size', 'embedded_data_hash',
        # dMint specific
        'contract_ref', 'algorithm', 'start_difficulty', 'current_difficulty',
        'reward', 'halving_interval', 'daa_mode', 'mint_count',
        # Relationships
        'container_ref', 'authority_ref', 'parent_ref',
        # NFT specific
        'attrs',
    )
    
    def __init__(self):
        # Core identity
        self.ref = b''
        self.protocols = []
        self.token_type = GlyphTokenType.UNKNOWN
        self.glyph_version = 1  # 1 for v1, 2 for v2
        self.name = None
        self.ticker = None
        self.decimals = 0
        self.description = None
        self.author = None
        self.license = None
        # Deployment info
        self.deploy_height = 0
        self.deploy_txid = b''
        self.metadata_hash = b''
        self.is_spent = False
        # Supply tracking
        self.total_supply = 0
        self.current_supply = 0
        self.premine = 0
        self.mined_supply = 0
        # Image/content
        self.icon_ref = None  # Reference to icon (txid_vout or embedded)
        self.icon_type = None  # MIME type (image/png, image/svg+xml, etc.)
        self.icon_size = 0
        self.embedded_data_hash = None  # Hash of embedded data (for DAT tokens)
        # dMint specific
        self.contract_ref = None  # Mining contract reference
        self.algorithm = 0  # Mining algorithm ID (0x01=SHA256D, etc.)
        self.start_difficulty = 0
        self.current_difficulty = 0
        self.reward = 0  # Current block reward
        self.halving_interval = 0  # Blocks between halvings
        self.daa_mode = 0  # Difficulty adjustment algorithm
        self.mint_count = 0  # Number of mint transactions
        # Relationships
        self.container_ref = None  # Parent container (if contained)
        self.authority_ref = None  # Authority token reference
        self.parent_ref = None  # Parent token for child tokens
        # NFT specific
        self.attrs = None  # Serialized attributes JSON
    
    def to_bytes(self) -> bytes:
        """Serialize token info to CBOR bytes for flexible storage."""
        if not HAS_CBOR:
            raise RuntimeError('cbor2 required for Glyph indexing')
        
        data = {
            # Core identity
            'ref': self.ref,
            'p': self.protocols,
            'tt': self.token_type,
            'gv': self.glyph_version,
            'n': self.name,
            'tk': self.ticker,
            'dc': self.decimals,
            'ds': self.description,
            'au': self.author,
            'li': self.license,
            # Deployment
            'dh': self.deploy_height,
            'dt': self.deploy_txid,
            'mh': self.metadata_hash,
            'sp': self.is_spent,
            # Supply
            'ts': self.total_supply,
            'cs': self.current_supply,
            'pm': self.premine,
            'ms': self.mined_supply,
            # Image/content
            'ir': self.icon_ref,
            'it': self.icon_type,
            'is': self.icon_size,
            'ed': self.embedded_data_hash,
            # dMint
            'cr': self.contract_ref,
            'al': self.algorithm,
            'sd': self.start_difficulty,
            'cd': self.current_difficulty,
            'rw': self.reward,
            'hi': self.halving_interval,
            'da': self.daa_mode,
            'mc': self.mint_count,
            # Relationships
            'co': self.container_ref,
            'ar': self.authority_ref,
            'pr': self.parent_ref,
            # NFT
            'at': self.attrs,
        }
        # Remove None values to save space
        data = {k: v for k, v in data.items() if v is not None and v != 0 and v != b''}
        return cbor2.dumps(data)
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'GlyphTokenInfo':
        """Deserialize token info from CBOR bytes."""
        if not HAS_CBOR:
            raise RuntimeError('cbor2 required for Glyph indexing')
        
        info = cls()
        d = cbor2.loads(data)
        
        # Core identity
        info.ref = d.get('ref', b'')
        info.protocols = d.get('p', [])
        info.token_type = d.get('tt', GlyphTokenType.UNKNOWN)
        info.glyph_version = d.get('gv', 1)
        info.name = d.get('n')
        info.ticker = d.get('tk')
        info.decimals = d.get('dc', 0)
        info.description = d.get('ds')
        info.author = d.get('au')
        info.license = d.get('li')
        # Deployment
        info.deploy_height = d.get('dh', 0)
        info.deploy_txid = d.get('dt', b'')
        info.metadata_hash = d.get('mh', b'')
        info.is_spent = d.get('sp', False)
        # Supply
        info.total_supply = d.get('ts', 0)
        info.current_supply = d.get('cs', 0)
        info.premine = d.get('pm', 0)
        info.mined_supply = d.get('ms', 0)
        # Image/content
        info.icon_ref = d.get('ir')
        info.icon_type = d.get('it')
        info.icon_size = d.get('is', 0)
        info.embedded_data_hash = d.get('ed')
        # dMint
        info.contract_ref = d.get('cr')
        info.algorithm = d.get('al', 0)
        info.start_difficulty = d.get('sd', 0)
        info.current_difficulty = d.get('cd', 0)
        info.reward = d.get('rw', 0)
        info.halving_interval = d.get('hi', 0)
        info.daa_mode = d.get('da', 0)
        info.mint_count = d.get('mc', 0)
        # Relationships
        info.container_ref = d.get('co')
        info.authority_ref = d.get('ar')
        info.parent_ref = d.get('pr')
        # NFT
        info.attrs = d.get('at')
        
        return info
    
    def percent_mined(self) -> float:
        """Calculate percentage of total supply that has been mined."""
        if self.total_supply == 0:
            return 0.0
        return (self.mined_supply / self.total_supply) * 100.0


class GlyphIndex:
    """
    Glyph token index manager.
    
    Handles indexing of Glyph tokens during block processing and
    provides query methods for the API.
    """
    
    def __init__(self, db, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        self.enabled = getattr(env, 'glyph_index', True)
        
        # In-memory caches for unflushed data
        self.token_cache: Dict[bytes, GlyphTokenInfo] = {}
        self.balance_cache: Dict[bytes, int] = {}  # key -> amount
        self.balance_height: Dict[bytes, int] = {}
        self.history_cache: List[Tuple[int, bytes, bytes]] = []  # (height, key, value)
        self.metadata_cache: Dict[bytes, bytes] = {}  # hash -> cbor
        self.metadata_height: Dict[bytes, int] = {}
        self.token_height: Dict[bytes, int] = {}

        # Persistent set of refs known to exist as tokens (survives flush cycles).
        # Prevents redundant DB lookups for refs already confirmed as known.
        self._known_refs: Set[bytes] = set()

        # Per-height undo information for reorg safety.
        # We store the previous value of each key (or None if absent) the first time
        # it is touched within a given height.
        self._undo_cache: Dict[int, List[Tuple[bytes, Optional[bytes]]]] = defaultdict(list)
        self._undo_seen: Dict[int, Set[bytes]] = defaultdict(set)

        # Undo retention: keep at most env.reorg_limit heights of undo data.
        # We do not try to retroactively delete historical keys on startup; we
        # only enforce the bound moving forward.
        current_height = getattr(db, 'db_height', -1)
        reorg_limit = getattr(env, 'reorg_limit', 0)
        min_keep = max(0, current_height - reorg_limit + 1) if reorg_limit else 0
        self._last_undo_pruned = min_keep - 1
        
        if self.enabled:
            self.logger.info('Glyph token indexing enabled')
    
    def process_tx(self, tx_hash: bytes, tx: 'Tx', height: int, tx_idx: int,
                    output_refs_by_vout: Dict[int, List[Tuple[bytes, int]]] = None):
        """
        Process a transaction for Glyph tokens.
        
        Called by BlockProcessor for each transaction.
        
        Token detection uses TWO complementary approaches:
        
        1. OUTPUT SCRIPT PATTERNS (primary): Detect FT/NFT token UTXOs by
           their ref opcodes (OP_PUSHINPUTREF 0xd0 for FT, 
           OP_PUSHINPUTREFSINGLETON 0xd8 for NFT). This catches ALL token
           activity including transfers (thousands per block).
        
        2. INPUT SCRIPTSIG 'gly' MAGIC (secondary): Detect rare reveal
           transactions that contain CBOR metadata. Only ~5 total on the
           entire mainnet chain, but needed for token metadata extraction.
        
        Args:
            tx_hash: Transaction hash
            tx: Transaction object
            height: Block height
            tx_idx: Transaction index in block
            output_refs_by_vout: Pre-parsed ref data from block processor.
                Dict mapping vout -> list of (ref_bytes, ref_type) where
                ref_type is 0 for normal (FT) and 1 for singleton (NFT).
        
        Returns:
            dict or None: The parsed Glyph envelope if found, for chaining to
                         WAVE/Swap indexers. Returns None if not a Glyph tx.
        """
        if not self.enabled:
            return None
        
        result_envelope = None
        is_token_tx = False
        
        # ===================================================================
        # PHASE 1: Detect token UTXOs by OUTPUT SCRIPT PATTERNS
        # This is the PRIMARY detection method — catches all token activity.
        # ===================================================================
        if output_refs_by_vout:
            for vout, ref_list in output_refs_by_vout.items():
                output = tx.outputs[vout]
                script = output.pk_script
                for ref_bytes, ref_type in ref_list:
                    is_token_tx = True
                    # Check if this token ref is already known
                    known = self._is_known_token(ref_bytes)
                    if not known:
                        # New token ref discovered — register it
                        token = GlyphTokenInfo()
                        token.ref = ref_bytes
                        if ref_type == 1:  # singleton = NFT
                            token.token_type = GlyphTokenType.NFT
                            token.protocols = [GlyphProtocol.GLYPH_NFT]
                        else:  # normal = FT
                            token.token_type = GlyphTokenType.FT
                            token.protocols = [GlyphProtocol.GLYPH_FT]
                        token.deploy_height = height
                        token.deploy_txid = tx_hash
                        self.token_cache[ref_bytes] = token
                        self.token_height[ref_bytes] = height
                        self._known_refs.add(ref_bytes)
                        
                        # Add deploy event
                        history_key = pack_history_key(ref_bytes, height, tx_idx)
                        history_value = struct.pack('<B', GlyphEventType.DEPLOY) + tx_hash
                        self.history_cache.append((height, history_key, history_value))
        else:
            # Fallback: parse output scripts directly if block processor
            # didn't provide pre-parsed ref data
            for vout, output in enumerate(tx.outputs):
                script = output.pk_script
                if len(script) < 38:
                    continue
                refs_found = self._extract_refs_from_script(script)
                for ref_bytes, ref_type in refs_found:
                    is_token_tx = True
                    known = self._is_known_token(ref_bytes)
                    if not known:
                        token = GlyphTokenInfo()
                        token.ref = ref_bytes
                        if ref_type == 1:
                            token.token_type = GlyphTokenType.NFT
                            token.protocols = [GlyphProtocol.GLYPH_NFT]
                        else:
                            token.token_type = GlyphTokenType.FT
                            token.protocols = [GlyphProtocol.GLYPH_FT]
                        token.deploy_height = height
                        token.deploy_txid = tx_hash
                        self.token_cache[ref_bytes] = token
                        self.token_height[ref_bytes] = height
                        self._known_refs.add(ref_bytes)
                        
                        history_key = pack_history_key(ref_bytes, height, tx_idx)
                        history_value = struct.pack('<B', GlyphEventType.DEPLOY) + tx_hash
                        self.history_cache.append((height, history_key, history_value))
        
        # ===================================================================
        # PHASE 2: Check INPUTS for 'gly' magic (reveal tx metadata)
        # This is RARE (~5 on entire mainnet) but provides CBOR metadata.
        # When found, update the token record with full metadata.
        # ===================================================================
        for vin_idx, txin in enumerate(tx.inputs):
            script = txin.script
            
            if not script:
                continue
            
            if not contains_glyph_magic(script):
                continue
            
            envelope = parse_glyph_envelope(script)
            if not envelope:
                continue
            
            if not envelope.get('is_reveal'):
                continue
            
            metadata = parse_glyph_metadata(envelope)
            if not metadata:
                continue
            if not isinstance(metadata, dict):
                self.logger.warning(
                    f'Invalid Glyph metadata type: tx={hash_to_hex_str(tx_hash)} input={vin_idx} type={type(metadata).__name__}'
                )
                continue
            
            protocols = metadata.get('p', [])
            token_type = get_token_type(protocols)
            self.logger.info(f'Glyph REVEAL: tx={hash_to_hex_str(tx_hash)} input={vin_idx} type={token_type} protocols={protocols}')
            
            if result_envelope is None:
                result_envelope = envelope.copy()
                result_envelope['metadata'] = metadata
                result_envelope['protocols'] = metadata.get('p', [])
                result_envelope['tx_hash'] = tx_hash
                result_envelope['vin_idx'] = vin_idx
            
            # Find the ref for this reveal
            prev_hash = txin.prev_hash
            prev_idx = txin.prev_idx
            ref = pack_ref(prev_hash, prev_idx)
            
            output_ref = self._find_output_ref(tx_hash, tx, metadata)
            final_ref = output_ref if output_ref else ref
            
            # Index the reveal with full metadata
            self._index_token_reveal(
                final_ref, tx_hash,
                vin_idx if not output_ref else output_ref[32:36],
                height, tx_idx, envelope, metadata, tx
            )
        
        return result_envelope
    
    def _is_known_token(self, ref: bytes) -> bool:
        """Check if a token ref is already known (in cache, known set, or DB)."""
        if ref in self._known_refs:
            return True
        if ref in self.token_cache:
            self._known_refs.add(ref)
            return True
        key = pack_token_key(ref)
        if self.db.utxo_db.get(key) is not None:
            self._known_refs.add(ref)
            return True
        return False
    
    @staticmethod
    def _extract_refs_from_script(script: bytes) -> List[Tuple[bytes, int]]:
        """
        Extract token refs from an output script.
        
        Detects:
        - OP_PUSHINPUTREFSINGLETON (0xd8) + 36 bytes = NFT (ref_type=1)
        - OP_PUSHINPUTREF (0xd0) + 36 bytes = FT (ref_type=0)
        
        Returns list of (ref_bytes, ref_type) tuples.
        """
        results = []
        n = 0
        while n < len(script):
            op = script[n]
            n += 1
            if op == 0xd8 and n + 36 <= len(script):  # OP_PUSHINPUTREFSINGLETON
                results.append((script[n:n+36], 1))
                n += 36
            elif op == 0xd0 and n + 36 <= len(script):  # OP_PUSHINPUTREF
                results.append((script[n:n+36], 0))
                n += 36
            elif op <= 0x4e:  # Data push opcodes — skip data
                if op < 0x4c:  # OP_PUSHBYTES_N
                    n += op
                elif op == 0x4c:  # OP_PUSHDATA1
                    if n < len(script):
                        n += 1 + script[n]
                elif op == 0x4d:  # OP_PUSHDATA2
                    if n + 1 < len(script):
                        dlen = script[n] | (script[n+1] << 8)
                        n += 2 + dlen
                elif op == 0x4e:  # OP_PUSHDATA4
                    if n + 3 < len(script):
                        dlen = script[n] | (script[n+1] << 8) | (script[n+2] << 16) | (script[n+3] << 24)
                        n += 4 + dlen
            elif op in (0xd1, 0xd2, 0xd3):  # Other ref ops with 36-byte data
                n += 36
        return results
    
    def _find_output_ref(self, tx_hash: bytes, tx, metadata: Dict) -> Optional[bytes]:
        """
        Find the token ref in the reveal transaction's outputs.
        
        The token's identity is the 36-byte ref embedded after OP_PUSHINPUTREFSINGLETON
        (0xd8) for NFTs or OP_PUSHINPUTREF (0xd0) for FTs in the output script.
        
        Returns the 36-byte ref or None if not found.
        """
        protocols = metadata.get('p', [])
        
        # For NFTs, look for singleton pattern (d8 + 36 byte ref)
        if GlyphProtocol.GLYPH_NFT in protocols:
            for vout, output in enumerate(tx.outputs):
                script = output.pk_script
                if len(script) >= 37 and script[0] == 0xd8:
                    return script[1:37]
                # Also check if d8 appears further into the script
                idx = script.find(b'\xd8')
                while idx >= 0 and idx + 37 <= len(script):
                    return script[idx+1:idx+37]
        
        # For FTs, look for normal ref pattern (d0 + 36 byte ref)
        if GlyphProtocol.GLYPH_FT in protocols:
            for vout, output in enumerate(tx.outputs):
                script = output.pk_script
                idx = script.find(b'\xd0')
                while idx >= 0 and idx + 37 <= len(script):
                    return script[idx+1:idx+37]
        
        return None
    
    def _index_token_reveal(self, ref: bytes, tx_hash: bytes, vout_or_vin,
                            height: int, tx_idx: int, envelope: Dict,
                            metadata: Dict, tx: 'Tx'):
        """
        Index a token reveal transaction.
        
        Args:
            ref: The token reference (txid + vout)
            tx_hash: Transaction hash
            vout_or_vin: Output or input index
            height: Block height
            tx_idx: Transaction index in block
            envelope: Parsed Glyph envelope
            metadata: Parsed CBOR metadata (already decoded)
            tx: The full transaction object
        """
        if not metadata:
            return
        
        # Extract token info
        token_info = extract_token_info(metadata)
        
        # Create token record
        token = GlyphTokenInfo()
        token.ref = ref
        token.protocols = token_info['protocols']
        token.token_type = get_token_type_id(token_info['protocols'])
        token.glyph_version = token_info.get('version', 1)
        token.name = token_info.get('name')
        token.ticker = token_info.get('ticker')
        token.decimals = token_info.get('decimals', 0)
        token.deploy_height = height
        token.deploy_txid = tx_hash
        token.is_spent = False
        
        # Store metadata
        metadata_bytes = envelope.get('metadata_bytes', b'')
        if metadata_bytes:
            token.metadata_hash = sha256(metadata_bytes)
            self.metadata_cache[token.metadata_hash] = metadata_bytes
            self.metadata_height[token.metadata_hash] = height
        
        # For FT, track supply
        if GlyphProtocol.GLYPH_FT in token.protocols:
            # Initial supply from metadata or 0 for dMint
            if GlyphProtocol.GLYPH_DMINT in token.protocols:
                token.total_supply = token_info.get('dmint', {}).get('max_supply', 0)
                token.current_supply = 0
            else:
                # For non-dMint FTs, get initial supply from output value
                # Find the FT output to get its value
                initial_supply = 0
                for out in tx.outputs:
                    if b'\xd0' in out.pk_script:  # OP_PUSHINPUTREF
                        initial_supply = out.value
                        break
                token.total_supply = initial_supply
                token.current_supply = initial_supply
        
        # Extract additional metadata fields
        token.description = token_info.get('description')
        if 'attrs' in token_info and token_info['attrs']:
            token.attrs = token_info['attrs']
        
        # Store in cache
        self.token_cache[ref] = token
        self.token_height[ref] = height
        
        # Add deploy event to history
        history_key = pack_history_key(ref, height, tx_idx)
        history_value = struct.pack('<B', GlyphEventType.DEPLOY) + tx_hash
        self.history_cache.append((height, history_key, history_value))
        
        # Log the indexed token
        ref_txid, ref_vout = unpack_ref(ref)
        self.logger.info(f'Indexed Glyph token: {hash_to_hex_str(ref_txid)}:{ref_vout} '
                         f'type={token.token_type} name={token.name} protocols={token.protocols}')
    
    def _track_commit(self, ref: bytes, envelope: Dict):
        """Track a commit transaction for later reveal matching."""
        # For now, we don't need to store commits separately
        # The reveal will be self-contained
        pass
    
    def update_balance(self, height: int, scripthash: bytes, ref: bytes, delta: int):
        """Update a token balance."""
        if not self.enabled:
            return

        key = pack_balance_key(scripthash, ref)
        holder_key = pack_holder_key(ref, scripthash)
        self._record_undo(height, key)
        self._record_undo(height, holder_key)
        current = self.balance_cache.get(key, 0)
        new_balance = max(0, current + delta)

        if new_balance > 0:
            self.balance_cache[key] = new_balance
            self.balance_height[key] = height
        elif key in self.balance_cache:
            del self.balance_cache[key]
            self.balance_height.pop(key, None)
    
    def _undo_key(self, height: int) -> bytes:
        return GlyphDBKeys.UNDO + pack_be_uint32(height)
    
    def _record_undo(self, height: int, key: bytes):
        """Record undo information for a key."""
        if not self.enabled:
            return
        if key in self._undo_seen[height]:
            return
        self._undo_seen[height].add(key)
        prev_value = self.db.utxo_db.get(key)
        self._undo_cache[height].append((key, prev_value))
    
    def backup(self, batch, height: int):
        """Revert DB keys written at the given height (reorg unwind)."""
        if not self.enabled:
            return
        # Clear known refs cache on reorg — it will repopulate from DB lookups
        self._known_refs.clear()
        raw = self.db.utxo_db.get(self._undo_key(height))
        if not raw:
            return
        entries = ast.literal_eval(raw.decode())

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

        # Keep undo info for heights in [db_height - reorg_limit + 1, db_height].
        min_keep = max(0, self.db.db_height - reorg_limit + 1)
        prune_to = min_keep - 1
        if prune_to <= self._last_undo_pruned:
            return

        for height in range(self._last_undo_pruned + 1, prune_to + 1):
            batch.delete(self._undo_key(height))
        self._last_undo_pruned = prune_to
    
    def flush(self, batch):
        """Flush cached Glyph data to the database."""
        if not self.enabled:
            return
        # Important: record undo entries for keys touched during this flush
        # first, then persist undo records at the end.

        self._prune_old_undo_keys(batch)
        
        # Flush tokens
        for ref, token in self.token_cache.items():
            height = self.token_height.get(ref)
            if height is None:
                continue
            key = pack_token_key(ref)
            self._record_undo(height, key)
            batch.put(key, token.to_bytes())
            
            # Also index by type
            type_key = GlyphDBKeys.BY_TYPE + struct.pack('<B', token.token_type) + ref
            self._record_undo(height, type_key)
            batch.put(type_key, b'')
            
            # Index by name (if present)
            if token.name:
                name_hash = sha256(token.name.lower().encode('utf-8'))[:16]
                name_key = GlyphDBKeys.BY_NAME + name_hash + ref
                self._record_undo(height, name_key)
                batch.put(name_key, b'')
            
            # Index by ticker (if FT)
            if token.ticker and GlyphProtocol.GLYPH_FT in token.protocols:
                ticker_key = GlyphDBKeys.BY_TICKER + token.ticker.upper().encode('utf-8')[:8]
                self._record_undo(height, ticker_key)
                batch.put(ticker_key, ref)
        
        # Flush balances (primary + secondary index)
        for key, amount in self.balance_cache.items():
            height = self.balance_height.get(key)
            if height is None:
                continue
            packed = struct.pack('<Q', amount)
            batch.put(key, packed)
            # Write secondary holder-by-ref index:
            # key = GB + scripthash(32) + ref(36) → extract parts
            scripthash = key[2:2+32]
            ref = key[2+32:2+32+36]
            holder_key = pack_holder_key(ref, scripthash)
            batch.put(holder_key, packed)
        
        # Flush history
        for height, key, value in self.history_cache:
            self._record_undo(height, key)
            batch.put(key, value)
        
        # Flush metadata
        for hash_bytes, cbor_data in self.metadata_cache.items():
            key = GlyphDBKeys.METADATA + hash_bytes
            height = self.metadata_height.get(hash_bytes)
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, cbor_data)

        # Persist undo information last so it includes keys written above.
        for height, entries in sorted(self._undo_cache.items()):
            batch.put(self._undo_key(height), repr(entries).encode())
        self._undo_cache.clear()
        self._undo_seen.clear()
        
        # Clear caches
        self.token_cache.clear()
        self.balance_cache.clear()
        self.balance_height.clear()
        self.history_cache.clear()
        self.metadata_cache.clear()
        self.metadata_height.clear()
        self.token_height.clear()
    
    # ========================================================================
    # Query Methods (used by API)
    # ========================================================================
    
    def get_token(self, ref: bytes) -> Optional[GlyphTokenInfo]:
        """Get token info by ref."""
        # Check cache first
        if ref in self.token_cache:
            return self.token_cache[ref]
        
        # Query database
        key = pack_token_key(ref)
        data = self.db.utxo_db.get(key)
        if data:
            return GlyphTokenInfo.from_bytes(data)
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about indexed Glyph tokens.
        
        Returns:
            Dict with token counts by type and other stats
        """
        stats = {
            'enabled': self.enabled,
            'total_tokens': 0,
            'by_type': {
                'FT': 0,       # Fungible tokens
                'NFT': 0,      # Non-fungible tokens
                'DAT': 0,      # Data tokens
                'dMint': 0,    # Distributed mint tokens
                'unknown': 0,
            },
            'by_version': {
                'v1': 0,
                'v2': 0,
            },
            'cache_size': len(self.token_cache),
        }
        
        if not self.enabled:
            return stats
        
        # Iterate over all tokens in database
        prefix = GlyphDBKeys.TOKEN
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            stats['total_tokens'] += 1
            
            try:
                token = GlyphTokenInfo.from_bytes(value)
                
                # Count by version
                if token.glyph_version == 2:
                    stats['by_version']['v2'] += 1
                else:
                    stats['by_version']['v1'] += 1
                
                # Count by type
                token_type = token.token_type
                if token_type == GlyphTokenType.FT:
                    stats['by_type']['FT'] += 1
                elif token_type == GlyphTokenType.NFT:
                    stats['by_type']['NFT'] += 1
                elif token_type == GlyphTokenType.DAT:
                    stats['by_type']['DAT'] += 1
                elif token_type == GlyphTokenType.DMINT:
                    stats['by_type']['dMint'] += 1
                else:
                    stats['by_type']['unknown'] += 1
            except Exception:
                stats['by_type']['unknown'] += 1
        
        return stats
    
    def get_token_by_ref_str(self, ref_str: str) -> Optional[Dict[str, Any]]:
        """Get token info by ref string (txid_vout)."""
        try:
            txid, vout = parse_ref(ref_str)
            ref = pack_ref(hex_str_to_hash(txid), vout)
            token = self.get_token(ref)
            if token:
                return self._token_to_dict(token)
        except Exception:
            pass
        return None
    
    def get_balance(self, scripthash: bytes, ref: bytes) -> int:
        """Get token balance for a scripthash."""
        key = pack_balance_key(scripthash, ref)
        
        # Check cache
        if key in self.balance_cache:
            return self.balance_cache[key]
        
        # Query database
        data = self.db.utxo_db.get(key)
        if data:
            return struct.unpack('<Q', data)[0]
        return 0
    
    def get_balances_for_scripthash(self, scripthash: bytes, 
                                     limit: int = 100) -> List[Dict]:
        """Get all token balances for a scripthash."""
        results = []
        prefix = GlyphDBKeys.BALANCE + scripthash
        
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            if len(results) >= limit:
                break
            
            ref = key[len(prefix):]
            amount = struct.unpack('<Q', value)[0]
            
            # Get token info
            token = self.get_token(ref)
            if token:
                results.append({
                    'ref': hash_to_hex_str(ref[:32]) + '_' + str(struct.unpack('<I', ref[32:36])[0]),
                    'amount': amount,
                    'name': token.name,
                    'ticker': token.ticker,
                    'decimals': token.decimals,
                    'type': token.token_type,
                })
        
        return results
    
    def get_token_history(self, ref: bytes, limit: int = 100, 
                          offset: int = 0) -> List[Dict]:
        """Get transaction history for a token."""
        results = []
        prefix = GlyphDBKeys.HISTORY + ref
        count = 0
        
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            if count < offset:
                count += 1
                continue
            if len(results) >= limit:
                break
            
            # Unpack height and tx_idx from key
            height = struct.unpack('>I', key[len(prefix):len(prefix)+4])[0]
            tx_idx = struct.unpack('>H', key[len(prefix)+4:len(prefix)+6])[0]
            
            # Unpack event type and txid from value
            event_type = value[0]
            txid = value[1:33]
            
            results.append({
                'height': height,
                'tx_idx': tx_idx,
                'txid': hash_to_hex_str(txid),
                'event': self._event_type_name(event_type),
            })
            count += 1
        
        return results
    
    def search_tokens(self, query: str, protocols: List[int] = None,
                      limit: int = 50) -> List[Dict]:
        """Search tokens by name or ticker."""
        results = []
        query_lower = query.lower()
        
        # Search by name hash
        name_hash = sha256(query_lower.encode('utf-8'))[:16]
        prefix = GlyphDBKeys.BY_NAME + name_hash
        
        for key, _ in self.db.utxo_db.iterator(prefix=prefix):
            if len(results) >= limit:
                break
            
            ref = key[len(prefix):]
            token = self.get_token(ref)
            
            if token:
                # Filter by protocols if specified
                if protocols and not any(p in token.protocols for p in protocols):
                    continue
                results.append(self._token_to_dict(token))
        
        return results
    
    def get_tokens_by_type(self, token_type: int, limit: int = 100,
                           offset: int = 0) -> List[Dict]:
        """Get tokens by type."""
        results = []
        prefix = GlyphDBKeys.BY_TYPE + struct.pack('<B', token_type)
        count = 0
        
        for key, _ in self.db.utxo_db.iterator(prefix=prefix):
            if count < offset:
                count += 1
                continue
            if len(results) >= limit:
                break
            
            ref = key[len(prefix):]
            token = self.get_token(ref)
            if token:
                results.append(self._token_to_dict(token))
            count += 1
        
        return results
    
    def get_metadata(self, metadata_hash: bytes) -> Optional[Dict]:
        """Get parsed metadata by hash."""
        # Check cache
        if metadata_hash in self.metadata_cache:
            cbor_data = self.metadata_cache[metadata_hash]
        else:
            key = GlyphDBKeys.METADATA + metadata_hash
            cbor_data = self.db.utxo_db.get(key)
        
        if cbor_data and HAS_CBOR:
            try:
                return cbor2.loads(cbor_data)
            except Exception:
                pass
        return None
    
    def _token_to_dict(self, token: GlyphTokenInfo, include_dmint: bool = True,
                        include_content: bool = True) -> Dict[str, Any]:
        """
        Convert token info to API dict.
        
        Returns all fields needed by explorers, wallets, and exchanges.
        """
        txid, vout = unpack_ref(token.ref)
        
        result = {
            # Core identity
            'ref': hash_to_hex_str(txid) + '_' + str(vout),
            'protocols': token.protocols,
            'type': token.token_type,
            'type_name': self._type_name(token.token_type),
            'name': token.name,
            'ticker': token.ticker,
            'decimals': token.decimals,
            'description': token.description,
            'author': token.author,
            'license': token.license,
            # Deployment
            'deploy_height': token.deploy_height,
            'deploy_txid': hash_to_hex_str(token.deploy_txid) if token.deploy_txid else None,
            'metadata_hash': hash_to_hex_str(token.metadata_hash) if token.metadata_hash else None,
            'is_spent': token.is_spent,
            # Supply tracking
            'total_supply': token.total_supply,
            'current_supply': token.current_supply,
            'premine': token.premine,
            'mined_supply': token.mined_supply,
            'percent_mined': token.percent_mined() if token.total_supply > 0 else None,
            # Relationships
            'container_ref': token.container_ref,
            'authority_ref': token.authority_ref,
            'parent_ref': token.parent_ref,
        }
        
        # Include image/content info
        if include_content:
            result.update({
                'icon_ref': token.icon_ref,
                'icon_type': token.icon_type,
                'icon_size': token.icon_size,
                'embedded_data_hash': hash_to_hex_str(token.embedded_data_hash) if token.embedded_data_hash else None,
            })
        
        # Include dMint-specific fields for minable tokens
        if include_dmint and GlyphProtocol.GLYPH_DMINT in token.protocols:
            result['dmint'] = {
                'contract_ref': token.contract_ref,
                'algorithm': token.algorithm,
                'algorithm_name': self._algorithm_name(token.algorithm),
                'start_difficulty': token.start_difficulty,
                'current_difficulty': token.current_difficulty,
                'reward': token.reward,
                'halving_interval': token.halving_interval,
                'daa_mode': token.daa_mode,
                'daa_mode_name': self._daa_mode_name(token.daa_mode),
                'mint_count': token.mint_count,
            }
        
        # Include NFT attributes
        if token.attrs:
            result['attrs'] = token.attrs
        
        return result
    
    @staticmethod
    def _algorithm_name(algo_id: int) -> str:
        """Get mining algorithm name from ID (per Glyph v2 spec Section 11.2)."""
        algos = {
            0x00: 'SHA256D',
            0x01: 'Blake3',
            0x02: 'KangarooTwelve',
            0x03: 'Argon2id-Light',
            0x04: 'RandomX-Light',
        }
        return algos.get(algo_id, f'Unknown ({algo_id})')
    
    @staticmethod
    def _daa_mode_name(daa_mode: int) -> str:
        """Get DAA mode name from ID."""
        modes = {
            0x00: 'Fixed',
            0x01: 'Epoch',
            0x02: 'ASERT',
            0x03: 'LWMA',
            0x04: 'Schedule',
        }
        return modes.get(daa_mode, f'Unknown ({daa_mode})')
    
    @staticmethod
    def _type_name(token_type: int) -> str:
        """Get type name from type ID."""
        names = {
            GlyphTokenType.UNKNOWN: 'Unknown',
            GlyphTokenType.FT: 'Fungible Token',
            GlyphTokenType.NFT: 'NFT',
            GlyphTokenType.DAT: 'Data',
            GlyphTokenType.DMINT: 'dMint Token',
            GlyphTokenType.WAVE: 'WAVE Name',
            GlyphTokenType.CONTAINER: 'Container',
            GlyphTokenType.AUTHORITY: 'Authority',
        }
        return names.get(token_type, 'Unknown')
    
    @staticmethod
    def _event_type_name(event_type: int) -> str:
        """Get event type name."""
        names = {
            GlyphEventType.DEPLOY: 'deploy',
            GlyphEventType.MINT: 'mint',
            GlyphEventType.TRANSFER: 'transfer',
            GlyphEventType.BURN: 'burn',
            GlyphEventType.UPDATE: 'update',
        }
        return names.get(event_type, 'unknown')
    
    # =========================================================================
    # TOKEN ANALYTICS API
    # =========================================================================
    
    def get_token_holders(self, ref: bytes, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """
        Get token holders for a specific token.
        
        Uses the HOLDER_BY_REF secondary index for efficient lookup:
        GR + ref + scripthash -> amount
        """
        holders = []
        total_holders = 0
        prefix = GlyphDBKeys.HOLDER_BY_REF + ref
        
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            balance = struct.unpack('<Q', value)[0] if len(value) == 8 else 0
            if balance <= 0:
                continue
            
            total_holders += 1
            if total_holders > offset and len(holders) < limit:
                scripthash = key[len(prefix):len(prefix)+32]
                holders.append({
                    'scripthash': scripthash.hex(),
                    'balance': balance,
                })
            elif len(holders) >= limit and total_holders > offset + limit:
                # We have enough results; keep counting total but stop collecting
                pass
        
        return {
            'ref': ref.hex(),
            'total_holders': total_holders,
            'holders': holders,
            'limit': limit,
            'offset': offset,
        }
    
    def get_token_supply(self, ref: bytes) -> Optional[Dict[str, Any]]:
        """
        Get detailed supply information for a token.
        
        Uses HOLDER_BY_REF index for efficient supply calculation.
        """
        token = self.get_token(ref)
        if not token:
            return None
        
        # Calculate holder-derived circulating supply via secondary index
        circulating = 0
        holder_count = 0
        
        prefix = GlyphDBKeys.HOLDER_BY_REF + ref
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            balance = struct.unpack('<Q', value)[0] if len(value) == 8 else 0
            if balance > 0:
                circulating += balance
                holder_count += 1
        
        # Get burn history count
        burn_count = 0
        history_prefix = GlyphDBKeys.HISTORY + ref
        for key, value in self.db.utxo_db.iterator(prefix=history_prefix):
            if len(value) >= 1 and value[0] == GlyphEventType.BURN:
                burn_count += 1
        
        return {
            'ref': ref.hex(),
            'name': token.name,
            'ticker': token.ticker,
            'decimals': token.decimals,
            'total_supply': token.total_supply,
            'circulating_supply': circulating,
            'current_supply': token.current_supply,
            'premine': token.premine,
            'mined_supply': token.mined_supply,
            'burned_count': burn_count,
            'holder_count': holder_count,
            'is_dmint': GlyphProtocol.GLYPH_DMINT in token.protocols,
        }
    
    def get_token_burns(self, ref: bytes, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """
        Get burn history for a token.
        
        Returns list of burn events with transaction details.
        """
        burns = []
        total_burns = 0
        
        history_prefix = GlyphDBKeys.HISTORY + ref
        for key, value in self.db.utxo_db.iterator(prefix=history_prefix):
            if len(value) < 1:
                continue
            event_type = value[0]
            if event_type != GlyphEventType.BURN:
                continue
            
            total_burns += 1
            if total_burns > offset and len(burns) < limit:
                # Extract height and tx_idx from key
                height = struct.unpack('>I', key[-6:-2])[0]
                tx_idx = struct.unpack('>H', key[-2:])[0]
                tx_hash = value[1:33] if len(value) >= 33 else b''
                
                burns.append({
                    'height': height,
                    'tx_idx': tx_idx,
                    'txid': hash_to_hex_str(tx_hash) if tx_hash else None,
                })
        
        return {
            'ref': ref.hex(),
            'total_burns': total_burns,
            'burns': burns,
            'limit': limit,
            'offset': offset,
        }
    
    def get_token_trades(self, ref: bytes, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """
        Get trade/transfer history for a token.
        
        Returns list of transfer events.
        """
        trades = []
        total_trades = 0
        
        history_prefix = GlyphDBKeys.HISTORY + ref
        for key, value in self.db.utxo_db.iterator(prefix=history_prefix):
            if len(value) < 1:
                continue
            event_type = value[0]
            if event_type != GlyphEventType.TRANSFER:
                continue
            
            total_trades += 1
            if total_trades > offset and len(trades) < limit:
                height = struct.unpack('>I', key[-6:-2])[0]
                tx_idx = struct.unpack('>H', key[-2:])[0]
                tx_hash = value[1:33] if len(value) >= 33 else b''
                
                trades.append({
                    'height': height,
                    'tx_idx': tx_idx,
                    'txid': hash_to_hex_str(tx_hash) if tx_hash else None,
                    'event': 'transfer',
                })
        
        return {
            'ref': ref.hex(),
            'total_trades': total_trades,
            'trades': trades,
            'limit': limit,
            'offset': offset,
        }
    
    # =========================================================================
    # RICH LIST / TOP WALLETS
    # =========================================================================
    
    def get_top_holders(self, ref: bytes, limit: int = 100) -> Dict[str, Any]:
        """
        Get top token holders sorted by balance (descending).
        Uses HOLDER_BY_REF secondary index.
        """
        all_holders = []
        
        prefix = GlyphDBKeys.HOLDER_BY_REF + ref
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            balance = struct.unpack('<Q', value)[0] if len(value) == 8 else 0
            if balance > 0:
                scripthash = key[len(prefix):len(prefix)+32]
                all_holders.append({
                    'scripthash': scripthash.hex(),
                    'balance': balance,
                })
        
        # Sort by balance descending
        all_holders.sort(key=lambda x: x['balance'], reverse=True)
        
        # Get token info for context
        token = self.get_token(ref)
        total_supply = token.total_supply if token else 0
        
        # Add percentage for each holder
        top_holders = all_holders[:limit]
        for holder in top_holders:
            if total_supply > 0:
                holder['percentage'] = round(holder['balance'] / total_supply * 100, 4)
            else:
                holder['percentage'] = 0
        
        return {
            'ref': ref.hex(),
            'name': token.name if token else None,
            'ticker': token.ticker if token else None,
            'total_supply': total_supply,
            'holder_count': len(all_holders),
            'top_holders': top_holders,
        }
    
    def get_all_tokens_summary(self, limit: int = 100, offset: int = 0, 
                               token_type: int = None) -> Dict[str, Any]:
        """
        Get summary of all indexed tokens with pagination.
        
        Optionally filter by token type.
        """
        tokens = []
        total = 0
        
        prefix = GlyphDBKeys.TOKEN
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            try:
                token = GlyphTokenInfo.from_bytes(value)
                
                # Filter by type if specified
                if token_type is not None and token.token_type != token_type:
                    continue
                
                total += 1
                if total > offset and len(tokens) < limit:
                    tokens.append({
                        'ref': token.ref.hex(),
                        'name': token.name,
                        'ticker': token.ticker,
                        'type': self._type_name(token.token_type),
                        'type_id': token.token_type,
                        'glyph_version': token.glyph_version,
                        'total_supply': token.total_supply,
                        'current_supply': token.current_supply,
                        'deploy_height': token.deploy_height,
                        'is_spent': token.is_spent,
                    })
            except Exception:
                continue
        
        return {
            'total': total,
            'tokens': tokens,
            'limit': limit,
            'offset': offset,
            'filter_type': self._type_name(token_type) if token_type else None,
        }
