"""
Glyph Token Index for RXinDexer

This module provides database storage and indexing for Glyph v1/v2 tokens.
Handles token registration, balance tracking, and history.
"""

import base64
import struct
from typing import Optional, Dict, Any, List, Tuple, Set
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash, sha256, HASHX_LEN, Base58, Base58Error
from electrumx.lib.script import Script, ScriptError, OpCodes
from electrumx.lib.util import pack_be_uint32, encode_undo, decode_undo
from electrumx.lib.glyph import (
    GLYPH_MAGIC,
    GlyphProtocol,
    GlyphTokenType,
    parse_glyph_envelope,
    parse_glyph_metadata,
    extract_token_info,
    get_token_type_id,
    get_token_type,
    cbor_loads_capped,
    contains_glyph_magic,
    is_glyph_op_return,
    parse_glyph_from_output,
    format_ref,
    parse_ref,
    to_jsonsafe,
)

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False


# Database key prefixes for Glyph data
class GlyphDBKeys:
    """Database key prefixes for Glyph index."""
    TOKEN = b'GT'              # GT + ref -> token info
    METADATA = b'GM'           # GM + metadata_hash -> CBOR metadata
    BALANCE = b'GB'            # GB + scripthash + ref -> amount
    HISTORY = b'GH'            # GH + ref + height + tx_idx -> event
    BY_TYPE = b'GY'            # GY + type + ref -> (for type queries)
    BY_NAME = b'GN'            # GN + name_hash -> ref (for search)
    BY_TICKER = b'GK'          # GK + ticker -> ref (for FT lookup)
    SUPPLY = b'GS'             # GS + ref -> current supply (FT only)
    HOLDER_BY_REF = b'GR'      # GR + ref + hashX -> amount (reverse of BALANCE)
    OWNER = b'GO'              # GO + hashX -> base scriptPubKey (resolvable owner identity for holder rows)
    UNDO = b'GXU'              # GXU + height(be) -> binary undo entries
    KEY_REVEALS = b'GKR'       # GKR + ref -> CBOR key reveal record (Phase 6 / REP-3009)
    CONTRACT_TO_TOKEN = b'GC'  # GC + contract_ref(36) -> token_ref(36) (R6 reverse index)
    STATS = b'GSTAT'           # GSTAT -> CBOR {total, ft, nft, dat, dmint, v1, v2} (R11)
    SCHEMA_VERSION = b'GVER'   # GVER -> uint8 schema version (R21)
    # --- v4 discovery indexes (recency-ordered; see _migrate_3_to_4) ---
    # inv_height = 0xFFFFFFFF - deploy_height, so a *forward* prefix scan
    # yields newest-first without a reverse iterator (which has mishandled
    # `seek` here before — see reverse-cursor-storage fix). Prefixes are chosen
    # to avoid the GY* aliasing trap (a GY-prefixed key would collide with the
    # BY_TYPE prefix scan `GY + type_byte`; cf. realm_index "no prefix may be a
    # prefix of another").
    BY_TYPE_RECENT = b'GZ'     # GZ + type(1) + inv_height(4 be) + ref(36) -> b''
    BY_PROTO = b'GP'           # GP + proto(1) + inv_height(4 be) + ref(36) -> b''
    GLOBAL_RECENT = b'GQ'      # GQ + inv_height(4 be) + ref(36) -> type(1)


# v3: per-dMint-contract liveness (`live_contracts`) for correct burn detection.
#     Requires a full reindex to backfill the live-contract set for existing tokens.
# v4: recency-ordered discovery indexes (BY_TYPE_RECENT / BY_PROTO / GLOBAL_RECENT).
#     Backfillable in place from existing GT rows (deploy_height + protocols are
#     already stored) — no radiantd rescan; see _migrate_3_to_4.
CURRENT_SCHEMA_VERSION = 4


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


def ref_to_display(ref: bytes) -> str:
    """Render a 36-byte ref as the canonical display form ``txid_vout``.

    The txid is shown in big-endian (block-explorer / wallet) order and the
    vout as a decimal integer, e.g. ``b3d8…cf06_0``.  This is the canonical
    *output* ref format across the API; :func:`parse_ref_any` accepts it (and
    the raw 72-hex form) on input.
    """
    return hash_to_hex_str(ref[:32]) + '_' + str(struct.unpack('<I', ref[32:36])[0])


def parse_ref_any(ref_str: str) -> bytes:
    """Parse a ref in either supported form into the 36 raw key bytes.

    Accepts both:
      * 72-hex *internal* form (the raw RocksDB key bytes: internal-order txid +
        little-endian uint32 vout), and
      * display ``txid_vout`` form (big-endian txid + decimal vout).

    Returns the 36-byte ref. Raises ``ValueError`` on malformed input so REST
    handlers can map it to a 400/422.
    """
    s = ref_str.strip()
    sep = '_' if '_' in s else (':' if ':' in s else None)
    if sep is not None:
        txid_hex, vout_str = s.rsplit(sep, 1)
        if len(txid_hex) != 64:
            raise ValueError('txid_vout form requires a 64-hex txid')
        return hex_str_to_hash(txid_hex) + struct.pack('<I', int(vout_str))
    b = bytes.fromhex(s)
    if len(b) != 36:
        raise ValueError('ref must be 72 hex chars (36 bytes) or txid_vout form')
    return b


def parse_ref_candidates(ref_str: str) -> List[bytes]:
    """Parse a ref and return all byte-order candidates worth a DB lookup.

    Background — historical inconsistency: ``/dmint/contracts`` emits
    ``token_ref`` as **BE-display** 72-hex (txid in block-explorer order
    followed by an 8-hex LE vout), whereas every other 72-hex API
    (``/glyphs/{ref}``, ``/tokens/{ref}/*``, ``/dmint/contracts/{ref}/*``
    on input) expects the **internal-LE** 72-hex form. A naive client
    that chains ``/dmint/contracts → /tokens/{token_ref}/holders`` 404s
    silently.

    This helper preserves backward compatibility: callers iterate the
    returned candidates in order and use the first that hits the DB.

      * ``txid_vout`` form: unambiguous (BE txid + decimal vout) →
        one candidate.
      * 72-hex form: try internal-LE as-given **and** the BE-display
        fallback (first 32 bytes reversed) → up to two candidates.

    Returns at least one element. Raises ``ValueError`` on malformed input.
    """
    primary = parse_ref_any(ref_str)
    if '_' in ref_str or ':' in ref_str:
        return [primary]
    # 72-hex form — also try with the txid portion reversed, to accept the
    # legacy BE-display hex form that ``/dmint/contracts`` returns.
    reversed_txid = primary[:32][::-1] + primary[32:]
    if reversed_txid == primary:
        return [primary]
    return [primary, reversed_txid]


def pack_balance_key(scripthash: bytes, ref: bytes) -> bytes:
    """Pack a balance key."""
    return GlyphDBKeys.BALANCE + scripthash + ref


def pack_holder_key(ref: bytes, scripthash: bytes) -> bytes:
    """Pack a holder-by-ref key (secondary index for token holder lookups)."""
    return GlyphDBKeys.HOLDER_BY_REF + ref + scripthash


def pack_owner_key(hashX: bytes) -> bytes:
    """Pack an owner-resolution key (hashX -> base scriptPubKey).

    Lets the holder/balance indexes — which are keyed by the one-way 11-byte
    ``hashX`` — be resolved back to a displayable owner identity (full Electrum
    scripthash + base58 address).  The indexer saw the output's scriptPubKey at
    index time, so it persists it here rather than throwing it away; a one-way
    hash alone could never be reversed.
    """
    return GlyphDBKeys.OWNER + hashX


def pack_token_key(ref: bytes) -> bytes:
    """Pack a token key."""
    return GlyphDBKeys.TOKEN + ref


def pack_history_key(ref: bytes, height: int, tx_idx: int) -> bytes:
    """Pack a history key."""
    return (GlyphDBKeys.HISTORY + ref +
            struct.pack('>I', height) + struct.pack('>H', tx_idx))


# v4 discovery indexes ------------------------------------------------------
# All three encode the deploy height as its 32-bit complement so a forward
# RocksDB prefix scan (the cursor machinery here only seeks forward) returns
# newest-first. Height is well under 2**32, so the complement never underflows.
INV_HEIGHT_MAX = 0xFFFFFFFF


def _inv_height(deploy_height: int) -> bytes:
    """4-byte big-endian complement of a deploy height (newest sorts first)."""
    h = deploy_height if 0 <= deploy_height <= INV_HEIGHT_MAX else 0
    return pack_be_uint32(INV_HEIGHT_MAX - h)


def pack_type_recent_key(token_type: int, deploy_height: int, ref: bytes) -> bytes:
    """GZ + type(1) + inv_height(4) + ref — newest-first list within a type."""
    return (GlyphDBKeys.BY_TYPE_RECENT + struct.pack('<B', token_type & 0xFF)
            + _inv_height(deploy_height) + ref)


def pack_proto_key(proto: int, deploy_height: int, ref: bytes) -> bytes:
    """GP + proto(1) + inv_height(4) + ref — newest-first list per protocol."""
    return (GlyphDBKeys.BY_PROTO + struct.pack('<B', proto & 0xFF)
            + _inv_height(deploy_height) + ref)


def pack_global_recent_key(deploy_height: int, ref: bytes) -> bytes:
    """GQ + inv_height(4) + ref — newest-first list across every type."""
    return GlyphDBKeys.GLOBAL_RECENT + _inv_height(deploy_height) + ref


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
        'reward', 'halving_interval', 'daa_mode', 'mint_count', 'num_contracts',
        'live_contracts',  # unspent contract singletons; None = untracked (pre-v3 reindex)
        # Relationships
        'container_ref', 'authority_ref', 'parent_ref',
        # NFT specific
        'attrs',
        # Encrypted content (Phase 6 / REP-3008)
        'is_encrypted', 'cipher_hash', 'enc_scheme',
        # Timelock (Phase 6 / REP-3009)
        'is_timelocked', 'timelock_mode', 'timelock_unlock_at', 'timelock_cek_hash', 'timelock_hint',
        # WAVE naming (REP-3011)
        'is_wave_duplicate',  # True if this WAVE name token is a duplicate (not canonical)
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
        self.num_contracts = 0  # Number of parallel mining contracts (at deploy)
        # Count of contract singletons still live (unspent). Starts at
        # num_contracts on deploy and decrements as contracts terminate
        # (mined out at maxHeight) or are burned. None = not yet tracked
        # (records written before the v3 reindex) — consumers must treat None
        # as "unknown" and fall back to supply-only mineability.
        self.live_contracts = None
        # Relationships
        self.container_ref = None  # Parent container (if contained)
        self.authority_ref = None  # Authority token reference
        self.parent_ref = None  # Parent token for child tokens
        # NFT specific
        self.attrs = None  # Serialized attributes JSON
        # Encrypted content (Phase 6 / REP-3008)
        self.is_encrypted = False       # True when GLYPH_ENCRYPTED (8) in protocols
        self.cipher_hash = None         # sha256:hex of ciphertext (from metadata.main.hash)
        self.enc_scheme = None          # Encryption scheme (e.g. 'chunked-aead-v1')
        # Timelock (Phase 6 / REP-3009)
        self.is_timelocked = False      # True when GLYPH_TIMELOCK (9) in protocols
        self.timelock_mode = None       # 'block' or 'time'
        self.timelock_unlock_at = None  # Block height or UNIX timestamp
        self.timelock_cek_hash = None   # sha256:hex CEK commitment
        self.timelock_hint = None       # Optional viewer hint
        # WAVE naming (REP-3011)
        self.is_wave_duplicate = False  # True if this WAVE name is a duplicate (not canonical)
    
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
            'nc': self.num_contracts,
            # Relationships
            'co': self.container_ref,
            'ar': self.authority_ref,
            'pr': self.parent_ref,
            # NFT
            'at': self.attrs,
            # Encrypted
            'xe': self.is_encrypted or None,
            'xh': self.cipher_hash,
            'xs': self.enc_scheme,
            # Timelock
            'tl': self.is_timelocked or None,
            'tm': self.timelock_mode,
            'tu': self.timelock_unlock_at,
            'tc': self.timelock_cek_hash,
            'th': self.timelock_hint,
            # WAVE naming
            'wd': self.is_wave_duplicate or None,
        }
        # Remove None values to save space
        data = {k: v for k, v in data.items() if v is not None and v != 0 and v != b''}
        # live_contracts: preserve an explicit 0 (= no live contracts / burned or
        # fully mined), which the zero-strip above would drop. None = untracked
        # (pre-v3) → omit so it reads back as None.
        if self.live_contracts is not None:
            data['lc'] = self.live_contracts
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
        info.num_contracts = d.get('nc', 0)
        info.live_contracts = d.get('lc')  # None if absent (untracked / pre-v3)
        # Relationships
        info.container_ref = d.get('co')
        info.authority_ref = d.get('ar')
        info.parent_ref = d.get('pr')
        # NFT
        info.attrs = d.get('at')
        # Encrypted
        info.is_encrypted = bool(d.get('xe', False))
        info.cipher_hash = d.get('xh')
        info.enc_scheme = d.get('xs')
        # Timelock
        info.is_timelocked = bool(d.get('tl', False))
        info.timelock_mode = d.get('tm')
        info.timelock_unlock_at = d.get('tu')
        info.timelock_cek_hash = d.get('tc')
        info.timelock_hint = d.get('th')
        # WAVE naming
        info.is_wave_duplicate = bool(d.get('wd', False))
        
        return info
    
    def percent_mined(self) -> float:
        """Calculate percentage of total supply that has been mined."""
        if self.total_supply == 0:
            return 0.0
        return (self.mined_supply / self.total_supply) * 100.0

    def is_fully_mined(self) -> bool:
        """All supply mined out (the legitimate 'done' state)."""
        return self.total_supply > 0 and self.mined_supply >= self.total_supply

    def dmint_mineable(self) -> Optional[bool]:
        """Whether this dMint token can still be mined.

        Returns:
            True  — supply remains AND >= 1 live contract singleton.
            False — fully mined, OR all contracts gone with supply remaining
                    (burned/terminated early).
            None  — liveness not tracked (record predates the v3 reindex);
                    callers should fall back to supply-only mineability.
        """
        if self.is_fully_mined():
            return False
        if self.live_contracts is None:
            return None
        return self.live_contracts > 0


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
        self.balance_deletes: Set[bytes] = set()  # balance keys to delete from DB on flush
        # hashX -> base scriptPubKey, for resolving holder rows to a displayable
        # owner identity (address / full scripthash).  Idempotent: a given hashX
        # always maps to the same script, so we never need to delete or undo it.
        self.owner_cache: Dict[bytes, bytes] = {}
        self.history_cache: List[Tuple[int, bytes, bytes]] = []  # (height, key, value)
        self.metadata_cache: Dict[bytes, bytes] = {}  # hash -> cbor
        self.metadata_height: Dict[bytes, int] = {}
        self.token_height: Dict[bytes, int] = {}

        # Cache for key reveals pending flush (R2 — atomic batch write)
        self.key_reveal_cache: Dict[bytes, bytes] = {}  # ref -> cbor bytes
        self.key_reveal_height: Dict[bytes, int] = {}   # ref -> height

        # Pending contract→token reverse index entries for flush (R6)
        self.contract_to_token_cache: Dict[bytes, bytes] = {}  # contract_ref -> token_ref
        self.contract_to_token_height: Dict[bytes, int] = {}   # contract_ref -> height

        # In-memory stats delta accumulator (R11)
        # Tracks net change in counts since last flush
        self._stats_delta: Dict[str, int] = dict(self._STATS_ZERO)

        # Transient set of refs known to exist as tokens (cleared on flush, R14).
        # Prevents redundant DB lookups within a flush window.
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

        # dMint spam denylist: set of 36-byte binary refs whose mint events
        # are suppressed (history + balance writes skipped; GT supply tracking
        # still updated so the token record remains accurate and queryable).
        self._dmint_denylist: Set[bytes] = set()
        raw_denylist: set = getattr(env, 'dmint_denylist', set())
        if raw_denylist:
            for ref_str in raw_denylist:
                try:
                    self._dmint_denylist.add(parse_ref_any(ref_str))
                except Exception:
                    self.logger.warning(f'DMINT_DENYLIST: invalid ref ignored: {ref_str!r}')
            self.logger.info(
                f'dMint denylist active: {len(self._dmint_denylist)} token(s) — '
                f'mint history/balance writes suppressed'
            )

        if self.enabled:
            self.logger.info('Glyph token indexing enabled')

    def post_open_init(self):
        """Called after DB is opened (utxo_db is available). Deferred from __init__."""
        if self.enabled:
            self._check_schema_version()
            if self._dmint_denylist:
                self._scrub_denylist_metadata()
    
    def _check_schema_version(self):
        """R21 — Verify/upgrade DB schema version.

        Three cases:
          * Fresh DB (no version key): stamp CURRENT and return. A from-scratch
            reindex replays every block through the token write path, which now
            populates the v4 discovery indexes natively — no migration needed.
          * Up to date: log and return.
          * Behind: apply in-place migrations one step at a time where an
            in-place migrator exists; hard-fail (full reindex required) for any
            gap without one. This preserves the old safety net for schema
            changes that genuinely need a rescan, while letting the v3->v4
            discovery-index upgrade run without deleting the DB.
        """
        raw = self.db.utxo_db.get(GlyphDBKeys.SCHEMA_VERSION)
        if raw is None:
            self.db.utxo_db.put(GlyphDBKeys.SCHEMA_VERSION,
                                bytes([CURRENT_SCHEMA_VERSION]))
            self.logger.info(f'Glyph DB schema version initialised to {CURRENT_SCHEMA_VERSION}')
            return

        v = raw[0]
        if v > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f'FATAL: Glyph DB schema version {v} > {CURRENT_SCHEMA_VERSION} — '
                f'the DB was written by a newer RXinDexer. Upgrade the software '
                f'or reindex.'
            )
        if v == CURRENT_SCHEMA_VERSION:
            self.logger.info(f'Glyph DB schema version {v} OK')
            return

        # v < CURRENT — walk the in-place migration chain.
        migrations = {3: self._migrate_3_to_4}
        while v < CURRENT_SCHEMA_VERSION:
            migrator = migrations.get(v)
            if migrator is None:
                raise RuntimeError(
                    f'FATAL: Glyph DB schema version {v} < {CURRENT_SCHEMA_VERSION} '
                    f'has no in-place migration. A full reindex is required. '
                    f'Delete the DB directory and restart.'
                )
            self.logger.info(f'Glyph DB schema migration v{v} -> v{v + 1} starting…')
            migrator()
            v += 1
            self.db.utxo_db.put(GlyphDBKeys.SCHEMA_VERSION, bytes([v]))
            self.logger.info(f'Glyph DB schema upgraded to v{v}')

    def _migrate_3_to_4(self) -> int:
        """v3 -> v4: backfill the recency-ordered discovery indexes in place.

        Walks existing GT rows — each already stores ``deploy_height`` and
        ``protocols`` — and writes the BY_TYPE_RECENT / BY_PROTO / GLOBAL_RECENT
        rows. No radiantd rescan and no block reprocessing.

        Notes:
          * Idempotent — re-writing the same derived keys is a no-op, so a run
            interrupted before the version stamp is safe to repeat.
          * Page-committed via ``seek`` so it never holds a RocksDB iterator open
            across a ``write_batch`` commit and never buffers the whole keyspace.
          * No undo is recorded. The migration runs at startup before block sync,
            so no reorg is concurrent; the only residue a later deep reorg could
            leave is an orphan recency row for a just-deployed token whose GT row
            is unwound — and every list query hydrates via ``get_token`` and
            skips a ref whose token is gone, so orphans are inert (self-healing).
        """
        prefix = GlyphDBKeys.TOKEN
        PAGE = 5000
        seek = prefix
        total = 0
        pages = 0
        while True:
            page = []
            for key, value in self.db.utxo_db.iterator(prefix=prefix, seek=seek):
                page.append((key, value))
                if len(page) >= PAGE:
                    break
            if not page:
                break
            with self.db.utxo_db.write_batch() as batch:
                for key, value in page:
                    ref = key[len(prefix):]
                    if len(ref) != 36:
                        continue
                    try:
                        token = GlyphTokenInfo.from_bytes(value)
                    except Exception:
                        continue
                    # Same key computation as the live write path (no undo here;
                    # the migration runs at startup before any reorg).
                    for k, v in self._discovery_rows(ref, token):
                        batch.put(k, v)
                    total += 1
            pages += 1
            self.logger.info(
                f'Glyph v4 migration: {total} tokens indexed ({pages} page(s))')
            if len(page) < PAGE:
                break
            # Resume strictly after the last key (seek is inclusive; a trailing
            # 0x00 byte is the smallest key greater than a fixed-length GT row).
            seek = page[-1][0] + b'\x00'
        self.logger.info(
            f'Glyph v4 migration complete: {total} tokens backfilled into '
            f'discovery indexes (GZ/GP/GQ)')
        return total

    def _scrub_denylist_metadata(self) -> None:
        """Delete stored CBOR metadata blobs (GM keys) for all denylisted tokens.

        Called once on startup when the denylist is non-empty.  The GT token
        record is preserved (supply/dmint fields remain queryable); only the
        raw CBOR payload — which holds the embedded JPEG — is erased.

        Safe to call repeatedly: a missing GM key is silently ignored.
        """
        to_delete = []
        for token_ref in self._dmint_denylist:
            token = self.token_cache.get(token_ref) or self.get_token(token_ref)
            if not token or not token.metadata_hash:
                continue
            gm_key = GlyphDBKeys.METADATA + token.metadata_hash
            existing = self.db.utxo_db.get(gm_key)
            if existing is not None:
                to_delete.append((gm_key, token_ref, len(existing)))

        if not to_delete:
            return

        with self.db.utxo_db.write_batch() as batch:
            for gm_key, token_ref, size in to_delete:
                batch.delete(gm_key)
                self.logger.info(
                    f'DMINT_DENYLIST: scrubbed GM metadata blob for '
                    f'{hash_to_hex_str(token_ref[:32])} '
                    f'({size:,} bytes freed)'
                )
        self.logger.info(
            f'DMINT_DENYLIST: scrub complete — {len(to_delete)} blob(s) deleted'
        )

    @staticmethod
    def _sanitize_str(s, max_len: int) -> Optional[str]:
        """R9 — Strip control chars and cap length on untrusted token strings."""
        if not isinstance(s, str):
            return None
        cleaned = ''.join(c for c in s if ord(c) >= 32)
        return cleaned[:max_len] if cleaned else None

    def process_tx(self, tx_hash: bytes, tx: 'Tx', height: int, tx_idx: int,
                    output_refs_by_vout: Dict[int, List[Tuple[bytes, int]]] = None,
                    spent_singleton_refs: set = None):
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
            spent_singleton_refs: Set of 36-byte singleton refs consumed by
                inputs.  Used to detect burned dMint contract UTXOs.
        
        Returns:
            dict or None: The parsed Glyph envelope if found, for chaining to
                         WAVE/Swap indexers. Returns None if not a Glyph tx.
        """
        if not self.enabled:
            return None
        
        result_envelope = None
        is_token_tx = False
        # Track FT refs that were already known before this tx — candidates for mint
        known_ft_refs_seen = set()
        
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
                    if known and ref_type == 0:
                        # Known FT ref — potential dMint mint event
                        known_ft_refs_seen.add(ref_bytes)
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
                    if known and ref_type == 0:
                        known_ft_refs_seen.add(ref_bytes)
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
            token_type = get_token_type(protocols, metadata)
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
        
        # ===================================================================
        # PHASE 2b: Check OUTPUTS for v2 Style A OP_RETURN envelopes
        # v2 tokens may embed the Glyph envelope in an OP_RETURN output
        # instead of the scriptSig.  Only runs when Phase 2 found nothing.
        # ===================================================================
        if result_envelope is None:
            for vout_idx, output in enumerate(tx.outputs):
                script = output.pk_script
                if not script or not is_glyph_op_return(script):
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
                        f'Invalid Glyph metadata (output) type: tx={hash_to_hex_str(tx_hash)} '
                        f'output={vout_idx} type={type(metadata).__name__}'
                    )
                    continue
                
                protocols = metadata.get('p', [])
                token_type = get_token_type(protocols, metadata)
                self.logger.info(
                    f'Glyph REVEAL (v2 Style A): tx={hash_to_hex_str(tx_hash)} '
                    f'output={vout_idx} type={token_type} protocols={protocols}'
                )
                
                if result_envelope is None:
                    result_envelope = envelope.copy()
                    result_envelope['metadata'] = metadata
                    result_envelope['protocols'] = metadata.get('p', [])
                    result_envelope['tx_hash'] = tx_hash
                    result_envelope['vout_idx'] = vout_idx
                
                # For Style A reveals, the ref comes from the tx outputs
                output_ref = self._find_output_ref(tx_hash, tx, metadata)
                if output_ref:
                    self._index_token_reveal(
                        output_ref, tx_hash, output_ref[32:36],
                        height, tx_idx, envelope, metadata, tx
                    )
                else:
                    self.logger.warning(
                        f'Glyph v2 Style A reveal with no output ref: '
                        f'tx={hash_to_hex_str(tx_hash)} output={vout_idx}'
                    )
        
        # ===================================================================
        # PHASE 3: Detect dMint MINT events
        # If a known FT ref (already deployed) appears in this tx's outputs,
        # it could be a mint event.  _process_mint checks if the token is
        # actually a dMint token and if the tx minted new supply.
        # ===================================================================
        for token_ref in known_ft_refs_seen:
            self._process_mint(tx_hash, tx, height, tx_idx, token_ref,
                               output_refs_by_vout or {})
        
        # ===================================================================
        # PHASE 4: Detect burned dMint contract singletons
        # A singleton ref consumed by an input but NOT recreated in any
        # output is permanently destroyed.  If it belongs to a known dMint
        # token, mark that token as burned so it is removed from the
        # mineable contracts listing.
        # ===================================================================
        if spent_singleton_refs:
            # Collect all singleton refs that survived in outputs
            # Exclude OP_RETURN outputs — burned contracts are locked in unspendable outputs
            output_singletons = set()
            if output_refs_by_vout:
                for vout, ref_list in output_refs_by_vout.items():
                    output = tx.outputs[vout]
                    script = output.pk_script
                    # Skip OP_RETURN outputs (unspendable)
                    # Burn script pattern: 0xd8 + ref + 0x6a (OP_RETURN at end)
                    if script and script[-1] == 0x6a:
                        continue
                    for ref_bytes, ref_type in ref_list:
                        if ref_type == 1:
                            output_singletons.add(ref_bytes)
            
            destroyed = spent_singleton_refs - output_singletons
            for singleton_ref in destroyed:
                self._process_contract_burn(
                    tx_hash, height, tx_idx, singleton_ref)
        
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
        
        # For NFTs and WAVE names, use _extract_refs_from_script to find singleton ref (R4 fix)
        # WAVE tokens use singleton refs but may not have GLYPH_NFT explicitly in protocols
        if GlyphProtocol.GLYPH_NFT in protocols or GlyphProtocol.GLYPH_WAVE in protocols:
            for vout, output in enumerate(tx.outputs):
                refs = self._extract_refs_from_script(output.pk_script)
                for ref_bytes, ref_type in refs:
                    if ref_type == 1:  # singleton
                        return ref_bytes

        # For FTs, use _extract_refs_from_script to find normal ref (R4 fix)
        if GlyphProtocol.GLYPH_FT in protocols:
            for vout, output in enumerate(tx.outputs):
                refs = self._extract_refs_from_script(output.pk_script)
                for ref_bytes, ref_type in refs:
                    if ref_type == 0:  # normal
                        return ref_bytes

        return None
    
    def _find_contract_ref(self, tx: 'Tx', token_ref: bytes) -> Optional[bytes]:
        """
        Find the dMint contract ref from the deploy transaction outputs.
        
        The contract UTXO uses OP_PUSHINPUTREFSINGLETON (0xd8) with a ref
        that differs from the token ref (which uses OP_PUSHINPUTREF 0xd0).
        
        Returns the 36-byte contract ref hex string, or None.
        """
        for vout, output in enumerate(tx.outputs):
            script = output.pk_script
            refs = self._extract_refs_from_script(script)
            for ref_bytes, ref_type in refs:
                if ref_type == 1 and ref_bytes != token_ref:
                    # Found a singleton ref that is not the token ref — contract ref
                    return ref_bytes.hex()
        return None

    def _find_all_contract_refs(self, tx: 'Tx', token_ref: bytes) -> set:
        """
        Find ALL per-contract singleton refs in a dMint deploy.

        Each parallel mining contract output carries a distinct singleton
        (OP_PUSHINPUTREFSINGLETON 0xd8).  The genuine per-contract singletons
        share the TOKEN ref's txid (token ref = `GEN:0`, contracts = `GEN:1..N`);
        the contract covenant also pushes unrelated state singletons at *other*
        txids, which must be excluded (verified on-chain — see
        docs/DMINT_BURN_DETECTION_SCOPE.md). Filter:
            singleton AND ref != token_ref AND ref.txid == token_ref.txid.

        Returns a set of 36-byte contract refs (may be empty).
        """
        token_txid = token_ref[:32]
        found = set()
        for output in tx.outputs:
            for ref_bytes, ref_type in self._extract_refs_from_script(output.pk_script):
                if (ref_type == 1
                        and ref_bytes != token_ref
                        and ref_bytes[:32] == token_txid):
                    found.add(ref_bytes)
        return found

    def _parse_deploy_contract_state(self, token: 'GlyphTokenInfo', tx: 'Tx'):
        """
        Parse initial contract state from the deploy transaction's outputs.
        
        The dMint contract output script encodes live state values.
        Uses parse_dmint_contract_state() from glyph.py for the actual parsing.
        Also counts the number of parallel contract outputs so that
        total_supply reflects all contracts (num_contracts * reward * max_height).
        """
        from electrumx.lib.glyph import parse_dmint_contract_state
        
        num_contracts = 0
        parsed_state = None
        
        for vout, output in enumerate(tx.outputs):
            script = output.pk_script
            if len(script) < 80:
                continue
            if b'\xd8' not in script:
                continue
            
            state = parse_dmint_contract_state(script)
            if state:
                num_contracts += 1
                if parsed_state is None:
                    parsed_state = state
        
        if not parsed_state:
            return
        
        # Override with on-chain state values if present.
        # H5: parse_dmint_contract_state already rejects contracts with a
        # negative/out-of-range reward or max_height, but guard here too so a
        # negative scriptnum can never reach token.reward / current_difficulty
        # (defence in depth — these feed the /dmint/contracts listing and the
        # difficulty math).
        from electrumx.lib.glyph import DMINT_MAX_TOTAL_SUPPLY
        if parsed_state.get('reward') and parsed_state['reward'] > 0:
            token.reward = parsed_state['reward']
        if parsed_state.get('target') and parsed_state['target'] > 0:
            token.current_difficulty = parsed_state['target']
            if not token.start_difficulty:
                token.start_difficulty = parsed_state['target']
        max_height = parsed_state.get('max_height')
        if max_height and max_height > 0:
            # Calculate total_supply from on-chain state if CBOR
            # metadata didn't provide it.  Multiply by num_contracts
            # since each parallel contract can mint independently.
            reward = parsed_state.get('reward') or token.reward or 0
            if reward and reward > 0 and not token.total_supply:
                supply = num_contracts * reward * max_height
                # Clamp to the int64 ceiling so a maliciously huge reward /
                # max_height / num_contracts can never produce an absurd or
                # overflowing supply that poisons percent_mined / is_fully_mined.
                if 0 < supply <= DMINT_MAX_TOTAL_SUPPLY:
                    token.total_supply = supply
                # else: leave total_supply at 0 (treated as "unknown/unbounded"
                # by percent_mined()/is_fully_mined(), which guard total>0).
        
        # V2-specific fields from on-chain state
        if 'algo_id' in parsed_state:
            token.algorithm = parsed_state['algo_id']
        if 'daa_mode' in parsed_state:
            token.daa_mode = parsed_state['daa_mode']
        
        token.num_contracts = num_contracts
        self.logger.info(
            f'Parsed dMint contract state: reward={parsed_state.get("reward")} '
            f'target={parsed_state.get("target")} algo={parsed_state.get("algo_id")} '
            f'daa_mode={parsed_state.get("daa_mode")} contracts={num_contracts}'
        )
    
    def _process_mint(self, tx_hash: bytes, tx: 'Tx', height: int,
                      tx_idx: int, token_ref: bytes,
                      output_refs_by_vout: Dict[int, List[Tuple[bytes, int]]]):
        """
        Process a dMint mint event.
        
        Detects when a dMint contract UTXO is spent and recreated with
        updated state. Updates mint_count, current_supply, mined_supply,
        current_difficulty. Records a MINT event in history.
        
        A mint tx has these characteristics:
        - An input spending a known dMint token ref UTXO
        - An output recreating that ref (the contract carries forward)
        - New minted token outputs with the same FT ref (OP_PUSHINPUTREF 0xd0)
        
        Args:
            tx_hash: Transaction hash
            tx: Transaction object
            height: Block height
            tx_idx: Transaction index in block
            token_ref: The 36-byte token ref that was detected
            output_refs_by_vout: Pre-parsed ref data from block processor
        """
        from electrumx.lib.glyph import parse_dmint_contract_state
        
        # Load the token from cache or DB
        token = self.token_cache.get(token_ref)
        if not token:
            key = pack_token_key(token_ref)
            data = self.db.utxo_db.get(key)
            if data:
                token = GlyphTokenInfo.from_bytes(data)
            else:
                return  # Token not found — skip
        
        # Only process dMint tokens
        if GlyphProtocol.GLYPH_DMINT not in token.protocols:
            return
        
        # Scan outputs to count contract UTXOs mined and parse contract state.
        # Contract outputs carry both a singleton ref (0xd8) and the token ref
        # (0xd0).  Each contract output represents one parallel contract that
        # was mined in this tx.  The newly minted amount is
        # num_contracts_mined * reward (from the contract state), which avoids
        # double-counting transferred/consolidated tokens in the same tx.
        num_contracts_mined = 0
        latest_state = None
        for vout, output in enumerate(tx.outputs):
            script = output.pk_script
            refs = self._extract_refs_from_script(script)
            has_singleton = any(rt == 1 for _, rt in refs)
            has_token_ref = any(rb == token_ref and rt == 0 for rb, rt in refs)
            if has_singleton and has_token_ref:
                num_contracts_mined += 1
                if latest_state is None and len(script) >= 80:
                    latest_state = parse_dmint_contract_state(script)
        
        if num_contracts_mined <= 0:
            return  # Not a mint — just a transfer or other operation
        
        # Update difficulty and reward from the contract state.
        # H5: parse_dmint_contract_state already rejects negative reward/target
        # at parse time; the >0 guards here are defence in depth so a bad
        # scriptnum can never overwrite a good reward/difficulty.
        if latest_state:
            if latest_state.get('target') and latest_state['target'] > 0:
                token.current_difficulty = latest_state['target']
            if latest_state.get('reward') and latest_state['reward'] > 0:
                token.reward = latest_state['reward']
        
        # Calculate minted amount: each contract mined produces `reward` tokens
        reward = token.reward or 0
        minted_amount = num_contracts_mined * reward
        
        if minted_amount <= 0:
            return
        
        # Update token state (guard against None for tokens indexed before dMint fields existed)
        token.mint_count = (token.mint_count or 0) + 1
        token.mined_supply = (token.mined_supply or 0) + minted_amount
        token.current_supply = (token.current_supply or 0) + minted_amount
        
        # Check if fully mined
        if (token.total_supply or 0) > 0 and (token.mined_supply or 0) >= token.total_supply:
            token.is_spent = True
        
        # Put back into cache so it gets flushed
        self.token_cache[token_ref] = token
        self.token_height[token_ref] = height
        
        # Denylisted tokens: supply counters already updated above; skip the
        # history event and per-address balance writes to keep the DB lean.
        if token_ref in self._dmint_denylist:
            return

        # Record MINT event in history
        history_key = pack_history_key(token_ref, height, tx_idx)
        history_value = (struct.pack('<B', GlyphEventType.MINT) + tx_hash +
                         struct.pack('<Q', minted_amount))
        self.history_cache.append((height, history_key, history_value))
        
        if token.mint_count % 100 == 1 or token.mint_count <= 1:
            self.logger.info(
                f'dMint MINT: token={hash_to_hex_str(token_ref[:32])} '
                f'amount={minted_amount} count={token.mint_count} '
                f'supply={token.mined_supply}/{token.total_supply}'
            )
    
    def _process_contract_burn(self, tx_hash: bytes, height: int,
                               tx_idx: int, singleton_ref: bytes):
        """
        Handle a destroyed dMint contract singleton.

        Called when a singleton ref was spent in inputs but NOT recreated in
        any output — that one contract UTXO is permanently gone, either because
        it was mined out at maxHeight (normal completion) or burned (e.g. to an
        OP_RETURN output).

        Decrements the owning token's `live_contracts` count. It does NOT mark
        the whole token `is_spent`: a dMint token has `num_contracts` parallel
        contracts and the others may still be mineable. (The previous behaviour
        — marking the whole token spent on the first destroyed singleton — hid
        partially-mined tokens like GRASS, which was 75.9% mined with ~5 live
        contracts when its first contract completed.)
        """
        token_ref = None
        token = None

        # R6: O(1) lookup via GC reverse index (contract_ref -> token_ref)
        gc_key = GlyphDBKeys.CONTRACT_TO_TOKEN + singleton_ref
        token_ref_bytes = self.db.utxo_db.get(gc_key)
        if token_ref_bytes and len(token_ref_bytes) == 36:
            token_ref = token_ref_bytes
            token = self.token_cache.get(token_ref) or self.get_token(token_ref)
        else:
            # Fallback: check pending contract_to_token_cache (not yet flushed)
            token_ref = self.contract_to_token_cache.get(singleton_ref)
            if token_ref:
                token = self.token_cache.get(token_ref) or self.get_token(token_ref)

        if token is None or token_ref is None:
            return  # Singleton doesn't belong to a known dMint token

        if token.live_contracts is None:
            # Record predates the v3 reindex — liveness untracked; can't safely
            # decrement. (A reindex registers every contract and re-runs this.)
            return
        if token.live_contracts <= 0:
            return  # Already fully accounted for

        token.live_contracts -= 1
        self.token_cache[token_ref] = token
        self.token_height[token_ref] = height

        remaining = token.live_contracts
        # Emit a single token-level BURN history event ONLY when the token is
        # genuinely terminated early — all contracts gone with supply still
        # unmined. Normal per-contract completion (other contracts remain, or
        # the token ends fully mined) is NOT a burn; emitting one event per
        # destroyed contract would inflate burned_count / get_token_burns for
        # healthy fully-mined tokens. This preserves the prior 0/1 semantics
        # (1 = the token was abandoned/terminated early).
        if remaining == 0 and not token.is_fully_mined():
            history_key = pack_history_key(token_ref, height, tx_idx)
            history_value = struct.pack('<B', GlyphEventType.BURN) + tx_hash
            self.history_cache.append((height, history_key, history_value))
            self.logger.info(
                f'dMint token TERMINATED early (all contracts gone, '
                f'{token.mined_supply}/{token.total_supply} mined): '
                f'token={hash_to_hex_str(token_ref[:32])} height={height}'
            )
        else:
            self.logger.debug(
                f'dMint contract gone: token={hash_to_hex_str(token_ref[:32])} '
                f'live_contracts={remaining} height={height}'
            )
    
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
        # Pass the decoded metadata so collection parents minted without
        # protocol code 7 (type:"container" only) still store as CONTAINER and
        # remain queryable via glyph.get_tokens_by_type(CONTAINER).
        token.token_type = get_token_type_id(token_info['protocols'], metadata)
        token.glyph_version = token_info.get('version', 1)
        token.name = self._sanitize_str(token_info.get('name'), 200)       # R9
        token.ticker = self._sanitize_str(token_info.get('ticker'), 16)     # R9
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
                dmint_info = token_info.get('dmint', {})
                token.total_supply = dmint_info.get('max_supply', 0) or 0
                token.premine = dmint_info.get('premine', 0) or 0
                token.current_supply = token.premine
                token.mined_supply = 0
                token.mint_count = 0
                # Copy dMint metadata fields
                token.algorithm = dmint_info.get('algorithm', 0) or 0
                token.start_difficulty = dmint_info.get('start_difficulty', 0) or 0
                token.current_difficulty = dmint_info.get('start_difficulty', 0) or 0
                token.reward = dmint_info.get('reward', 0) or 0
                token.daa_mode = dmint_info.get('daa_mode', 0) or 0
                token.halving_interval = dmint_info.get('halflife', 0) or 0
                # Find ALL per-contract singleton refs from the deploy tx.
                # Each parallel mining contract is a distinct singleton (0xd8)
                # sharing the token ref's txid. We register every one so burn
                # detection can track them individually (live_contracts), rather
                # than collapsing a multi-contract token to a single ref.
                contract_refs = self._find_all_contract_refs(tx, ref)
                # contract_ref keeps the first (sorted, for deterministic display)
                token.contract_ref = (
                    sorted(contract_refs)[0].hex() if contract_refs else None
                )
                token.live_contracts = len(contract_refs)
                # R6: write GC reverse index (contract_ref -> token_ref) for each
                for contract_ref_bytes in contract_refs:
                    self.contract_to_token_cache[contract_ref_bytes] = ref
                    self.contract_to_token_height[contract_ref_bytes] = height
                # Also try to parse initial state from the contract output
                self._parse_deploy_contract_state(token, tx)
            else:
                # For non-dMint FTs, get initial supply from output value
                # Use _extract_refs_from_script to avoid false positives (R15)
                initial_supply = 0
                for out in tx.outputs:
                    refs = self._extract_refs_from_script(out.pk_script)
                    if any(ref_type == 0 for _, ref_type in refs):
                        initial_supply = out.value
                        break
                token.total_supply = initial_supply
                token.current_supply = initial_supply
        
        # Extract additional metadata fields
        token.description = token_info.get('description')
        if 'attrs' in token_info and token_info['attrs']:
            token.attrs = token_info['attrs']

        # Extract image/content fields from CBOR remote/embed
        # remote: { t: MIME, u: URL/IPFS, h: sha256_bytes, hs: hashstamp_webp }
        # embed:  { t: MIME, b: raw_bytes }
        remote = metadata.get('remote') or metadata.get('rm')
        embed = metadata.get('embed') or metadata.get('em') or metadata.get('main')
        if remote and isinstance(remote, dict):
            token.icon_ref = remote.get('u') or remote.get('url')
            token.icon_type = remote.get('t') or remote.get('type')
            # Store hashstamp (compressed on-chain thumbnail) if present
            hs = remote.get('hs')
            if isinstance(hs, (bytes, bytearray)):
                token.icon_size = len(hs)
            h = remote.get('h')
            if isinstance(h, (bytes, bytearray)):
                token.embedded_data_hash = bytes(h)
        elif embed and isinstance(embed, dict):
            token.icon_type = embed.get('t') or embed.get('type')
            b = embed.get('b')
            # CBORTag 64 = typed array; value may be hex str or bytes
            if hasattr(b, 'value'):
                b = bytes.fromhex(b.value) if isinstance(b.value, str) else b.value
            if isinstance(b, (bytes, bytearray)):
                token.icon_size = len(b)
                token.icon_ref = 'embedded'

        # Encrypted content fields (Phase 6 / REP-3008)
        if GlyphProtocol.GLYPH_ENCRYPTED in token.protocols:
            token.is_encrypted = True
            # 'main' sub-object holds enc scheme and ciphertext hash
            main = metadata.get('main') or {}
            if isinstance(main, dict):
                token.cipher_hash = main.get('hash')  # e.g. 'sha256:abcd...'
                token.enc_scheme = main.get('scheme') or main.get('enc')

        # Timelock fields (Phase 6 / REP-3009)
        if GlyphProtocol.GLYPH_TIMELOCK in token.protocols:
            token.is_timelocked = True
            # 'crypto' sub-object contains timelock commitment
            crypto = metadata.get('crypto') or {}
            if isinstance(crypto, dict):
                tl = crypto.get('timelock') or {}
                if isinstance(tl, dict):
                    token.timelock_mode = tl.get('mode')
                    token.timelock_unlock_at = tl.get('unlock_at')
                    token.timelock_cek_hash = tl.get('cek_hash')  # 'sha256:hex'
                    token.timelock_hint = tl.get('hint')

        # Store in cache (GSTAT counting happens once at the GT write point in
        # flush(), covering all registration paths uniformly).
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

    # =========================================================================
    # Key Reveal Index (Phase 6 / REP-3009)
    # =========================================================================

    def record_key_reveal(self, ref: bytes, reveal_tx_hash: bytes,
                          revealed_key: str, reveal_height: int,
                          created_at: int) -> None:
        """
        Persist a CEK reveal record.

        Called when an OP_RETURN reveal transaction is confirmed containing
        the plaintext CEK for a timelocked token.

        Args:
            ref: 36-byte token reference
            reveal_tx_hash: 32-byte txid of the reveal transaction
            revealed_key: Hex-encoded CEK (64 hex chars = 32 bytes)
            reveal_height: Block height at which the reveal was confirmed
            created_at: UNIX timestamp of the reveal tx
        """
        if not self.enabled or not HAS_CBOR:
            return

        record = {
            'tx': reveal_tx_hash,
            'key': revealed_key,
            'h': reveal_height,
            't': created_at,
        }
        db_key = GlyphDBKeys.KEY_REVEALS + ref
        self.key_reveal_cache[ref] = cbor2.dumps(record)  # R2: defer to flush
        self.key_reveal_height[ref] = reveal_height

    def get_key_reveal(self, ref: bytes) -> Optional[Dict]:
        """
        Retrieve a CEK reveal record for a token.

        Returns a dict with fields tx, key, h (height), t (timestamp),
        or None if no reveal is recorded.
        """
        if not self.enabled or not HAS_CBOR:
            return None

        db_key = GlyphDBKeys.KEY_REVEALS + ref
        raw = self.key_reveal_cache.get(ref) or self.db.utxo_db.get(db_key)
        if not raw:
            return None
        try:
            d = cbor2.loads(raw)
            # The reveal record is decoded straight from on-chain/stored CBOR and
            # the result is spread (`**result`) into the glyph.get_key_reveal RPC
            # reply and the REST route. `revealed_key`/`reveal_height`/`created_at`
            # are raw `d.get(...)` values, so a malformed or future-written record
            # could carry a non-JSON-native value (raw bytes for the CEK, a
            # CBORTag, cbor2.undefined, a datetime). Returning that would make the
            # reply un-serialisable and hang the client (aiorpcX silently drops a
            # reply it cannot JSON-encode). Coerce to JSON-safe form.
            return to_jsonsafe({
                'reveal_tx': d['tx'].hex() if isinstance(d.get('tx'), (bytes, bytearray)) else d.get('tx'),
                'revealed_key': d.get('key'),
                'reveal_height': d.get('h'),
                'created_at': d.get('t'),
            })
        except Exception:
            return None

    def list_encrypted_tokens(self, limit: int = 100,
                              timelocked_only: bool = False,
                              cursor: Optional[str] = None) -> Dict[str, Any]:
        """
        Return encrypted (and optionally timelocked) tokens, newest-first.

        v4: backed by the BY_PROTO recency index — a prefix seek instead of the
        former full GT-table scan + in-memory sort (old R16). ``timelocked_only``
        seeks the TIMELOCK(9) facet and keeps the original "must also be
        encrypted" semantics via a hydration predicate. Returns dicts safe for
        JSON serialisation (no raw bytes).

        Consistency note: like every other list endpoint, this now reflects
        flushed DB state (the BY_PROTO rows are written on flush); a token
        deployed in the current, not-yet-flushed batch appears on the next flush.
        The opaque cursor is an index key, not the pre-v4 integer offset.
        """
        if not self.enabled:
            return []

        if timelocked_only:
            proto = GlyphProtocol.GLYPH_TIMELOCK
            predicate = lambda t: t.is_encrypted  # noqa: E731 (parity with old filter)
        else:
            proto = GlyphProtocol.GLYPH_ENCRYPTED
            predicate = None
        prefix = GlyphDBKeys.BY_PROTO + struct.pack('<B', proto)
        return self._paginate_hydrated(prefix, limit, cursor, predicate=predicate)

    def update_balance(self, height: int, scripthash: bytes, ref: bytes, delta: int):
        """Update a token balance."""
        if not self.enabled:
            return

        key = pack_balance_key(scripthash, ref)
        holder_key = pack_holder_key(ref, scripthash)
        self._record_undo(height, key)
        self._record_undo(height, holder_key)

        # Check cache first, then balance_deletes (zeroed this cycle),
        # then DB for existing balance.  This ordering prevents stale-read
        # bugs where the DB returns a value from a previous flush cycle
        # after the balance was zeroed in the current cycle.
        if key in self.balance_cache:
            current = self.balance_cache[key]
        elif key in self.balance_deletes:
            current = 0
        else:
            db_val = self.db.utxo_db.get(key)
            current = struct.unpack('<Q', db_val)[0] if db_val and len(db_val) == 8 else 0

        new_balance = max(0, current + delta)

        if new_balance > 0:
            self.balance_cache[key] = new_balance
            self.balance_height[key] = height
            self.balance_deletes.discard(key)
        else:
            self.balance_cache.pop(key, None)
            self.balance_height.pop(key, None)
            # Mark for deletion from DB on next flush
            self.balance_deletes.add(key)

    # Ref data format: each entry is 36 bytes ref_id + 1 byte ref_type
    REF_ENTRY_SIZE = 37

    def process_balance_changes(self, height: int, debits, credits):
        """Process token balance changes for a transaction.

        debits:  list of (hashX, value, refs_bytes) for spent inputs
        credits: list of (hashX, value, refs_dict_keys[, base_script]) for new
                 outputs.  The optional ``base_script`` is the recipient's base
                 locking script (ref preamble stripped); when present it is
                 stashed in the owner index so holder rows resolve to a
                 displayable address.

        Only known Glyph tokens are tracked; other refs are ignored.
        """
        if not self.enabled:
            return

        # Debits: subtract balance for each spent input carrying a token ref
        for hashX, value, refs_data in debits:
            if not refs_data:
                continue
            for i in range(0, len(refs_data), self.REF_ENTRY_SIZE):
                ref = refs_data[i:i + 36]
                if len(ref) == 36 and self._is_known_token(ref):
                    self.update_balance(height, hashX, ref, -value)

        # Credits: add balance for each new output carrying a token ref
        for credit in credits:
            if len(credit) == 4:
                hashX, value, ref_keys, base_script = credit
            else:
                hashX, value, ref_keys = credit
                base_script = None
            credited = False
            for ref in ref_keys:
                if len(ref) == 36 and self._is_known_token(ref):
                    self.update_balance(height, hashX, ref, value)
                    credited = True
            # Persist a resolvable owner identity for this hashX (idempotent).
            if credited and base_script and hashX not in self.owner_cache:
                self.owner_cache[hashX] = base_script
    
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

        # Keep undo info for heights in [db_height - reorg_limit + 1, db_height].
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
        these caches grow large enough to OOM the process.  Per-entry numbers
        follow the same convention as block_processor (~190B for dict[bytes,bytes]
        with 32B values) and intentionally err on the conservative (high) side.
        '''
        if not self.enabled:
            return 0
        undo_entries = sum(len(v) for v in self._undo_cache.values())
        return (
            len(self.token_cache) * 300
            + len(self.balance_cache) * 140
            + len(self.balance_height) * 140
            + len(self.balance_deletes) * 100
            + len(self.owner_cache) * 120
            + len(self.history_cache) * 250
            + len(self.metadata_cache) * 600
            + len(self.metadata_height) * 140
            + len(self.token_height) * 140
            + len(self.key_reveal_cache) * 600
            + len(self.key_reveal_height) * 140
            + len(self.contract_to_token_cache) * 190
            + len(self.contract_to_token_height) * 140
            + undo_entries * 120
            + len(self._known_refs) * 100
        )

    def _discovery_rows(self, ref: bytes, token: 'GlyphTokenInfo'):
        """Yield (key, value) for the v4 discovery rows a token owns.

        One BY_TYPE_RECENT + one GLOBAL_RECENT + one BY_PROTO per distinct
        protocol. All keyed on ``token.deploy_height`` so they stay together and
        can be recreated/deleted deterministically from a token record.
        """
        dh = token.deploy_height
        tt = token.token_type
        yield pack_type_recent_key(tt, dh, ref), b''
        yield pack_global_recent_key(dh, ref), struct.pack('<B', tt & 0xFF)
        for proto in set(token.protocols or ()):
            yield pack_proto_key(proto, dh, ref), b''

    def _write_discovery_rows(self, batch, ref: bytes, token: 'GlyphTokenInfo',
                              height: int):
        """Write a token's v4 discovery rows (undo-recorded, like BY_TYPE, so a
        reorg's backup() removes the newly-created rows).

        Facets that are not a primary token_type (encrypted=8, mutable=5,
        timelock=9, …) get a first-class recency-ordered list this way, so those
        queries no longer need a full GT scan.
        """
        for k, v in self._discovery_rows(ref, token):
            self._record_undo(height, k)
            batch.put(k, v)

    def _delete_discovery_rows(self, batch, ref: bytes,
                               token: 'GlyphTokenInfo', height: int):
        """Delete a token's v4 discovery rows (undo-recorded so a reorg restores
        them). Used to clear rows keyed on a superseded deploy_height/type/proto
        set when the same ref is re-written."""
        for k, _v in self._discovery_rows(ref, token):
            self._record_undo(height, k)
            batch.delete(k)

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
            # Count each distinct token exactly once, here at the single GT
            # write point — there are several registration paths and counting
            # at each drifts (the GSTAT total previously undercounted the GT row
            # set ~2x). A token re-written for a metadata update already exists
            # in the DB, so it is not recounted.
            existing_raw = self.db.utxo_db.get(key)
            if existing_raw is None:
                self._update_stats_delta(token, +1)
            else:
                # Re-write (e.g. a mutable metadata UPDATE re-reveals the same
                # ref at a new height). The v4 discovery keys embed deploy_height
                # / token_type / protocols, so if any of those changed the old
                # rows would orphan and the token would appear twice in a recency
                # list. Drop the stale rows first (undo-recorded, so a reorg
                # restores them). No-op when nothing changed.
                try:
                    prev_token = GlyphTokenInfo.from_bytes(existing_raw)
                except Exception:
                    prev_token = None
                if prev_token is not None:
                    self._delete_discovery_rows(batch, ref, prev_token, height)
            self._record_undo(height, key)
            batch.put(key, token.to_bytes())

            # Also index by type
            type_key = GlyphDBKeys.BY_TYPE + struct.pack('<B', token.token_type) + ref
            self._record_undo(height, type_key)
            batch.put(type_key, b'')

            # v4 recency-ordered discovery indexes (by type, by protocol, global).
            self._write_discovery_rows(batch, ref, token, height)

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
        # Balance key format: GB(2) + hashX(HASHX_LEN) + ref(36)
        hx_off = 2 + HASHX_LEN  # offset where ref starts in balance key
        for key, amount in self.balance_cache.items():
            height = self.balance_height.get(key)
            if height is None:
                continue
            packed = struct.pack('<Q', amount)
            batch.put(key, packed)
            # Write secondary holder-by-ref index
            scripthash = key[2:hx_off]
            ref = key[hx_off:hx_off + 36]
            holder_key = pack_holder_key(ref, scripthash)
            batch.put(holder_key, packed)

        # Flush owner-resolution index (hashX -> base scriptPubKey).
        # Idempotent and append-only: a hashX always maps to the same script, so
        # no undo is recorded — a stale entry is never read once its holder rows
        # are gone.  Lets holder rows resolve to a displayable address.
        for hashX, base_script in self.owner_cache.items():
            batch.put(pack_owner_key(hashX), base_script)
        self.owner_cache.clear()

        # Delete zero-balance entries from DB (primary + secondary)
        # R1: record undo BEFORE deleting so reorgs can restore zero-balanced entries
        for key in self.balance_deletes:
            height = self.balance_height.get(key, self.db.db_height)
            self._record_undo(height, key)
            scripthash = key[2:hx_off]
            ref = key[hx_off:hx_off + 36]
            holder_key = pack_holder_key(ref, scripthash)
            self._record_undo(height, holder_key)
            batch.delete(key)
            batch.delete(holder_key)
        
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

        # R2: Flush key reveals (atomic write inside batch with undo)
        for ref, cbor_data in self.key_reveal_cache.items():
            key = GlyphDBKeys.KEY_REVEALS + ref
            height = self.key_reveal_height.get(ref, self.db.db_height)
            self._record_undo(height, key)
            batch.put(key, cbor_data)

        # R6: Flush contract→token reverse index
        for contract_ref_bytes, token_ref in self.contract_to_token_cache.items():
            key = GlyphDBKeys.CONTRACT_TO_TOKEN + contract_ref_bytes
            height = self.contract_to_token_height.get(contract_ref_bytes, self.db.db_height)
            self._record_undo(height, key)
            batch.put(key, token_ref)

        # R11: Flush incremental stats counter
        self._flush_stats_counter(batch)

        # Persist undo information last so it includes keys written above.
        for height, entries in sorted(self._undo_cache.items()):
            batch.put(self._undo_key(height), encode_undo(entries))  # R22
        self._undo_cache.clear()
        self._undo_seen.clear()
        
        # Clear caches
        self.token_cache.clear()
        self.balance_cache.clear()
        self.balance_height.clear()
        self.balance_deletes.clear()
        self.history_cache.clear()
        self.metadata_cache.clear()
        self.metadata_height.clear()
        self.token_height.clear()
        self.key_reveal_cache.clear()          # R2
        self.key_reveal_height.clear()         # R2
        self.contract_to_token_cache.clear()   # R6
        self.contract_to_token_height.clear()  # R6
        self._stats_delta = dict(self._STATS_ZERO)  # R11
        self._known_refs.clear()               # R14: clear on every flush
    
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
    
    def _flush_stats_counter(self, batch):
        """R11 — Merge stats delta into persisted GSTAT counter."""
        if not any(self._stats_delta.values()):
            return
        raw = self.db.utxo_db.get(GlyphDBKeys.STATS)
        current = None
        if raw:
            try:
                current = cbor2.loads(raw)
            except Exception:
                current = None
        if current is None:
            current = dict(self._STATS_ZERO)
        for k, delta in self._stats_delta.items():
            current[k] = max(0, current.get(k, 0) + delta)
        batch.put(GlyphDBKeys.STATS, cbor2.dumps(current))

    # All bucket keys tracked in GSTAT; by_type buckets sum to `total`.
    _STATS_ZERO = {'total': 0, 'ft': 0, 'nft': 0, 'dat': 0, 'dmint': 0,
                   'wave': 0, 'container': 0, 'authority': 0, 'unknown': 0,
                   'v1': 0, 'v2': 0}

    def _update_stats_delta(self, token: 'GlyphTokenInfo', sign: int):
        """R11 — Accumulate a +1/-1 delta for a token's type/version buckets.

        Every token increments exactly one type bucket so the per-type buckets
        sum to ``total`` (previously WAVE/Container/Authority/unknown tokens hit
        ``total`` but no bucket, leaving the two disagreeing).
        """
        self._stats_delta['total'] += sign
        tt = token.token_type
        bucket = {
            GlyphTokenType.FT: 'ft',
            GlyphTokenType.NFT: 'nft',
            GlyphTokenType.DAT: 'dat',
            GlyphTokenType.DMINT: 'dmint',
            GlyphTokenType.WAVE: 'wave',
            GlyphTokenType.CONTAINER: 'container',
            GlyphTokenType.AUTHORITY: 'authority',
        }.get(tt, 'unknown')
        self._stats_delta[bucket] += sign
        if getattr(token, 'glyph_version', 1) == 2:
            self._stats_delta['v2'] += sign
        else:
            self._stats_delta['v1'] += sign

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about indexed Glyph tokens.
        Reads from the incremental GSTAT counter — O(1). (R11)
        """
        base = {
            'enabled': self.enabled,
            'total_tokens': 0,
            'by_type': {'FT': 0, 'NFT': 0, 'DAT': 0, 'dMint': 0,
                        'WAVE': 0, 'Container': 0, 'Authority': 0, 'unknown': 0},
            'by_version': {'v1': 0, 'v2': 0},
            'cache_size': len(self.token_cache),
        }
        if not self.enabled:
            return base
        raw = self.db.utxo_db.get(GlyphDBKeys.STATS)
        if raw:
            try:
                c = cbor2.loads(raw)
                base['total_tokens'] = c.get('total', 0)
                base['by_type']['FT'] = c.get('ft', 0)
                base['by_type']['NFT'] = c.get('nft', 0)
                base['by_type']['DAT'] = c.get('dat', 0)
                base['by_type']['dMint'] = c.get('dmint', 0)
                base['by_type']['WAVE'] = c.get('wave', 0)
                base['by_type']['Container'] = c.get('container', 0)
                base['by_type']['Authority'] = c.get('authority', 0)
                base['by_type']['unknown'] = c.get('unknown', 0)
                base['by_version']['v1'] = c.get('v1', 0)
                base['by_version']['v2'] = c.get('v2', 0)
            except Exception:
                pass
        return base
    
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
    
    @staticmethod
    def _scripthash_to_hashX(scripthash: bytes) -> bytes:
        """Convert an Electrum scripthash to the internal 11-byte hashX.

        Glyph balance/holder rows are keyed by ``hashX`` (the recipient's
        base-address hashX), exactly as the rest of ElectrumX keys address
        indexes.  Clients, however, pass the standard Electrum *scripthash*
        (``sha256(scriptPubKey)``, 32 bytes, the same value accepted by
        ``blockchain.scripthash.*``).  This mirrors
        ``electrumx.server.session.scripthash_to_hashX``: reverse to the
        natural sha256 byte order, then take the first HASHX_LEN bytes.

        A value that is already hashX-length (internal callers) is returned
        unchanged.
        """
        if len(scripthash) == 32:
            return scripthash[::-1][:HASHX_LEN]
        return scripthash[:HASHX_LEN]

    def _script_to_address(self, script: bytes) -> Optional[str]:
        """Best-effort base58 address from a base locking script.

        Matches the standard P2PKH / P2SH byte templates directly (robust and
        unambiguous); returns ``None`` for anything exotic — the caller still
        has the full scripthash + raw script to fall back on.
        """
        if not script:
            return None
        coin = getattr(self.env, 'coin', None)
        if coin is None:
            return None
        try:
            # P2PKH: OP_DUP OP_HASH160 <push20> OP_EQUALVERIFY OP_CHECKSIG
            if (len(script) == 25 and script[0] == OpCodes.OP_DUP
                    and script[1] == OpCodes.OP_HASH160 and script[2] == 0x14
                    and script[23] == OpCodes.OP_EQUALVERIFY
                    and script[24] == OpCodes.OP_CHECKSIG):
                return Base58.encode_check(coin.P2PKH_VERBYTE + script[3:23])
            # P2SH: OP_HASH160 <push20> OP_EQUAL
            if (len(script) == 23 and script[0] == OpCodes.OP_HASH160
                    and script[1] == 0x14 and script[22] == OpCodes.OP_EQUAL):
                return Base58.encode_check(coin.P2SH_VERBYTES[0] + script[2:22])
        except (Base58Error, Exception):
            return None
        return None

    def _owner_identity(self, hashX: bytes) -> Dict[str, Any]:
        """Resolve a holder hashX to a displayable owner identity.

        Returns ``{'address', 'scripthash', 'hashX'}``.  ``address`` and the
        full 32-byte Electrum ``scripthash`` are populated when the owner index
        (``GO``) has the base scriptPubKey for this hashX (written during
        indexing / resync); otherwise they are ``None`` and only the one-way
        ``hashX`` is available.
        """
        ident = {'address': None, 'scripthash': None, 'hashX': hashX.hex()}
        script = self.db.utxo_db.get(pack_owner_key(hashX))
        if script:
            # Electrum scripthash convention: sha256(script) reversed.
            ident['scripthash'] = sha256(script)[::-1].hex()
            ident['address'] = self._script_to_address(script)
        return ident

    def get_balance(self, scripthash: bytes, ref: bytes) -> int:
        """Get token balance for an address scripthash + token ref."""
        key = pack_balance_key(self._scripthash_to_hashX(scripthash), ref)
        
        # Check cache
        if key in self.balance_cache:
            return self.balance_cache[key]
        
        # Query database
        data = self.db.utxo_db.get(key)
        if data:
            return struct.unpack('<Q', data)[0]
        return 0
    
    @staticmethod
    def _decode_cursor(cursor: Optional[str]) -> Optional[bytes]:
        """R16: Decode an opaque base64 cursor to a raw RocksDB seek key.

        Accepts BOTH the URL-safe alphabet (what ``_encode_cursor`` now emits)
        and the legacy standard alphabet (cursors already held by clients).
        A ``+`` that a query-string parser turned into a space is restored
        before decoding — that mangling silently reset REST pagination to
        page 1 (decode failed → seek fell back to the prefix).
        """
        if not cursor:
            return None
        try:
            normalised = cursor.replace(' ', '+').replace('-', '+').replace('_', '/')
            return base64.b64decode(normalised)
        except Exception:
            return None

    @staticmethod
    def _encode_cursor(raw_key: bytes) -> str:
        """R16: Encode a raw RocksDB key as an opaque cursor.

        URL-safe alphabet (``-_`` instead of ``+/``): cursors travel in REST
        query strings, where ``+`` is form-decoded to a space and breaks the
        round-trip. ``_decode_cursor`` still accepts old standard-alphabet
        cursors.
        """
        return base64.urlsafe_b64encode(raw_key).decode()

    def get_balances_for_scripthash(self, scripthash: bytes,
                                     limit: int = 100,
                                     cursor: Optional[str] = None) -> Dict[str, Any]:
        """Get all token balances held by an address.

        ``scripthash`` is the standard Electrum scripthash; balances are keyed
        by the recipient's base-address ``hashX``, so convert before seeking.
        """
        results = []
        prefix = GlyphDBKeys.BALANCE + self._scripthash_to_hashX(scripthash)
        seek = self._decode_cursor(cursor) or prefix
        next_cursor = None

        for key, value in self.db.utxo_db.iterator(prefix=prefix, seek=seek):
            if len(results) >= limit:
                next_cursor = self._encode_cursor(key)
                break

            ref = key[len(prefix):]
            amount = struct.unpack('<Q', value)[0]

            token = self.get_token(ref)
            if token:
                results.append({
                    'ref': ref_to_display(ref),
                    'ref_hex': ref.hex(),
                    'amount': amount,
                    'name': token.name,
                    'ticker': token.ticker,
                    'decimals': token.decimals,
                    'type': token.token_type,
                })

        return {'balances': results, 'next_cursor': next_cursor}
    
    def get_token_history(self, ref: bytes, limit: int = 100,
                          offset: int = 0,
                          cursor: Optional[str] = None,
                          _use_cursor: bool = False):
        """Get transaction history for a token.

        Two response shapes for backwards compatibility:

        * Legacy (``_use_cursor=False``, the default): returns a plain
          ``List[Dict]`` and honours ``offset``. This preserves the
          contract that existing offset/limit callers depend on.
        * Cursor (``_use_cursor=True``, set by the JSON-RPC handler when
          the client explicitly passes a ``cursor`` argument): returns
          ``{'entries', 'next_cursor', 'has_more'}``. ``offset`` is
          ignored. ``cursor`` is an opaque token returned by a prior
          response; pass ``None`` to start from the beginning.

        See docs/pagination-cursors.md for the design rationale.
        """
        prefix = GlyphDBKeys.HISTORY + ref

        if _use_cursor:
            seek = self._decode_cursor(cursor) or prefix
            entries = []
            next_cursor = None
            for key, value in self.db.utxo_db.iterator(prefix=prefix, seek=seek):
                if len(entries) >= limit:
                    next_cursor = self._encode_cursor(key)
                    break
                prefix_len = len(prefix)
                height = struct.unpack('>I', key[prefix_len:prefix_len + 4])[0]
                tx_idx = struct.unpack('>H', key[prefix_len + 4:prefix_len + 6])[0]
                event_type = value[0]
                txid = value[1:33]
                entries.append({
                    'height': height,
                    'tx_idx': tx_idx,
                    'txid': hash_to_hex_str(txid),
                    'event': self._event_type_name(event_type),
                })
            return {
                'entries': entries,
                'next_cursor': next_cursor,
                'has_more': next_cursor is not None,
            }

        results = []
        count = 0
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            if count < offset:
                count += 1
                continue
            if len(results) >= limit:
                break

            height = struct.unpack('>I', key[len(prefix):len(prefix)+4])[0]
            tx_idx = struct.unpack('>H', key[len(prefix)+4:len(prefix)+6])[0]
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
    
    def get_mint_history(self, ref: bytes, limit: int = 100,
                         offset: int = 0) -> Dict[str, Any]:
        """
        Get dMint mint history for a token.
        
        Returns only MINT events, including minted_amount per event.
        """
        mints = []
        total_mints = 0
        prefix = GlyphDBKeys.HISTORY + ref
        
        for key, value in self.db.utxo_db.iterator(prefix=prefix):
            if len(value) < 1:
                continue
            event_type = value[0]
            if event_type != GlyphEventType.MINT:
                continue
            
            total_mints += 1
            if total_mints > offset and len(mints) < limit:
                prefix_len = len(GlyphDBKeys.HISTORY) + 36  # R5: absolute offsets
                height = struct.unpack('>I', key[prefix_len:prefix_len + 4])[0]
                tx_idx = struct.unpack('>H', key[prefix_len + 4:prefix_len + 6])[0]
                tx_hash = value[1:33] if len(value) >= 33 else b''
                # MINT events store minted_amount as uint64 after txid
                minted_amount = 0
                if len(value) >= 41:
                    minted_amount = struct.unpack('<Q', value[33:41])[0]
                
                mints.append({
                    'height': height,
                    'tx_idx': tx_idx,
                    'txid': hash_to_hex_str(tx_hash) if tx_hash else None,
                    'minted_amount': minted_amount,
                })
        
        # Get token info for context
        token = self.get_token(ref)
        
        return {
            'ref': ref_to_display(ref),
            'ref_hex': ref.hex(),
            'name': token.name if token else None,
            'ticker': token.ticker if token else None,
            'total_mints': total_mints,
            'total_supply': token.total_supply if token else 0,
            'mined_supply': token.mined_supply if token else 0,
            'percent_mined': token.percent_mined() if token and token.total_supply > 0 else None,
            'mints': mints,
            'limit': limit,
            'offset': offset,
        }
    
    def get_dmint_tokens(self, limit: int = 100, active_only: bool = True,
                         cursor: Optional[str] = None) -> Dict[str, Any]:
        """
        Get all dMint tokens with full mining details.

        Optionally filter to active-only (not fully mined).
        Supports cursor-based pagination via opaque `cursor` / `next_cursor`.
        """
        tokens = []
        prefix = GlyphDBKeys.BY_TYPE + struct.pack('<B', GlyphTokenType.DMINT)
        seek = self._decode_cursor(cursor) or prefix
        next_cursor = None

        for key, _ in self.db.utxo_db.iterator(prefix=prefix, seek=seek):
            ref = key[len(prefix):]
            token = self.get_token(ref)
            if not token:
                continue
            if active_only:
                # Exclude not-mineable tokens (fully mined OR burned). For
                # records predating the v3 reindex, dmint_mineable() is None —
                # fall back to the is_spent flag so behaviour is unchanged until
                # the reindex backfills live_contracts.
                m = token.dmint_mineable()
                if m is False or (m is None and token.is_spent):
                    continue
            if len(tokens) >= limit:
                next_cursor = self._encode_cursor(key)
                break
            tokens.append(self._token_to_dict(token))

        return {
            'tokens': tokens,
            'limit': limit,
            'active_only': active_only,
            'next_cursor': next_cursor,
        }
    
    def search_tokens(self, query: str, protocols: List[int] = None,
                      limit: int = 50,
                      cursor: Optional[str] = None,
                      _use_cursor: bool = False):
        """Search tokens by name or ticker.

        Legacy shape (``_use_cursor=False``): returns ``List[Dict]``.
        Cursor shape (``_use_cursor=True``): returns
        ``{entries, next_cursor, has_more}`` with a stable seek-key cursor.

        See docs/pagination-cursors.md.
        """
        query_lower = query.lower()
        name_hash = sha256(query_lower.encode('utf-8'))[:16]
        prefix = GlyphDBKeys.BY_NAME + name_hash

        if _use_cursor:
            entries = []
            seek = self._decode_cursor(cursor) or prefix
            next_cursor = None
            for key, _ in self.db.utxo_db.iterator(prefix=prefix, seek=seek):
                if len(entries) >= limit:
                    next_cursor = self._encode_cursor(key)
                    break
                ref = key[len(prefix):]
                token = self.get_token(ref)
                if token:
                    if protocols and not any(p in token.protocols for p in protocols):
                        continue
                    entries.append(self._token_to_dict(token))
            return {
                'entries': entries,
                'next_cursor': next_cursor,
                'has_more': next_cursor is not None,
            }

        results = []
        for key, _ in self.db.utxo_db.iterator(prefix=prefix):
            if len(results) >= limit:
                break
            ref = key[len(prefix):]
            token = self.get_token(ref)
            if token:
                if protocols and not any(p in token.protocols for p in protocols):
                    continue
                results.append(self._token_to_dict(token))
        return results
    
    def _paginate_hydrated(self, prefix: bytes, limit: int,
                           cursor: Optional[str] = None,
                           predicate=None) -> Dict[str, Any]:
        """Seek a secondary index whose keys END in a 36-byte ref, hydrate each
        token, and paginate with an opaque forward cursor (the raw next key).

        Works for both the legacy ref-ordered indexes (``GY + type``, remainder
        is exactly the ref) and the v4 recency indexes (``…+ inv_height + ref``),
        because the ref is always the trailing 36 bytes. A row whose token is
        gone (spent, or a rare reorg orphan) hydrates to ``None`` and is skipped,
        which keeps the v4 backfill self-healing. ``predicate``, if given, is a
        ``token -> bool`` filter applied after hydration (the cursor still points
        at the next raw key, so pagination stays correct across filtered rows).
        """
        results = []
        seek = self._decode_cursor(cursor) or prefix
        next_cursor = None
        for key, _ in self.db.utxo_db.iterator(prefix=prefix, seek=seek):
            if len(results) >= limit:
                next_cursor = self._encode_cursor(key)
                break
            token = self.get_token(key[-36:])
            if token and (predicate is None or predicate(token)):
                # No raw embed payloads in LIST pages — see _token_to_dict.
                results.append(self._token_to_dict(token, include_embed_data=False))
        return {'tokens': results, 'next_cursor': next_cursor}

    def get_tokens_by_type(self, token_type: int, limit: int = 100,
                           cursor: Optional[str] = None,
                           order: str = 'ref') -> Dict[str, Any]:
        """Get tokens of a type with cursor-based pagination.

        ``order='ref'`` (default) — legacy stable order by ref bytes (txid hash,
        i.e. effectively random); preserved so existing ``/glyphs/by-type``
        cursors keep working. ``order='recent'`` — newest-deployed first via the
        v4 BY_TYPE_RECENT index. Cursors are order-specific (opaque raw keys) and
        must not be carried across a change of ``order``.
        """
        if order == 'recent':
            prefix = GlyphDBKeys.BY_TYPE_RECENT + struct.pack('<B', token_type & 0xFF)
        else:
            prefix = GlyphDBKeys.BY_TYPE + struct.pack('<B', token_type & 0xFF)
        return self._paginate_hydrated(prefix, limit, cursor)

    def get_recent_tokens(self, limit: int = 100,
                          cursor: Optional[str] = None) -> Dict[str, Any]:
        """Newest-deployed tokens across every type (v4 GLOBAL_RECENT index)."""
        return self._paginate_hydrated(GlyphDBKeys.GLOBAL_RECENT, limit, cursor)

    def get_tokens_by_protocol(self, proto: int, limit: int = 100,
                               cursor: Optional[str] = None) -> Dict[str, Any]:
        """Newest-first list of tokens carrying a given GlyphProtocol (v4).

        Gives first-class, index-backed lists for protocol facets that are not a
        primary token_type: encrypted(8), mutable(5), timelock(9), container(7),
        authority(10), etc. — no full GT scan.
        """
        prefix = GlyphDBKeys.BY_PROTO + struct.pack('<B', proto & 0xFF)
        return self._paginate_hydrated(prefix, limit, cursor)

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
                # Size-capped to match index-time decoding; an over-cap or
                # malformed body fails closed (returns None) rather than feeding
                # an unbounded structure to the JSON serialiser.
                return cbor_loads_capped(cbor_data)
            except Exception:
                pass
        return None
    
    def _token_to_dict(self, token: GlyphTokenInfo, include_dmint: bool = True,
                        include_content: bool = True,
                        include_embed_data: bool = True) -> Dict[str, Any]:
        """
        Convert token info to API dict.

        Returns all fields needed by explorers, wallets, and exchanges.
        ``include_embed_data=False`` keeps the ``embed`` summary (type/size)
        but omits the raw hex payload — LIST responses must use it, because a
        page of icon-heavy tokens otherwise ships megabytes per page and blows
        the ElectrumX per-session bandwidth budget (the session gets dropped
        mid-pagination). Single-token endpoints keep the full payload.
        """
        txid, vout = unpack_ref(token.ref)
        
        result = {
            # Core identity (canonical txid_vout + raw 72-hex for round-tripping)
            'ref': hash_to_hex_str(txid) + '_' + str(vout),
            'ref_hex': token.ref.hex(),
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
            # dMint liveness — `mineable` is the authoritative signal the dMint
            # contracts manager gates on (None = untracked pre-v3 → fall back to
            # supply). live_contracts = unspent contract singletons.
            'live_contracts': token.live_contracts,
            'mineable': (token.dmint_mineable()
                         if GlyphProtocol.GLYPH_DMINT in token.protocols else None),
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
            # Re-parse CBOR metadata to expose remote/embed for explorer image rendering
            # Classify files by content (like Photonic Wallet filterFileObj),
            # not by key name, since 'main' can be either embed or remote.
            if token.metadata_hash:
                raw_meta = self.get_metadata(token.metadata_hash)
                if raw_meta and isinstance(raw_meta, dict):
                    file_obj = None
                    for fkey in ('main', 'preview', 'embed', 'em', 'remote', 'rm'):
                        candidate = raw_meta.get(fkey)
                        if isinstance(candidate, dict):
                            file_obj = candidate
                            break
                    if file_obj:
                        # Classify by content: 'u'/'url' = remote, 'b' = embed
                        has_url = isinstance(file_obj.get('u'), str) or isinstance(file_obj.get('url'), str)
                        raw_b = file_obj.get('b')
                        has_bytes = isinstance(raw_b, (bytes, bytearray)) or hasattr(raw_b, 'value')
                        if has_url:
                            hs = file_obj.get('hs')
                            result['remote'] = {
                                'url': file_obj.get('u') or file_obj.get('url'),
                                'type': file_obj.get('t') or file_obj.get('type'),
                                'hash': (file_obj.get('h') or b'').hex() if isinstance(file_obj.get('h'), (bytes, bytearray)) else None,
                                'hashstamp': (bytes(hs).hex() if isinstance(hs, (bytes, bytearray)) else None),
                            }
                        elif has_bytes:
                            b = raw_b
                            # CBORTag 64 = typed array; value may be hex str or bytes
                            if hasattr(b, 'value'):
                                b = bytes.fromhex(b.value) if isinstance(b.value, str) else b.value
                            result['embed'] = {
                                'type': file_obj.get('t') or file_obj.get('type'),
                                'size': len(b) if isinstance(b, (bytes, bytearray)) else None,
                                'data': (bytes(b).hex() if isinstance(b, (bytes, bytearray)) else None)
                                        if include_embed_data else None,
                            }
        
        # Include dMint-specific fields for minable tokens
        if include_dmint and GlyphProtocol.GLYPH_DMINT in token.protocols:
            result['dmint'] = {
                'contract_ref': token.contract_ref,
                'algorithm': token.algorithm,
                'algorithm_name': self._algorithm_name(token.algorithm),
                'start_difficulty': token.start_difficulty,
                'current_difficulty': token.current_difficulty,
                'reward': token.reward,
                'premine': token.premine,
                'halving_interval': token.halving_interval,
                'daa_mode': token.daa_mode,
                'daa_mode_name': self._daa_mode_name(token.daa_mode),
                'mint_count': token.mint_count,
                'num_contracts': token.num_contracts,
                'live_contracts': token.live_contracts,
            }
        
        # Include NFT attributes
        if token.attrs:
            result['attrs'] = token.attrs

        # Encrypted content fields (Phase 6 / REP-3008)
        if token.is_encrypted:
            result['is_encrypted'] = True
            if token.cipher_hash is not None:
                result['cipher_hash'] = token.cipher_hash
            if token.enc_scheme is not None:
                result['enc_scheme'] = token.enc_scheme

        # Timelock fields (Phase 6 / REP-3009)
        if token.is_timelocked:
            result['is_timelocked'] = True
            if token.timelock_mode is not None:
                result['timelock_mode'] = token.timelock_mode
            if token.timelock_unlock_at is not None:
                result['timelock_unlock_at'] = token.timelock_unlock_at
            if token.timelock_cek_hash is not None:
                result['timelock_cek_hash'] = token.timelock_cek_hash
            if token.timelock_hint is not None:
                result['timelock_hint'] = token.timelock_hint

        # WAVE naming fields (REP-3011)
        if GlyphProtocol.GLYPH_WAVE in token.protocols:
            result['is_wave'] = True
            result['is_wave_duplicate'] = token.is_wave_duplicate
            if token.is_wave_duplicate:
                result['wave_warning'] = 'This is a DUPLICATE WAVE name registration. It is NOT used for name resolution. Only the first (canonical) registration is authoritative.'

        # Several fields here are carried verbatim from the on-chain CBOR
        # metadata with no type coercion — `attrs` (an arbitrary NFT-attribute
        # sub-structure), plus scalar passthroughs like `description`,
        # `icon_type`, `cipher_hash`, `enc_scheme` and the `timelock_*` fields
        # (each a raw `metadata.get(...)`/`d.get(...)` that round-trips through
        # to_bytes/from_bytes unchanged). Any of these can therefore be a
        # non-JSON-native value: raw bytes, a CBORTag, cbor2.undefined, a
        # Decimal/datetime, or a set. _token_to_dict is the return value of many
        # handlers (glyph.get_token_info, glyph.list_tokens, the REST token
        # routes, the dMint contract sync, ...), several of which do NOT wrap
        # their result in to_jsonsafe; leaking such a value makes the reply
        # un-serialisable and hangs the client, because aiorpcX silently drops a
        # reply it cannot JSON-encode (the same footgun as the
        # glyph.get_metadata timeout). Coerce the whole dict once here so every
        # caller is covered uniformly. This is a no-op tree walk over the
        # already-hex-encoded/primitive fields the method builds explicitly.
        return to_jsonsafe(result)
    
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
    
    def get_token_holders(self, ref: bytes, limit: int = 100,
                          cursor: Optional[str] = None) -> Dict[str, Any]:
        """
        Get token holders for a specific token.

        Uses the HOLDER_BY_REF secondary index for efficient lookup:
        GR + ref + hashX -> amount
        Supports cursor-based pagination.

        Each holder is resolved to a displayable owner identity via the owner
        index (``GO``): ``address`` (base58, when standard), the full 32-byte
        Electrum ``scripthash``, the internal ``hashX``, and ``amount``.  The
        legacy ``balance`` field is retained as an alias of ``amount``.
        """
        holders = []
        prefix = GlyphDBKeys.HOLDER_BY_REF + ref
        seek = self._decode_cursor(cursor) or prefix
        next_cursor = None

        for key, value in self.db.utxo_db.iterator(prefix=prefix, seek=seek):
            balance = struct.unpack('<Q', value)[0] if len(value) == 8 else 0
            if balance <= 0:
                continue
            if len(holders) >= limit:
                next_cursor = self._encode_cursor(key)
                break
            hashX = key[len(prefix):]
            ident = self._owner_identity(hashX)
            holders.append({
                'address': ident['address'],
                'scripthash': ident['scripthash'],
                'hashX': ident['hashX'],
                'amount': balance,
                'balance': balance,  # legacy alias
            })

        return {
            'ref': ref_to_display(ref),
            'ref_hex': ref.hex(),
            'holders': holders,
            'limit': limit,
            'next_cursor': next_cursor,
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
        
        # Fall back to current_supply as circulating when holder index is empty
        if circulating == 0 and token.current_supply > 0:
            circulating = token.current_supply

        return {
            'ref': ref_to_display(ref),
            'ref_hex': ref.hex(),
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
            'percent_mined': token.percent_mined() if token.total_supply > 0 else None,
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
            'ref': ref_to_display(ref),
            'ref_hex': ref.hex(),
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
            'ref': ref_to_display(ref),
            'ref_hex': ref.hex(),
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
                hashX = key[len(prefix):]
                all_holders.append({'hashX': hashX, 'amount': balance})

        # Sort by balance descending, then resolve only the top N to an identity
        all_holders.sort(key=lambda x: x['amount'], reverse=True)

        # Get token info for context
        token = self.get_token(ref)
        total_supply = token.total_supply if token else 0

        # Resolve + add percentage for the returned page only
        top_holders = []
        for h in all_holders[:limit]:
            ident = self._owner_identity(h['hashX'])
            top_holders.append({
                'address': ident['address'],
                'scripthash': ident['scripthash'],
                'hashX': ident['hashX'],
                'amount': h['amount'],
                'balance': h['amount'],  # legacy alias
                'percentage': round(h['amount'] / total_supply * 100, 4) if total_supply > 0 else 0,
            })

        return {
            'ref': ref_to_display(ref),
            'ref_hex': ref.hex(),
            'name': token.name if token else None,
            'ticker': token.ticker if token else None,
            'total_supply': total_supply,
            'holder_count': len(all_holders),
            'top_holders': top_holders,
        }
    
    # token_type id -> get_stats()['by_type'] bucket name
    _TYPE_TO_STAT = {
        GlyphTokenType.FT: 'FT', GlyphTokenType.NFT: 'NFT',
        GlyphTokenType.DAT: 'DAT', GlyphTokenType.DMINT: 'dMint',
        GlyphTokenType.WAVE: 'WAVE', GlyphTokenType.CONTAINER: 'Container',
        GlyphTokenType.AUTHORITY: 'Authority',
    }

    def _summary_entry(self, token: 'GlyphTokenInfo') -> Dict[str, Any]:
        """Build a list-summary row for a token (with grid image metadata)."""
        entry = {
            'ref': ref_to_display(token.ref),
            'ref_hex': token.ref.hex(),
            'name': token.name,
            'ticker': token.ticker,
            'type': self._type_name(token.token_type),
            'type_id': token.token_type,
            'glyph_version': token.glyph_version,
            'total_supply': token.total_supply,
            'current_supply': token.current_supply,
            'deploy_height': token.deploy_height,
            'is_spent': token.is_spent,
            'icon_ref': token.icon_ref,
            'icon_type': token.icon_type,
        }
        # Include embed/remote for image rendering in the grid
        if token.metadata_hash:
            raw_meta = self.get_metadata(token.metadata_hash)
            if raw_meta and isinstance(raw_meta, dict):
                remote = raw_meta.get('remote') or raw_meta.get('rm')
                embed = raw_meta.get('embed') or raw_meta.get('em') or raw_meta.get('main')
                if remote and isinstance(remote, dict):
                    hs = remote.get('hs')
                    entry['remote'] = {
                        'url': remote.get('u') or remote.get('url'),
                        'type': remote.get('t') or remote.get('type'),
                        'hash': (remote.get('h') or b'').hex() if isinstance(remote.get('h'), (bytes, bytearray)) else None,
                        'hashstamp': bytes(hs).hex() if isinstance(hs, (bytes, bytearray)) else None,
                    }
                elif embed and isinstance(embed, dict):
                    b = embed.get('b')
                    if hasattr(b, 'value'):
                        b = bytes.fromhex(b.value) if isinstance(b.value, str) else b.value
                    entry['embed'] = {
                        'type': embed.get('t') or embed.get('type'),
                        'size': len(b) if isinstance(b, (bytes, bytearray)) else None,
                        'data': bytes(b).hex() if isinstance(b, (bytes, bytearray)) else None,
                    }
        return entry

    def get_all_tokens_summary(self, limit: int = 100, offset: int = 0,
                               token_type: int = None) -> Dict[str, Any]:
        """Summary of all indexed tokens with pagination.

        ``total`` comes from the O(1) GSTAT counter — never a full keyspace scan.
        Only the rows on the requested page are CBOR-decoded and have metadata
        fetched; earlier rows are skipped at the iterator without decoding and we
        stop as soon as the page is full.  Optionally filter by token type (uses
        the BY_TYPE secondary index so the scan is bounded to that type).
        """
        stats = self.get_stats()
        tokens = []

        if token_type is None:
            total = stats.get('total_tokens', 0)
            prefix = GlyphDBKeys.TOKEN
            seen = 0
            for key, value in self.db.utxo_db.iterator(prefix=prefix):
                if len(tokens) >= limit:
                    break
                if seen < offset:
                    seen += 1
                    continue
                seen += 1
                try:
                    tokens.append(self._summary_entry(GlyphTokenInfo.from_bytes(value)))
                except Exception:
                    continue
        else:
            total = stats.get('by_type', {}).get(
                self._TYPE_TO_STAT.get(token_type, 'unknown'), 0)
            prefix = GlyphDBKeys.BY_TYPE + struct.pack('<B', token_type)
            seen = 0
            for key, _ in self.db.utxo_db.iterator(prefix=prefix):
                if len(tokens) >= limit:
                    break
                if seen < offset:
                    seen += 1
                    continue
                seen += 1
                ref = key[len(prefix):]
                token = self.get_token(ref)
                if token:
                    tokens.append(self._summary_entry(token))

        return {
            'total': total,
            'tokens': tokens,
            'limit': limit,
            'offset': offset,
            'filter_type': self._type_name(token_type) if token_type else None,
        }
