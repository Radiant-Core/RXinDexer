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

import base64
import struct
import time
from typing import Optional, Dict, Any, List, Tuple, Set
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash, sha256
from electrumx.lib.util import pack_be_uint32, encode_undo, decode_undo
from electrumx.lib.glyph import GlyphProtocol, to_jsonsafe

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

# ---------------------------------------------------------------------------
# Name lifecycle (term-based registration — see WAVE-Protocol ANNOUNCEMENT.md)
#
# Names are registered for a fixed term and renewed on-chain: any tx that
# spends the name's claim singleton (covenant-proven owner action) and pays
# >= the name's registration price to the protocol treasury extends the term
# to max(current expiry, block time) + WAVE_REGISTRATION_TERM. After expiry
# the owner has WAVE_GRACE_PERIOD in which the name still resolves and can
# still be renewed; past that the name lapses — resolution stops, the name
# reports available, and the next registration supersedes the old canonical
# mapping. Burning the singleton (no re-created output) releases the name
# immediately: a destroyed singleton can never resolve again.
#
# Expiry timestamps are derived from BLOCK TIMES (registration / renewal
# block), never from self-asserted metadata, so wallets cannot mint
# themselves longer terms. Query-time checks use the server wall clock.
# ---------------------------------------------------------------------------
WAVE_REGISTRATION_TERM = 2 * 365 * 24 * 60 * 60   # 2 years, in seconds
WAVE_GRACE_PERIOD = 30 * 24 * 60 * 60             # 30 days
# Default treasury: the address Photonic pays registration fees to.
WAVE_TREASURY_ADDRESS_DEFAULT = '1GrwkQNJfjbEJjH25heszNZLpbZou8nfXG'
# Safety floor for the transition to enforced expiry: no name can lapse
# before this timestamp (2027-01-01T00:00:00Z), so every owner of a
# pre-enforcement name has months of runway to renew after deployment.
# Operators must share this value or their canonical mappings will diverge.
WAVE_EXPIRY_FLOOR_DEFAULT = 1798761600


def wave_name_price(bare_name: str) -> int:
    """Registration/renewal price in photons, tiered by bare-name length.

    Mirrors Photonic's ``calculateNameCost`` (packages/lib/src/wave.ts) —
    keep the two in sync.
    """
    length = len(bare_name)
    if length <= 3:
        return 10_000_000_000   # 100 RXD
    if length == 4:
        return 5_000_000_000    # 50 RXD
    if length == 5:
        return 1_000_000_000    # 10 RXD
    return 500_000_000          # 5 RXD


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
    SINGLETON = b'WSG'  # WSG + singleton_ref(36) -> name_hash (canonical name owning it)
    # WM + ref(36) -> full name, utf-8 ("gatorcoin.rxd"). NAME is one-way
    # (name_hash), so without this there is no way back from a ref to the
    # readable label and reverse_lookup could only ever return bare refs.
    #
    # Prefix choice: no prefix may be a prefix of another. The WR reverse-owner
    # scan is `WR + hashX + ref`, so e.g. `WRN` would be swept up by any owner
    # whose hashX starts with 0x4E ('N'); `WNx` would likewise pollute the `WN`
    # scan in list_names. `WM` is unused and shares no head with WT/WN/WZ/WO/
    # WR/WH/WD/WVU/WSG.
    REF_NAME = b'WM'
    # WX + name_hash -> expiry timestamp (8B LE uint64, unix seconds).
    # Written at registration (block time + term), rewritten by renewals and
    # by a superseding registration; deleted when a name is freed by burn.
    # Absent for legacy pre-lifecycle rows until backfill_lifecycle runs —
    # a missing record means the name cannot lapse.
    EXPIRY = b'WX'
    # WCS + name_hash -> singleton_ref(36) currently authorised to control
    # the name (target updates / renewals / burn-release). SINGLETON (WSG)
    # is one-way and rows survive supersession, so without this a lapsed
    # name's OLD singleton could still steer the NEW canonical registration.
    # Absent (legacy) -> the WSG mapping alone authorises, as before.
    CURRENT_SINGLETON = b'WCS'


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
        # Zone fields (custom x-* records, TXT/MX/NS lists, desc, ...) come
        # verbatim from on-chain CBOR metadata, so a record can carry a
        # non-JSON-native value: cbor2.undefined (CBOR simple value 23), raw
        # bytes, a CBORTag, a Decimal/datetime, or a set. Returning such a value
        # from wave.resolve / the REST zone routes makes the reply
        # un-serialisable, and aiorpcX SILENTLY DROPS a reply it cannot
        # JSON-encode — so the client hangs (the same footgun as the
        # glyph.get_metadata timeout). Coerce the whole dict to JSON-safe form.
        return to_jsonsafe(result)
    
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


# Maximum accepted length (chars) for a WAVE mutable-target payment address.
# Base58 P2PKH/P2SH addresses are ~34 chars; this is a generous upper bound that
# rejects pathological multi-kilobyte "target" payloads before they hit the
# coin's address decoder.
WAVE_MAX_TARGET_LEN = 90


def validate_target_address(coin, target) -> bool:
    """Return True iff ``target`` is a sane base58 payment address for ``coin``.

    A mutable "mod" update carries an attacker-influenced ``attrs.target`` that
    is otherwise stored verbatim and served to wallets. The on-chain covenant
    only proves the singleton holder produced the spend — it does NOT constrain
    the bytes of this field, so we validate before persisting:

    - must be a non-empty ``str`` no longer than ``WAVE_MAX_TARGET_LEN``
    - must base58-decode to a valid address for this coin (correct checksum and
      a recognised P2PKH/P2SH version byte); ``coin.address_to_hashX`` raises
      otherwise.
    """
    if not isinstance(target, str):
        return False
    if not (1 <= len(target) <= WAVE_MAX_TARGET_LEN):
        return False
    try:
        coin.address_to_hashX(target)
    except Exception:
        return False
    return True


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
        # singleton_ref -> name_hash. The NFT singleton ref is a STABLE identity
        # across moves, so recording it at registration lets a later mutable
        # target update (which co-spends the singleton but carries no protocol
        # list) be tied back to the canonical name it belongs to.
        self.singleton_cache: Dict[bytes, bytes] = {}  # singleton_ref(36) -> name_hash
        # ref(36) -> full name utf-8. Written at registration, where the
        # plaintext label is in hand; read back by reverse_lookup.
        self.refname_cache: Dict[bytes, bytes] = {}
        # name_hash -> packed expiry (WX) / controlling singleton ref (WCS).
        self.expiry_cache: Dict[bytes, bytes] = {}
        self.cursingle_cache: Dict[bytes, bytes] = {}
        # Full DB key -> height. Keys deleted this flush window (burn-released
        # names). Readers must treat a key present here as absent from the DB.
        self.delete_cache: Dict[bytes, int] = {}

        self.tree_height: Dict[bytes, int] = {}
        self.name_height: Dict[bytes, int] = {}
        self.zone_height: Dict[bytes, int] = {}
        self.owner_height: Dict[bytes, int] = {}
        self.singleton_height: Dict[bytes, int] = {}
        self.refname_height: Dict[bytes, int] = {}
        self.expiry_height: Dict[bytes, int] = {}
        self.cursingle_height: Dict[bytes, int] = {}

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

        # ---- Name lifecycle (expiry / renewal / release) configuration ----
        # isinstance guards: tests build env from Mock(), whose missing attrs
        # are truthy Mock objects, not usable values — fall back to defaults.
        enforce = getattr(env, 'wave_expiry_enforce', True)
        self.expiry_enforce = enforce if isinstance(enforce, bool) else True
        floor_ts = getattr(env, 'wave_expiry_floor_ts', None)
        self.expiry_floor_ts = (floor_ts if isinstance(floor_ts, int)
                                else WAVE_EXPIRY_FLOOR_DEFAULT)
        treasury = getattr(env, 'wave_treasury_address', None)
        if not isinstance(treasury, str):
            treasury = WAVE_TREASURY_ADDRESS_DEFAULT
        self.treasury_script = self._build_treasury_script(treasury)

        if self.enabled:
            self.logger.info('WAVE name indexing enabled')
            if self.genesis_ref:
                self.logger.info(f'WAVE genesis ref: {genesis_ref_str}')
            else:
                self.logger.warning('WAVE_GENESIS_REF not configured')
            self.logger.info(
                f'WAVE lifecycle: enforce={self.expiry_enforce} '
                f'floor_ts={self.expiry_floor_ts} '
                f'treasury={"ok" if self.treasury_script else "UNAVAILABLE"}'
            )

    # ------------------------------------------------------------------
    # Name lifecycle helpers (expiry / renewal / release)
    # ------------------------------------------------------------------

    def _build_treasury_script(self, address: str) -> Optional[bytes]:
        """P2PKH script bytes for the treasury address, or None (renewals
        then simply never match — logged, not fatal)."""
        try:
            from electrumx.lib.hash import Base58
            from electrumx.lib.script import ScriptPubKey
            raw = Base58.decode_check(address)
            if len(raw) != 21:
                raise ValueError(f'unexpected payload length {len(raw)}')
            return ScriptPubKey.P2PKH_script(raw[1:])
        except Exception as e:
            self.logger.warning(
                f'WAVE treasury address unusable ({address!r}): {e} — '
                f'renewal detection disabled'
            )
            return None

    def _db_get_live(self, key: bytes) -> Optional[bytes]:
        """DB read that honours pending deletions (burn-released names)."""
        if key in self.delete_cache:
            return None
        return self.db.utxo_db.get(key)

    def _get_expiry(self, name_hash: bytes) -> Optional[int]:
        """Stored expiry timestamp for a canonical name, or None (legacy
        row not yet backfilled — such a name cannot lapse)."""
        raw = self.expiry_cache.get(name_hash)
        if raw is None:
            raw = self._db_get_live(WaveDBKeys.EXPIRY + name_hash)
        if raw is None or len(raw) != 8:
            return None
        return struct.unpack('<Q', raw)[0]

    def _effective_expiry(self, name_hash: bytes) -> Optional[int]:
        """Expiry with the transition floor applied: no name lapses before
        WAVE_EXPIRY_FLOOR (owners get runway to renew post-deployment)."""
        expiry = self._get_expiry(name_hash)
        if expiry is None:
            return None
        return max(expiry, self.expiry_floor_ts)

    def _is_lapsed(self, name_hash: bytes, at_ts: int) -> bool:
        """True when the name's grace period has fully passed at ``at_ts``.

        Index-time callers pass the BLOCK time (deterministic across nodes);
        query-time callers pass the wall clock.
        """
        if not self.expiry_enforce or not at_ts:
            return False
        effective = self._effective_expiry(name_hash)
        return (effective is not None
                and at_ts >= effective + WAVE_GRACE_PERIOD)

    def _get_current_singleton(self, name_hash: bytes) -> Optional[bytes]:
        cur = self.cursingle_cache.get(name_hash)
        if cur is None:
            cur = self._db_get_live(WaveDBKeys.CURRENT_SINGLETON + name_hash)
        return cur

    def _singleton_controls_name(self, singleton_ref: bytes,
                                 name_hash: bytes) -> bool:
        """Whether this singleton currently controls the canonical name.

        A superseding registration re-points WCS at its own singleton, so a
        lapsed name's old singleton loses control. Legacy names (no WCS row
        yet) fall back to the WSG mapping alone, exactly as before.
        """
        current = self._get_current_singleton(name_hash)
        if current is None:
            return True
        return current == singleton_ref

    def _set_expiry(self, name_hash: bytes, expiry: int, height: int,
                    eager_undo: bool = False):
        key = WaveDBKeys.EXPIRY + name_hash
        if eager_undo:
            # Snapshot cache-first (two renewals in one flush window must
            # each capture the value immediately before them — same pattern
            # as zone-target undo).
            if key not in self._undo_seen[height]:
                self._undo_seen[height].add(key)
                prev = self.expiry_cache.get(name_hash)
                if prev is None:
                    prev = self.db.utxo_db.get(key)
                self._undo_cache[height].append((key, prev))
        self.expiry_cache[name_hash] = struct.pack('<Q', expiry)
        self.expiry_height[name_hash] = height
        self.delete_cache.pop(key, None)

    def _free_name(self, name_hash: bytes, singleton_ref: bytes, height: int,
                   reason: str):
        """Release a name NOW: its singleton was destroyed, so it can never
        resolve again. Deletes the canonical mapping (and lifecycle rows) so
        the name reports available and the next registration is canonical.
        All deletions are undo-recorded for reorg safety."""
        claim_ref = self._resolve_name_to_ref_raw(name_hash)
        keys = [
            WaveDBKeys.NAME + name_hash,
            WaveDBKeys.EXPIRY + name_hash,
            WaveDBKeys.CURRENT_SINGLETON + name_hash,
            WaveDBKeys.SINGLETON + singleton_ref,
        ]
        for key in keys:
            self._record_undo(height, key)
            self.delete_cache[key] = height
        # Drop any unflushed writes for these rows.
        self.name_cache.pop(name_hash, None)
        self.name_height.pop(name_hash, None)
        self.expiry_cache.pop(name_hash, None)
        self.expiry_height.pop(name_hash, None)
        self.cursingle_cache.pop(name_hash, None)
        self.cursingle_height.pop(name_hash, None)
        self.singleton_cache.pop(singleton_ref, None)
        self.singleton_height.pop(singleton_ref, None)
        if claim_ref:
            self._invalidate_hot_name_by_ref(claim_ref)
        self._pending_heights[name_hash.hex()] = height
        self.logger.info(
            f'WAVE name released ({reason}): '
            f'name_hash={name_hash.hex()[:12]}.. at height {height}'
        )

    def _resolve_name_to_ref_raw(self, name_hash: bytes) -> Optional[bytes]:
        """Canonical ref by name_hash (cache -> live DB)."""
        if name_hash in self.name_cache:
            return self.name_cache[name_hash]
        return self._db_get_live(WaveDBKeys.NAME + name_hash)

    def _maybe_process_renewal(self, tx_hash: bytes, tx, height: int,
                               block_time: int, spent_singleton_refs: set):
        """Extend a name's term when its singleton spend pays the treasury.

        Rule: a tx that spends the name's controlling singleton and pays
        >= wave_name_price(name) to the treasury P2PKH extends the expiry to
        max(current expiry, block time) + WAVE_REGISTRATION_TERM. Payment is
        a shared budget consumed name-by-name in sorted-ref order, so one tx
        cannot renew N names with one fee (and iteration is deterministic
        for reorg replay).
        """
        if (not self.enabled or not spent_singleton_refs
                or not self.treasury_script or not block_time):
            return
        paid = 0
        for out in tx.outputs:
            if getattr(out, 'pk_script', None) == self.treasury_script:
                try:
                    paid += int(getattr(out, 'value', 0) or 0)
                except (TypeError, ValueError):
                    pass
        if paid <= 0:
            return

        for singleton_ref in sorted(spent_singleton_refs):
            name_hash = self._resolve_singleton_to_name(singleton_ref)
            if not name_hash:
                continue
            if not self._singleton_controls_name(singleton_ref, name_hash):
                continue
            claim_ref = self._resolve_name_to_ref_raw(name_hash)
            if not claim_ref:
                continue
            full_name = self._get_ref_name(claim_ref)
            if full_name:
                price = wave_name_price(full_name.split('.', 1)[0])
            else:
                # Label unknown (REF_NAME not backfilled) — charge the
                # cheapest tier rather than making renewal impossible.
                price = wave_name_price('xxxxxx')
                self.logger.warning(
                    f'WAVE renewal price fallback (no label) for '
                    f'name_hash={name_hash.hex()[:12]}..'
                )
            if paid < price:
                self.logger.info(
                    f'WAVE renewal underpaid for name_hash='
                    f'{name_hash.hex()[:12]}..: {paid} < {price} '
                    f'(tx {hash_to_hex_str(tx_hash)})'
                )
                continue
            paid -= price
            current = self._get_expiry(name_hash)
            new_expiry = max(current or 0, block_time) + WAVE_REGISTRATION_TERM
            self._set_expiry(name_hash, new_expiry, height, eager_undo=True)
            self._pending_heights[name_hash.hex()] = height
            self._invalidate_hot_name_by_ref(claim_ref)
            self.logger.info(
                f'WAVE name renewed: name_hash={name_hash.hex()[:12]}.. '
                f'-> expires {new_expiry} at height {height} '
                f'(tx {hash_to_hex_str(tx_hash)})'
            )

    def process_tx(self, tx_hash: bytes, tx, height: int, tx_idx: int,
                   glyph_envelope: Dict[str, Any] = None,
                   output_refs_by_vout: Dict[int, List[Tuple[bytes, int]]] = None,
                   spent_singleton_refs: set = None,
                   block_time: int = 0):
        """
        Process a transaction for WAVE name registration/update.

        A WAVE registration has:
        - Protocol [2, 5, 11] = NFT + Mutable + WAVE
        - 38 outputs (1 claim + 37 branches)
        - Metadata with app.namespace = 'rxd.wave'

        ``block_time`` is the containing block's header timestamp. It drives
        the name-lifecycle decisions (expiry stamping, renewal extension,
        lapsed-name supersession); when 0 (unknown), lifecycle actions are
        skipped and the tx is processed with the legacy semantics.
        """
        if not self.enabled:
            return

        # Owner tracking: a spent singleton mapping to a known name means the name
        # changed hands (transfer / sale / mutable target update). Re-point its
        # owner from the re-created singleton output. Runs BEFORE the
        # glyph-envelope gate so plain transfers — which carry no envelope — are
        # tracked too. Also detects singleton BURNS (spent, not re-created) and
        # releases the name immediately.
        self._maybe_update_owner(
            tx_hash, tx, height, output_refs_by_vout, spent_singleton_refs
        )

        # Renewal: singleton spend + treasury payment extends the term. Runs
        # before the envelope gate too — a plain transfer can carry a renewal.
        self._maybe_process_renewal(
            tx_hash, tx, height, block_time, spent_singleton_refs
        )

        if not glyph_envelope:
            return

        protocols = glyph_envelope.get('protocols', [])
        if GlyphProtocol.GLYPH_WAVE not in protocols:
            # Not a (re)registration — but it may be a mutable TARGET UPDATE.
            # A "mod" op re-points the name by co-spending the NFT singleton; its
            # CBOR payload is just {attrs: {...}} with no protocol list, so it
            # would otherwise be ignored here and the name would resolve forever
            # to its genesis target. Apply the update against the canonical name
            # that owns the spent singleton.
            self._maybe_apply_target_update(
                tx_hash, height, glyph_envelope, spent_singleton_refs
            )
            self.logger.debug(
                f'WAVE non-registration tx {hash_to_hex_str(tx_hash)}: '
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

        # Record ref -> readable name. This is the ONLY back-edge from a ref to
        # its label: NAME is keyed by the one-way name_hash, so reverse_lookup
        # (which starts from refs) has no other way to name its hits. Recorded
        # for duplicates too, so a duplicate hit is still nameable. Written only
        # once the registration is known to be accepted — a rejected one (no
        # resolvable parent) must not leave a name row behind for its outpoint.
        # `parent_name` has already been folded to None for the 'rxd' root.
        self.refname_cache[claim_ref] = f'{name}.{parent_name or "rxd"}'.encode('utf-8')
        self.refname_height[claim_ref] = height

        # Store name -> ref mapping
        name_hash = name_to_hash(name)

        # Check if this name is already registered (first registration wins —
        # unless the existing registration has LAPSED at this block's time, in
        # which case this registration supersedes it as the new canonical).
        existing_ref = self._resolve_name_to_ref(name)
        lapsed = (existing_ref is not None
                  and self._is_lapsed(name_hash, block_time))
        is_duplicate = existing_ref is not None and not lapsed

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
            # First registration - store as canonical. A supersession (lapsed
            # prior registration) takes the same path: the flush-time undo
            # snapshots the previous on-disk WN/WX/WCS values, so a reorg
            # restores the old canonical mapping.
            if lapsed:
                self._invalidate_hot_name_by_ref(existing_ref)
                self.hot_names.pop(normalize_name(name), None)
                self.logger.info(
                    f'WAVE lapsed name "{name}" superseded: '
                    f'{existing_ref.hex()[:16]}.. -> {claim_ref.hex()[:16]}.. '
                    f'at height {height}'
                )
            self.name_cache[name_hash] = claim_ref
            self.name_height[name_hash] = height
            # WN may have been burn-released earlier in this flush window;
            # this registration re-creates it.
            self.delete_cache.pop(WaveDBKeys.NAME + name_hash, None)

            # Stamp the registration term from the BLOCK time (never from
            # self-asserted metadata). block_time == 0 (legacy caller/tests)
            # leaves no expiry row — the name then cannot lapse.
            if block_time:
                self._set_expiry(
                    name_hash, block_time + WAVE_REGISTRATION_TERM, height
                )

            # Record the NFT singleton this registration creates so a later
            # mutable target update (which co-spends the singleton) can be mapped
            # back to this canonical name. Singleton refs are a stable identity,
            # so this mapping survives moves with no re-recording.
            #
            # Scope this to the CLAIM output (vout 0) only. The other 37 branch
            # outputs may legitimately carry unrelated singletons; recording them
            # all would let a spend of any of those branch singletons hijack this
            # name's target update. Only the claim-output singleton is the name's
            # own NFT.
            if output_refs_by_vout:
                for ref_bytes, ref_type in output_refs_by_vout.get(0, ()):
                    if ref_type != 1:  # only singletons (NFTs)
                        continue
                    # First-writer guard: never overwrite an existing
                    # singleton->name mapping. A singleton can only ever belong
                    # to one canonical name; silently re-pointing it would let a
                    # second registration steal control of the first's updates.
                    if (ref_bytes in self.singleton_cache
                            or self._db_get_live(
                                WaveDBKeys.SINGLETON + ref_bytes) is not None):
                        self.logger.warning(
                            f'WAVE singleton {ref_bytes.hex()[:16]}.. already '
                            f'mapped; refusing to remap to "{name}"'
                        )
                        continue
                    self.singleton_cache[ref_bytes] = name_hash
                    self.singleton_height[ref_bytes] = height
                    # This singleton now CONTROLS the name (target updates,
                    # renewals, release). A supersession re-points this, so a
                    # lapsed name's old singleton loses control.
                    self.cursingle_cache[name_hash] = ref_bytes
                    self.cursingle_height[name_hash] = height
                    self.delete_cache.pop(
                        WaveDBKeys.CURRENT_SINGLETON + name_hash, None)
        
        # Store zone records (for both canonical and duplicates)
        zone = WaveZoneRecords.from_metadata(metadata)
        # The genesis target is attacker-controlled; null it out if it isn't a
        # valid payment address so an invalid genesis target never enters
        # zone_cache and gets served to wallets. Other zone fields are untouched.
        if zone.address is not None and not validate_target_address(
                self.env.coin, zone.address):
            self.logger.warning(
                f'WAVE genesis target rejected for "{name}": '
                f'invalid address {zone.address!r} at height {height}'
            )
            zone.address = None
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
        
        # Store owner = base-address hashX of the claim output (the embedded
        # P2PKH), so a name is queryable by the holder's normal address hashX and
        # the value stays stable across plain<->auth singleton forms (a target /
        # state update wraps the same p2pkh in an auth covenant). Kept current on
        # every move by _maybe_update_owner.
        claim_script = tx.outputs[0].pk_script
        self.owner_cache[claim_ref] = self._owner_hashX_from_script(claim_script)
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
        return self._resolve_name_to_ref_raw(name_to_hash(name))

    def _resolve_singleton_to_name(self, singleton_ref: bytes) -> Optional[bytes]:
        """Resolve an NFT singleton ref to the canonical name_hash that owns it."""
        if singleton_ref in self.singleton_cache:
            return self.singleton_cache[singleton_ref]
        return self._db_get_live(WaveDBKeys.SINGLETON + singleton_ref)

    def _invalidate_hot_name_by_ref(self, ref: bytes):
        """Drop any hot-cache entries pointing at this claim ref so the next
        resolve() rebuilds from the freshly-updated zone records."""
        stale = [n for n, info in self.hot_names.items()
                 if getattr(info, 'ref', None) == ref]
        for n in stale:
            self.hot_names.pop(n, None)

    def _maybe_apply_target_update(self, tx_hash: bytes, height: int,
                                   glyph_envelope: Dict[str, Any],
                                   spent_singleton_refs: set):
        """Apply a mutable "mod" target update to the canonical name.

        The tx co-spends the name's NFT singleton (covenant-enforced: only the
        singleton holder can produce this), so a spent singleton that maps to a
        known canonical name authorises updating that name's zone target.
        """
        if not self.enabled or not spent_singleton_refs:
            return

        metadata = glyph_envelope.get('metadata') or {}
        attrs = metadata.get('attrs') or {}
        # Only address-type targets are meaningful for resolution.
        if attrs.get('target_type', 'address') != 'address':
            return
        new_target = attrs.get('target')
        if not new_target:
            return

        # The covenant proves WHO spent the singleton, not WHAT the target is.
        # Reject malformed/oversized/non-address targets before they enter the
        # zone cache and get served to wallets as a resolved payment address.
        if not validate_target_address(self.env.coin, new_target):
            self.logger.warning(
                f'WAVE target update rejected: invalid target {new_target!r} '
                f'at height {height} (tx {hash_to_hex_str(tx_hash)})'
            )
            return

        for singleton_ref in spent_singleton_refs:
            name_hash = self._resolve_singleton_to_name(singleton_ref)
            if not name_hash:
                continue
            # A superseded registration's old singleton must not steer the
            # new canonical name.
            if not self._singleton_controls_name(singleton_ref, name_hash):
                continue
            claim_ref = self._resolve_name_to_ref_raw(name_hash)
            if not claim_ref:
                continue

            # Merge onto the existing zone so unrelated records are preserved.
            zone = self._get_zone_records(claim_ref) or WaveZoneRecords()
            if zone.address == new_target:
                continue  # already current
            zone.address = new_target
            if HAS_CBOR:
                zone_key = WaveDBKeys.ZONE + claim_ref
                # Record undo EAGERLY, keyed at THIS update's height, snapshotting
                # the prior value from cache-then-DB — BEFORE we overwrite the
                # cache. The flush-time undo only snapshots the on-disk value and
                # records once per ref, so two updates to one ref in a single
                # flush (H1->A then H2->B) would otherwise collapse into a single
                # undo entry at H2 and lose the intermediate A state. Per-height
                # eager undo gives backup(H2) -> A and backup(H1) -> original.
                # _record_undo_from_cache dedups per (height, key), so the later
                # flush-time _record_undo(H2, ...) becomes a no-op and never
                # clobbers the snapshot taken here.
                self._record_undo_from_cache(height, zone_key, claim_ref)
                self.zone_cache[claim_ref] = cbor2.dumps(zone.to_dict())
                self.zone_height[claim_ref] = height
                self._pending_heights[claim_ref.hex()] = height
            self._invalidate_hot_name_by_ref(claim_ref)
            self.logger.info(
                f'WAVE target updated via mod: '
                f'name_hash={name_hash.hex()[:12]}.. -> {new_target!r} '
                f'at height {height} (tx {hash_to_hex_str(tx_hash)})'
            )

    def _owner_hashX_from_script(self, script: bytes) -> bytes:
        """Owner identity for a WAVE name = the base-address hashX (the embedded
        P2PKH) of the singleton's holding script.

        Using the base address (not zero_refs of the whole script) means the
        value is the holder's ordinary address hashX — so wave.reverse_lookup is
        queryable by address — and it is identical whether the singleton rests in
        a plain nftScript (transfer) or an auth covenant (after a target/state
        update). Falls back to zero_refs for exotic scripts with no P2PKH.
        """
        from electrumx.lib.script import Script
        try:
            return self.env.coin.hashX_from_script(
                Script.base_locking_script(script)
            )
        except Exception:
            return self.env.coin.hashX_from_script(Script.zero_refs(script))

    def _maybe_update_owner(self, tx_hash: bytes, tx, height: int,
                            output_refs_by_vout: Dict[int, Any],
                            spent_singleton_refs: set):
        """Re-point a name's owner when its singleton moves (transfer / sale /
        target update).

        The owner is otherwise written only at registration, so without this a
        name's ownership — and wave.reverse_lookup — stays frozen on the genesis
        holder even after the name changes hands. A spent singleton that maps to
        a known canonical name (covenant-enforced: only the holder can spend it)
        identifies the name; the re-created singleton output carries the same ref
        and the new holder's address. Owner is keyed by the canonical claim_ref.

        Stale reverse-index entries from the previous owner are NOT deleted here
        (that would need reorg-fragile batch deletes); reverse_lookup filters
        them out by comparing each hit against the current OWNER record.
        """
        if not self.enabled or not spent_singleton_refs:
            return
        # None means the caller did not compute output refs at all (legacy
        # callers/tests) — "singleton absent from outputs" then proves
        # nothing, so burn-release must not fire. The block processor always
        # passes a dict (possibly empty), so real burns still release.
        refs_known = output_refs_by_vout is not None
        output_refs_by_vout = output_refs_by_vout or {}

        for singleton_ref in sorted(spent_singleton_refs):
            name_hash = self._resolve_singleton_to_name(singleton_ref)
            if not name_hash:
                continue
            claim_ref = self._resolve_name_to_ref_raw(name_hash)
            if not claim_ref:
                continue

            # Find the re-created singleton output (same ref) → new owner address.
            recreated = False
            new_owner = None
            for vout, refs in output_refs_by_vout.items():
                if any(rb == singleton_ref and rt == 1 for (rb, rt) in refs):
                    recreated = True
                    try:
                        new_owner = self._owner_hashX_from_script(
                            tx.outputs[vout].pk_script
                        )
                    except Exception:
                        new_owner = None
                    break

            if not recreated:
                # Singleton DESTROYED (burn / melt / reclaim). The node's
                # singleton rule means it can never reappear, so the name can
                # never resolve again — release it immediately so it returns
                # to the available pool. Only the CONTROLLING singleton frees
                # the name (an orphaned mapping from a superseded registration
                # must not release the new canonical), and only when the
                # caller genuinely computed output refs (refs_known).
                if (refs_known and self.expiry_enforce
                        and self._singleton_controls_name(
                            singleton_ref, name_hash)):
                    self._free_name(name_hash, singleton_ref, height, 'burn')
                continue

            # Re-created but the holding script defied owner extraction —
            # leave the owner as-is rather than guess.
            if new_owner is None:
                continue
            if self._get_owner(claim_ref) == new_owner:
                continue  # unchanged

            self.owner_cache[claim_ref] = new_owner
            self.owner_height[claim_ref] = height
            self._pending_heights[claim_ref.hex()] = height
            self.logger.info(
                f'WAVE owner updated: name_hash={name_hash.hex()[:12]}.. -> '
                f'{new_owner.hex()} at height {height} '
                f'(tx {hash_to_hex_str(tx_hash)})'
            )

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

    def _record_undo_from_cache(self, height: int, key: bytes,
                                cache_ref: bytes):
        """Like ``_record_undo`` but snapshots the prior value from the in-memory
        zone cache first, falling back to the on-disk value.

        Used for eager per-update undo of zone targets: when several updates to
        the same ref land in one flush, each must capture the value as it stood
        *immediately before that update* (which, for the 2nd+ update, lives only
        in ``zone_cache`` and has not yet been flushed to disk). Dedups per
        (height, key) so a later flush-time ``_record_undo`` for the same key at
        the same height is a no-op and cannot overwrite this snapshot.
        """
        if not self.enabled:
            return
        if key in self._undo_seen[height]:
            return
        self._undo_seen[height].add(key)
        if cache_ref in self.zone_cache:
            prev_value = self.zone_cache[cache_ref]
        else:
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
            + len(self.singleton_cache) * 190
            + len(self.refname_cache) * 190
            + len(self.expiry_cache) * 190
            + len(self.cursingle_cache) * 190
            + len(self.delete_cache) * 140
            + len(self.tree_height) * 140
            + len(self.name_height) * 140
            + len(self.zone_height) * 140
            + len(self.owner_height) * 140
            + len(self.singleton_height) * 140
            + len(self.refname_height) * 140
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

        # Flush singleton -> name_hash mappings (for target-update lookups)
        for singleton_ref, name_hash in self.singleton_cache.items():
            height = self.singleton_height.get(singleton_ref)
            key = WaveDBKeys.SINGLETON + singleton_ref
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, name_hash)

        # Flush ref -> readable name (backs reverse_lookup's `name`/`full_name`).
        # Backfilled rows carry no height and so record no undo: they restate
        # historical registrations that no reorg in the window can unwind.
        for ref, full_name in self.refname_cache.items():
            height = self.refname_height.get(ref)
            key = WaveDBKeys.REF_NAME + ref
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, full_name)

        # Flush expiry rows (registration stamp / renewals / backfill).
        for name_hash, packed in self.expiry_cache.items():
            height = self.expiry_height.get(name_hash)
            key = WaveDBKeys.EXPIRY + name_hash
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, packed)

        # Flush controlling-singleton rows.
        for name_hash, singleton_ref in self.cursingle_cache.items():
            height = self.cursingle_height.get(name_hash)
            key = WaveDBKeys.CURRENT_SINGLETON + name_hash
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, singleton_ref)

        # Apply deletions (burn-released names) AFTER the put loops — the
        # release already dropped its rows from the write caches, and the
        # eager undo recorded at release time restores them on reorg.
        for key, height in self.delete_cache.items():
            self._record_undo(height, key)
            batch.delete(key)

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
        self.singleton_cache.clear()
        self.refname_cache.clear()
        self.expiry_cache.clear()
        self.cursingle_cache.clear()
        self.delete_cache.clear()
        self.tree_height.clear()
        self.name_height.clear()
        self.zone_height.clear()
        self.owner_height.clear()
        self.singleton_height.clear()
        self.refname_height.clear()
        self.expiry_height.clear()
        self.cursingle_height.clear()
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

            # ...and the back-edge ref -> name, so reverse_lookup can name it.
            self.refname_cache[claim_ref] = (raw_name or name).encode('utf-8')

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

    def backfill_ref_names(self, glyph_index) -> int:
        """Backfill the REF_NAME (ref -> readable name) index for existing DBs.

        REF_NAME was added after mainnet had already indexed thousands of names,
        so a DB synced by an older build has none of these rows and
        ``reverse_lookup`` would keep returning name-less hits until a full
        reindex. This rebuilds them in place from the Glyph token DB — no daemon
        rescan — and is a no-op once any REF_NAME row exists.

        Two ref forms are written per token, because the two ways a name can
        enter this index disagree on which outpoint is the ref:

          * ``deploy_txid:0`` — what ``process_tx`` records (``claim_ref``, the
            *reveal* transaction's claim output). This is what a normally-synced
            mainnet DB holds, and the form ``reverse_lookup`` returns.
          * the Glyph token's own ref — what ``backfill_from_glyph_db`` records
            (the commit/singleton outpoint).

        Both map to the same name, so writing both makes the index correct
        regardless of which path populated the DB. (That the two paths key the
        same name differently is a pre-existing inconsistency, noted here rather
        than changed: altering a name's canonical ref would break every client
        holding one.)
        """
        if not self.enabled:
            return 0

        if self._count_db_prefix(WaveDBKeys.REF_NAME, limit=1) > 0 or self.refname_cache:
            return 0

        try:
            from electrumx.server.glyph_index import GlyphDBKeys, GlyphTokenInfo
        except ImportError:
            self.logger.warning('Cannot import GlyphIndex — REF_NAME backfill aborted')
            return 0

        self.logger.info('Backfilling WAVE REF_NAME index from Glyph token DB...')
        count = 0
        for key, value in self.db.utxo_db.iterator(prefix=GlyphDBKeys.TOKEN):
            try:
                token = GlyphTokenInfo.from_bytes(value)
            except Exception:
                continue
            if GlyphProtocol.GLYPH_WAVE not in token.protocols:
                continue
            if not token.name:
                continue

            token_ref = key[len(GlyphDBKeys.TOKEN):]
            if len(token_ref) < 36:
                continue
            encoded = token.name.encode('utf-8')

            # No heights recorded: these restate historical registrations, so
            # they need no reorg undo (see flush()).
            self.refname_cache[token_ref[:36]] = encoded
            if token.deploy_txid:
                self.refname_cache[token.deploy_txid + struct.pack('<I', 0)] = encoded
            count += 1

        self.logger.info(f'WAVE REF_NAME backfill: named {count} tokens')
        return count

    def backfill_lifecycle(self, glyph_index) -> int:
        """One-time backfill of the lifecycle rows (WX expiry, WCS control)
        for names indexed before term enforcement existed.

        - WCS: inverted from the existing SINGLETON (WSG) rows — at
          registration each claim singleton was mapped to its name, so the
          inverse identifies the controlling singleton per name.
        - WX: registration block time (from the glyph token's deploy height →
          stored header timestamp) + WAVE_REGISTRATION_TERM. Names whose
          height or header can't be recovered get the transition floor, so
          they still enter the lifecycle with months of renewal runway.

        No-op once any WX row exists. Rows carry no height and record no
        undo (they restate historical registrations, like backfill_ref_names).
        Returns the number of names stamped.
        """
        if not self.enabled:
            return 0
        if (self._count_db_prefix(WaveDBKeys.EXPIRY, limit=1) > 0
                or self.expiry_cache):
            return 0

        # ---- WCS from WSG (skip names that already have one) ----
        for key, name_hash in self.db.utxo_db.iterator(
                prefix=WaveDBKeys.SINGLETON):
            singleton_ref = key[len(WaveDBKeys.SINGLETON):]
            if len(singleton_ref) != 36 or len(name_hash) != 16:
                continue
            if (name_hash in self.cursingle_cache
                    or self.db.utxo_db.get(
                        WaveDBKeys.CURRENT_SINGLETON + name_hash) is not None):
                continue
            self.cursingle_cache[name_hash] = singleton_ref

        # ---- deploy_txid -> height map from the glyph token DB ----
        height_by_txid: Dict[bytes, int] = {}
        try:
            from electrumx.server.glyph_index import GlyphDBKeys, GlyphTokenInfo
            for key, value in self.db.utxo_db.iterator(
                    prefix=GlyphDBKeys.TOKEN):
                try:
                    token = GlyphTokenInfo.from_bytes(value)
                except Exception:
                    continue
                if GlyphProtocol.GLYPH_WAVE not in token.protocols:
                    continue
                if not token.deploy_height:
                    continue
                if token.deploy_txid:
                    height_by_txid[token.deploy_txid] = token.deploy_height
                token_ref = key[len(GlyphDBKeys.TOKEN):]
                if len(token_ref) >= 32:
                    height_by_txid.setdefault(
                        token_ref[:32], token.deploy_height)
        except ImportError:
            self.logger.warning(
                'WAVE lifecycle backfill: GlyphIndex unavailable — '
                'falling back to the floor for all names')

        def _block_time(height: int) -> Optional[int]:
            headers_file = getattr(self.db, 'headers_file', None)
            if headers_file is None or height <= 0:
                return None
            try:
                raw = headers_file.read(height * 80, 80)
                if len(raw) != 80:
                    return None
                return struct.unpack_from('<I', raw, 68)[0]
            except Exception:
                return None

        count = 0
        floored = 0
        for key, ref_bytes in self.db.utxo_db.iterator(
                prefix=WaveDBKeys.NAME):
            name_hash = key[len(WaveDBKeys.NAME):]
            if len(name_hash) != 16 or len(ref_bytes) < 36:
                continue
            height = height_by_txid.get(ref_bytes[:32])
            ts = _block_time(height) if height else None
            if ts:
                expiry = ts + WAVE_REGISTRATION_TERM
            else:
                # Unknown registration time — floor keeps the name safe
                # until the transition window ends, then normal grace rules.
                expiry = self.expiry_floor_ts
                floored += 1
            self.expiry_cache[name_hash] = struct.pack('<Q', expiry)
            count += 1

        if count:
            self.logger.info(
                f'WAVE lifecycle backfill: stamped {count} names '
                f'({floored} floored), {len(self.cursingle_cache)} control rows'
            )
        return count

    # ========================================================================
    # Query Methods (API)
    # ========================================================================

    def resolve(self, name: str, include_duplicates: bool = False,
                now: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Resolve a WAVE name to its zone records and owner.

        Always returns the CANONICAL registration. Duplicate registrations
        are tracked but not returned unless requested.

        Lifecycle: a name past its expiry + grace period is LAPSED and no
        longer resolves (returns None, as if unregistered). Within the grace
        window it still resolves, with ``status: 'grace'``. Active/grace
        results carry ``expires`` (unix seconds, floor-adjusted).

        Args:
            name: The WAVE name to resolve
            include_duplicates: If True, include list of duplicate registrations
            now: Override the wall clock (tests)

        Returns None if name is not registered (or has lapsed).
        """
        valid, error = validate_wave_name(name)
        if not valid:
            return {'error': error}

        now = int(time.time()) if now is None else now
        name_hash = name_to_hash(name)
        normalized = normalize_name(name)
        if self._is_lapsed(name_hash, now):
            # Stale hot entry must not resurrect a lapsed name.
            self.hot_names.pop(normalized, None)
            return None

        # Check hot cache
        if normalized in self.hot_names:
            info = self.hot_names[normalized]
            result = self._name_info_to_dict(info)
            self._append_lifecycle(result, name_hash, now)
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
        self._append_lifecycle(result, name_hash, now)

        if include_duplicates:
            result['duplicates'] = self._get_duplicate_registrations(name)

        return result

    def _append_lifecycle(self, result: Dict[str, Any], name_hash: bytes,
                          now: int):
        """Attach expires/status to a resolve() result. Legacy names with no
        expiry row get no lifecycle fields (they cannot lapse)."""
        effective = self._effective_expiry(name_hash)
        if effective is None:
            return
        result['expires'] = effective
        if self.expiry_enforce and now >= effective:
            result['status'] = 'grace'
            result['grace_until'] = effective + WAVE_GRACE_PERIOD
        else:
            result['status'] = 'active'
    
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
    
    def check_available(self, name: str,
                        now: Optional[int] = None) -> Dict[str, Any]:
        """Check if a WAVE name is available for registration.

        A LAPSED name (expiry + grace passed without renewal) is available:
        the next registration supersedes the old canonical mapping. The
        response then carries ``expired: True`` and the lapsed registration's
        ref for transparency.
        """
        valid, error = validate_wave_name(name)
        if not valid:
            return {'available': False, 'error': error}

        now = int(time.time()) if now is None else now
        ref = self._resolve_name_to_ref(name)

        if ref:
            name_hash = name_to_hash(name)
            if self._is_lapsed(name_hash, now):
                return {
                    'available': True,
                    'name': normalize_name(name),
                    'expired': True,
                    'previous_ref': self._format_ref(ref),
                }
            result = {
                'available': False,
                'ref': self._format_ref(ref),
                'name': normalize_name(name),
            }
            self._append_lifecycle(result, name_hash, now)
            return result
        else:
            return {
                'available': True,
                'name': normalize_name(name),
            }
    
    def get_subdomains(self, parent_name: str, limit: int = 100,
                       offset: int = 0,
                       cursor: Optional[str] = None,
                       _use_cursor: bool = False):
        """Get subdomains of a parent name.

        The underlying loop is bounded (37 char slots) and the index keys
        are deterministic per char_idx, so cursor support here is mostly
        for API consistency with the other paginated methods. The cursor
        encodes the next char_idx to scan from.

        Legacy shape: ``List[Dict]``.
        Cursor shape: ``{entries, next_cursor, has_more}``.
        See docs/pagination-cursors.md.
        """
        parent_ref = self._resolve_name_to_ref(parent_name)

        if _use_cursor:
            entries: List[Dict[str, Any]] = []
            next_cursor = None
            if not parent_ref:
                return {'entries': entries, 'next_cursor': None, 'has_more': False}
            start_char = 0
            if cursor:
                try:
                    # URL-safe first; tolerate legacy standard-alphabet cursors
                    # and a '+' that a query-string parser turned into a space.
                    decoded = base64.b64decode(
                        cursor.replace(' ', '+').replace('-', '+').replace('_', '/'))
                    if len(decoded) == 1:
                        start_char = decoded[0]
                except Exception:
                    start_char = 0
            for char_idx in range(start_char, 37):
                output_idx = char_idx + 1
                tree_key = WaveDBKeys.TREE + parent_ref + struct.pack('<B', output_idx)
                child_ref = self.db.utxo_db.get(tree_key)
                if not child_ref:
                    continue
                if len(entries) >= limit:
                    next_cursor = base64.urlsafe_b64encode(bytes([char_idx])).decode()
                    break
                entries.append({
                    'char': index_to_char(char_idx),
                    'ref': self._format_ref(child_ref),
                })
            return {
                'entries': entries,
                'next_cursor': next_cursor,
                'has_more': next_cursor is not None,
            }

        results: List[Dict[str, Any]] = []
        if not parent_ref:
            return results

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
    
    def reverse_lookup(self, scripthash: bytes, limit: int = 100,
                       now: Optional[int] = None) -> List[Dict[str, Any]]:
        """Find WAVE names owned by an address.

        Owners are indexed by the 11-byte ElectrumX hashX (sha256(scriptPubKey)
        first 11 bytes, NOT reversed). Callers may pass that hashX directly, or a
        32-byte Electrum scripthash (sha256(script) reversed, as wallets compute),
        which we convert (reverse, then first 11 bytes). Without this, a 32-byte
        value never prefix-matches the 11-byte owner keys and the lookup returns
        nothing. Index: WR + hashX + ref -> ''.
        """
        from electrumx.lib.hash import HASHX_LEN
        now = int(time.time()) if now is None else now
        if len(scripthash) == 32:
            owner_key = scripthash[::-1][:HASHX_LEN]
        else:
            owner_key = scripthash[:HASHX_LEN]

        results = []
        prefix = WaveDBKeys.REVERSE_OWNER + owner_key

        for key, _value in self.db.utxo_db.iterator(prefix=prefix):
            if len(results) >= limit:
                break

            ref = key[len(prefix):]

            # Filter stale entries. When a name moves we update OWNER (authoritative)
            # and add a fresh reverse entry, but the previous owner's reverse entry
            # is left in place (deleting it would need reorg-fragile batch deletes).
            # So only return refs whose CURRENT owner still matches this hashX.
            owner_sh = self._get_owner(ref)
            if owner_sh != owner_key:
                continue

            entry = {'ref': self._format_ref(ref)}
            # Name the hit here rather than leaving it to callers. Enrichment
            # used to be attempted in the RPC/REST layers via
            # glyph_index.get_token_by_ref_str(ref), which can never work: a
            # WAVE ref is the *reveal* outpoint (reveal_txid:0) while a Glyph
            # token is keyed by its own commit/singleton ref, so that lookup
            # always missed and every hit came back name-less.
            full_name = self._get_ref_name(ref)
            if full_name:
                entry['name'] = full_name.split('.', 1)[0]
                entry['full_name'] = full_name
                # Lifecycle status so owners can see names needing renewal.
                # Lapsed names stay listed (flagged 'expired') — the owner
                # still holds the token even though resolution has stopped.
                # WX is keyed by name_hash (shared with any superseding
                # registration), so only attach it when THIS ref is still
                # the canonical holder; otherwise flag the supersession.
                nh = name_to_hash(entry['name'])
                if self._resolve_name_to_ref_raw(nh) == ref:
                    effective = self._effective_expiry(nh)
                    if effective is not None:
                        entry['expires'] = effective
                        if not self.expiry_enforce or now < effective:
                            entry['status'] = 'active'
                        elif now < effective + WAVE_GRACE_PERIOD:
                            entry['status'] = 'grace'
                        else:
                            entry['status'] = 'expired'
                else:
                    entry['status'] = 'superseded'
            zone = self._get_zone_records(ref)
            if zone:
                entry['zone'] = zone.to_dict()
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
    
    def _get_ref_name(self, ref: bytes) -> Optional[str]:
        """Resolve a ref to its full readable name (e.g. ``gatorcoin.rxd``).

        Returns ``None`` for refs registered before the REF_NAME index existed
        and not yet backfilled — callers must treat the name as optional.
        """
        raw = self.refname_cache.get(ref)
        if raw is None:
            raw = self.db.utxo_db.get(WaveDBKeys.REF_NAME + ref)
        if not raw:
            return None
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
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
            'lifecycle': {
                'expiry_enforce': self.expiry_enforce,
                'registration_term': WAVE_REGISTRATION_TERM,
                'grace_period': WAVE_GRACE_PERIOD,
                'expiry_floor_ts': self.expiry_floor_ts,
                'renewals_enabled': self.treasury_script is not None,
            },
        }


# API method registration
WAVE_METHODS = {
    'wave.resolve': 'wave_resolve',
    'wave.check_available': 'wave_check_available',
    'wave.get_subdomains': 'wave_get_subdomains',
    'wave.reverse_lookup': 'wave_reverse_lookup',
}
