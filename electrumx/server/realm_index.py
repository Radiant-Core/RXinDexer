"""
Realm Index for RXinDexer — on-chain GlyphGalaxy realm directory.

A "realm" is a GlyphGalaxy world / arena / experience minted as a `realm_v1`
Glyph NFT (an ordinary Glyph NFT whose CBOR payload carries
``app.schema == "realm_v1"`` and the canonical realm fields under
``app.data.realm`` + immutable creator/royalty under ``app.data.base`` — see the
game's packages/sdk/src/realm.ts buildRealmPayload).

This module mirrors wave_index.py (a named, queryable, owned Glyph record): it
recognises realm_v1 reveals during block processing, extracts the discovery
fields, and stores them keyed by the realm's stable slug id so the game server's
directory can read them from chain (persistent across restarts, shared across
servers) instead of an in-memory map.

OWNERSHIP NUANCE (the "tradeable" property): the realm's CURRENT OWNER — who may
edit it — is the CURRENT HOLDER of the realm NFT, resolved live from the Glyph
holder index (glyph_index.get_token_holders on the singleton ref), NOT the
immutable ``owner`` field baked into the mint payload. Transfer the NFT → the
holder index reflects the new address → realm.* reports the new owner → edit
rights follow the token. The discovery fields (name/kind/seed/spawn/desc/creator/
royalty) come from the immutable payload; ownership comes from the holder index.

Realms are IMMUTABLE NFTs today (no name/target "mod" path), so unlike wave names
the cached discovery record never needs re-parsing on a mutable update.
"""

import struct
from typing import Optional, Dict, Any, List, Tuple
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, sha256
from electrumx.lib.util import pack_be_uint32, encode_undo, decode_undo

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False


# The Glyph app schema that identifies a realm NFT (kept in sync with the game's
# packages/protocol/src/realm.ts REALM_SCHEMA and packages/sdk/src/realm.ts).
REALM_SCHEMA = 'realm_v1'
REALM_NAMESPACE = 'rxd.game'
REALM_KINDS = ('world', 'arena', 'experience')

# Mirror the game's protocol bounds (packages/protocol/src/realm.ts) so the
# indexer rejects the same junk at its trust boundary.
MAX_REALM_ID_LEN = 48
MAX_REALM_NAME_LEN = 64
MAX_REALM_DESC_LEN = 280
MAX_ROYALTY_BPS = 5000
# URL-safe slug: 2-48 chars, [a-z0-9_-], starting alphanumeric.
import re as _re
_REALM_ID_RE = _re.compile(r'^[a-z0-9][a-z0-9_-]*$')


class RealmDBKeys:
    # NB: no prefix may be a prefix of another — a RocksDB prefix scan for REALM
    # must NOT also match UNDO/SINGLETON keys (that would feed undo blobs into the
    # realm decoder). REALM/SINGLETON/UNDO are all distinct 2-byte prefixes.
    REALM = b'RM'       # RM + id_hash(16) -> realm record (CBOR)
    SINGLETON = b'RS'   # RS + singleton_ref(36) -> id_hash(16) (first-writer wins)
    UNDO = b'RU'        # RU + height(be) -> encoded undo entries


def realm_id_hash(realm_id: str) -> bytes:
    """Stable 16-byte key for a realm id (lowercased)."""
    return sha256(realm_id.lower().encode('utf-8'))[:16]


def is_valid_realm_id(realm_id: Any) -> bool:
    return (
        isinstance(realm_id, str)
        and 2 <= len(realm_id) <= MAX_REALM_ID_LEN
        and bool(_REALM_ID_RE.match(realm_id))
    )


def _clean_str(v: Any, max_len: int) -> Optional[str]:
    """Coerce to a trimmed, control-char-stripped string bounded to max_len."""
    if not isinstance(v, str):
        return None
    s = ''.join(ch for ch in v if ch >= ' ' and ch != '\x7f')
    return s[:max_len] if s else None


def extract_realm_fields(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse + validate the realm discovery fields from a decoded Glyph payload.

    Returns a normalized dict (id/name/kind/seed/spawn/owner + optional
    desc/creator/royalty_bps) or ``None`` if this is not a well-formed realm_v1
    payload. Pure — no DB, no I/O. The ``owner`` here is the IMMUTABLE payload
    owner (the minter); current-holder ownership is resolved separately.
    """
    if not isinstance(metadata, dict):
        return None
    app = metadata.get('app')
    if not isinstance(app, dict):
        return None
    if app.get('schema') != REALM_SCHEMA:
        return None
    # Namespace is a soft gate: enforce it when present (defends against a stray
    # realm_v1 schema under an unrelated namespace) but don't hard-require it.
    ns = app.get('namespace')
    if ns is not None and ns != REALM_NAMESPACE:
        return None
    data = app.get('data')
    if not isinstance(data, dict):
        return None
    realm = data.get('realm')
    base = data.get('base') if isinstance(data.get('base'), dict) else {}
    if not isinstance(realm, dict):
        return None

    realm_id = realm.get('id')
    if not is_valid_realm_id(realm_id):
        return None
    kind = realm.get('kind')
    if kind not in REALM_KINDS:
        return None
    seed = realm.get('seed')
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 or seed > 0xffffffff:
        return None
    spawn = realm.get('spawn')
    if (not isinstance(spawn, (list, tuple)) or len(spawn) != 3
            or not all(isinstance(n, int) and not isinstance(n, bool) for n in spawn)):
        return None
    owner = realm.get('owner')
    if not isinstance(owner, str) or not owner:
        return None

    name = _clean_str(realm.get('name'), MAX_REALM_NAME_LEN) \
        or _clean_str(metadata.get('name'), MAX_REALM_NAME_LEN) or realm_id

    out: Dict[str, Any] = {
        'id': realm_id,
        'name': name,
        'kind': kind,
        'seed': int(seed),
        'spawn': [int(spawn[0]), int(spawn[1]), int(spawn[2])],
        'owner': owner,
    }
    desc = _clean_str(realm.get('desc'), MAX_REALM_DESC_LEN)
    if desc:
        out['desc'] = desc
    creator = base.get('creator')
    if isinstance(creator, str) and creator:
        out['creator'] = creator
    royalty = base.get('royalty_bps')
    if isinstance(royalty, int) and not isinstance(royalty, bool) and 0 <= royalty <= MAX_ROYALTY_BPS:
        out['royalty_bps'] = int(royalty)
    return out


class RealmIndex:
    """Indexes realm_v1 Glyph NFT mints and answers realm.* queries.

    Discovery fields are cached from the immutable payload at mint; the current
    owner (edit rights) is resolved live from the Glyph holder index.
    """

    def __init__(self, db, env, glyph_index=None):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        # The Glyph index is the source of truth for "who currently holds this
        # NFT ref". Set by the block processor (it owns both indexes); also
        # available at query time since the session resolves the same bp objects.
        self.glyph_index = glyph_index
        self.enabled = getattr(env, 'realm_index', True)

        # Unflushed write caches.
        self.realm_cache: Dict[bytes, bytes] = {}      # id_hash -> record CBOR
        self.realm_height: Dict[bytes, int] = {}
        self.singleton_cache: Dict[bytes, bytes] = {}  # singleton_ref(36) -> id_hash
        self.singleton_height: Dict[bytes, int] = {}

        # Per-height undo for reorg safety (mirror wave_index).
        self._undo_cache: Dict[int, List[Tuple[bytes, Optional[bytes]]]] = defaultdict(list)
        self._undo_seen: Dict[int, set] = defaultdict(set)

        current_height = getattr(db, 'db_height', -1)
        reorg_limit = getattr(env, 'reorg_limit', 0)
        min_keep = max(0, current_height - reorg_limit + 1) if reorg_limit else 0
        self._last_undo_pruned = min_keep - 1

        if self.enabled:
            self.logger.info('Realm (realm_v1) indexing enabled')

    # ------------------------------------------------------------------ index

    def process_tx(self, tx_hash: bytes, tx, height: int, tx_idx: int,
                   glyph_envelope: Dict[str, Any] = None,
                   output_refs_by_vout: Dict[int, List[Tuple[bytes, int]]] = None,
                   spent_singleton_refs: set = None):
        """Index a realm_v1 mint. No-op for any non-realm tx.

        Called by the block processor with the same args as wave_index (the
        already-parsed Glyph envelope + per-output refs). Realms are immutable,
        so this only handles the registration (mint); there is no mod path.
        """
        if not self.enabled or not glyph_envelope:
            return

        metadata = glyph_envelope.get('metadata')
        fields = extract_realm_fields(metadata or {})
        if not fields:
            return

        id_hash = realm_id_hash(fields['id'])

        # First-registration-wins by id (anti-squat, like wave names): a later
        # mint of the same slug — by anyone — never overwrites the canonical
        # record. (Discovery uses the slug as the directory key.)
        if id_hash in self.realm_cache or self.db.utxo_db.get(RealmDBKeys.REALM + id_hash) is not None:
            self.logger.warning(
                f'Realm "{fields["id"]}" already indexed; ignoring duplicate '
                f'mint at height {height} (tx {hash_to_hex_str(tx_hash)})'
            )
            return

        # The realm NFT's singleton ref is its stable on-chain identity — record
        # it so realm.* can resolve the CURRENT holder (edit rights follow the
        # NFT). The token is minted to vout 0 (mintToken "direct"); prefer the
        # vout-0 singleton, else fall back to the first singleton in the outputs.
        singleton_ref = self._pick_singleton(output_refs_by_vout)

        record = {
            'id': fields['id'],
            'name': fields['name'],
            'kind': fields['kind'],
            'seed': fields['seed'],
            'spawn': fields['spawn'],
            'owner': fields['owner'],          # immutable payload owner (minter)
            'ref': singleton_ref or b'',       # 36-byte singleton ref (b'' if none)
            'height': height,
        }
        if 'desc' in fields:
            record['desc'] = fields['desc']
        if 'creator' in fields:
            record['creator'] = fields['creator']
        if 'royalty_bps' in fields:
            record['royalty_bps'] = fields['royalty_bps']

        if not HAS_CBOR:
            self.logger.warning('cbor2 unavailable — cannot index realm record')
            return
        self.realm_cache[id_hash] = cbor2.dumps(record)
        self.realm_height[id_hash] = height

        if singleton_ref:
            # First-writer guard: a singleton can only ever belong to one realm.
            if (singleton_ref not in self.singleton_cache
                    and self.db.utxo_db.get(RealmDBKeys.SINGLETON + singleton_ref) is None):
                self.singleton_cache[singleton_ref] = id_hash
                self.singleton_height[singleton_ref] = height

        self.logger.info(
            f'Indexed realm "{fields["id"]}" ({fields["kind"]}, seed '
            f'{fields["seed"]}) at height {height} '
            f'ref={singleton_ref.hex() if singleton_ref else "none"}'
        )

    @staticmethod
    def _pick_singleton(output_refs_by_vout: Optional[Dict[int, List[Tuple[bytes, int]]]]) -> Optional[bytes]:
        if not output_refs_by_vout:
            return None
        # Prefer the token output (vout 0 for a "direct" NFT mint).
        for ref_bytes, ref_type in output_refs_by_vout.get(0, ()):  # type: ignore[arg-type]
            if ref_type == 1:
                return ref_bytes
        # Fall back to the first singleton anywhere in the outputs.
        for vout in sorted(output_refs_by_vout.keys()):
            for ref_bytes, ref_type in output_refs_by_vout[vout]:
                if ref_type == 1:
                    return ref_bytes
        return None

    # ------------------------------------------------------------------ flush/undo

    def _undo_key(self, height: int) -> bytes:
        return RealmDBKeys.UNDO + pack_be_uint32(height)

    def _record_undo(self, height: int, key: bytes):
        if not self.enabled or key in self._undo_seen[height]:
            return
        self._undo_seen[height].add(key)
        self._undo_cache[height].append((key, self.db.utxo_db.get(key)))

    def _prune_old_undo_keys(self, batch):
        reorg_limit = getattr(self.env, 'reorg_limit', 0)
        if not reorg_limit:
            return
        prune_to = max(0, self.db.db_height - reorg_limit + 1) - 1
        if prune_to <= self._last_undo_pruned:
            return
        for height in range(self._last_undo_pruned + 1, prune_to + 1):
            batch.delete(self._undo_key(height))
        self._last_undo_pruned = prune_to

    def flush(self, batch):
        if not self.enabled:
            return
        self._prune_old_undo_keys(batch)

        for id_hash, record in self.realm_cache.items():
            key = RealmDBKeys.REALM + id_hash
            height = self.realm_height.get(id_hash)
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, record)

        for singleton_ref, id_hash in self.singleton_cache.items():
            key = RealmDBKeys.SINGLETON + singleton_ref
            height = self.singleton_height.get(singleton_ref)
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, id_hash)

        for height, entries in sorted(self._undo_cache.items()):
            batch.put(self._undo_key(height), encode_undo(entries))
        self._undo_cache.clear()
        self._undo_seen.clear()

        count = len(self.realm_cache)
        self.realm_cache.clear()
        self.realm_height.clear()
        self.singleton_cache.clear()
        self.singleton_height.clear()
        if count:
            self.logger.info(f'Flushed {count} realm entries')

    def backup(self, batch, height: int):
        """Revert realm keys written at the given height (reorg unwind)."""
        if not self.enabled:
            return
        raw = self.db.utxo_db.get(self._undo_key(height))
        if not raw:
            return
        for key, prev in decode_undo(raw):
            if prev is None:
                batch.delete(key)
            else:
                batch.put(key, prev)
        batch.delete(self._undo_key(height))

    def memory_estimate(self) -> int:
        if not self.enabled:
            return 0
        undo_entries = sum(len(v) for v in self._undo_cache.values())
        return (
            len(self.realm_cache) * 600
            + len(self.realm_height) * 140
            + len(self.singleton_cache) * 190
            + len(self.singleton_height) * 140
            + undo_entries * 120
        )

    # ------------------------------------------------------------------ backfill

    def backfill_from_glyph_db(self, glyph_index) -> int:
        """Index realm_v1 tokens already present in the Glyph DB.

        Run once at startup when the realm DB is empty but the chain was synced
        before realm indexing existed: scan NFT tokens, decode their stored
        metadata, and index any realm_v1 payloads. Live indexing covers anything
        minted after this code is deployed; this catches the backlog.
        """
        if not self.enabled or not HAS_CBOR:
            return 0
        # Skip if we already have realm rows.
        for _k, _v in self.db.utxo_db.iterator(prefix=RealmDBKeys.REALM):
            self.logger.info('Realm backfill skipped: realm rows already present')
            return 0

        try:
            from electrumx.server.glyph_index import GlyphDBKeys, GlyphTokenInfo
        except ImportError:
            return 0

        count = 0
        for key, value in self.db.utxo_db.iterator(prefix=GlyphDBKeys.TOKEN):
            try:
                token = GlyphTokenInfo.from_bytes(value)
            except Exception:
                continue
            mh = getattr(token, 'metadata_hash', None)
            if not mh:
                continue
            meta_raw = self.db.utxo_db.get(GlyphDBKeys.METADATA + mh)
            if not meta_raw:
                continue
            try:
                metadata = cbor2.loads(meta_raw)
            except Exception:
                continue
            fields = extract_realm_fields(metadata if isinstance(metadata, dict) else {})
            if not fields:
                continue
            id_hash = realm_id_hash(fields['id'])
            if id_hash in self.realm_cache or self.db.utxo_db.get(RealmDBKeys.REALM + id_hash) is not None:
                continue
            ref = key[len(GlyphDBKeys.TOKEN):]
            singleton_ref = ref if len(ref) == 36 else b''
            record = {k: fields[k] for k in ('id', 'name', 'kind', 'seed', 'spawn', 'owner')}
            for opt in ('desc', 'creator', 'royalty_bps'):
                if opt in fields:
                    record[opt] = fields[opt]
            record['ref'] = singleton_ref
            record['height'] = token.deploy_height or 0
            self.realm_cache[id_hash] = cbor2.dumps(record)
            self.realm_height[id_hash] = record['height']
            if singleton_ref:
                self.singleton_cache[singleton_ref] = id_hash
                self.singleton_height[singleton_ref] = record['height']
            count += 1

        if count:
            self.logger.info(f'Realm backfill complete: indexed {count} realms')
        return count

    # ------------------------------------------------------------------ queries

    def _load_record(self, id_hash: bytes) -> Optional[Dict[str, Any]]:
        raw = self.realm_cache.get(id_hash) or self.db.utxo_db.get(RealmDBKeys.REALM + id_hash)
        if not raw or not HAS_CBOR:
            return None
        try:
            rec = cbor2.loads(raw)
            return rec if isinstance(rec, dict) else None
        except Exception:
            return None

    def _resolve_owner(self, ref: bytes) -> Optional[str]:
        """Current holder address of the realm NFT (None if unresolved)."""
        if not ref or not self.glyph_index:
            return None
        try:
            holders = self.glyph_index.get_token_holders(ref, limit=1)
            hs = holders.get('holders', []) if isinstance(holders, dict) else []
            if hs:
                return hs[0].get('address')
        except Exception:
            return None
        return None

    def _record_to_api(self, record: Dict[str, Any]) -> Dict[str, Any]:
        ref = record.get('ref') or b''
        owner = self._resolve_owner(ref)
        return {
            'id': record.get('id'),
            'name': record.get('name'),
            'kind': record.get('kind'),
            'seed': record.get('seed'),
            'spawn': list(record.get('spawn') or []),
            'desc': record.get('desc'),
            'creator': record.get('creator'),
            'royalty_bps': record.get('royalty_bps'),
            # CURRENT holder (who can edit). Falls back to the payload owner only
            # when the holder can't be resolved (e.g. glyph index unavailable).
            'owner': owner or record.get('owner'),
            # The immutable owner baked at mint (informational / provenance).
            'minted_owner': record.get('owner'),
            'ref': self._format_ref(ref),
            'ref_hex': ref.hex() if ref else None,
            'height': record.get('height'),
        }

    @staticmethod
    def _format_ref(ref: bytes) -> Optional[str]:
        if not ref or len(ref) < 36:
            return None
        return hash_to_hex_str(ref[:32]) + '_' + str(struct.unpack('<I', ref[32:36])[0])

    def get_by_id(self, realm_id: str) -> Optional[Dict[str, Any]]:
        if not isinstance(realm_id, str) or not realm_id:
            return None
        record = self._load_record(realm_id_hash(realm_id))
        return self._record_to_api(record) if record else None

    def list(self, kind: Optional[str] = None, owner: Optional[str] = None,
             q: Optional[str] = None, sort: str = 'new', limit: int = 200) -> List[Dict[str, Any]]:
        """List indexed realms, filtered + sorted. ``owner`` filters by CURRENT
        holder. ``q`` is a case-insensitive substring over name + desc + id."""
        limit = max(1, min(int(limit or 200), 1000))
        q_low = q.lower() if isinstance(q, str) and q else None
        out: List[Dict[str, Any]] = []
        seen: set = set()

        def consider(id_hash: bytes, raw: bytes):
            if id_hash in seen:
                return
            seen.add(id_hash)
            try:
                record = cbor2.loads(raw)
            except Exception:
                return
            if not isinstance(record, dict):
                return  # defensive: never treat a non-record blob as a realm
            if kind and record.get('kind') != kind:
                return
            if q_low:
                hay = f"{record.get('name', '')} {record.get('desc', '')} {record.get('id', '')}".lower()
                if q_low not in hay:
                    return
            api = self._record_to_api(record)
            if owner and api.get('owner') != owner:
                return
            out.append(api)

        # Cache first (unflushed), then DB.
        for id_hash, raw in self.realm_cache.items():
            consider(id_hash, raw)
        for key, raw in self.db.utxo_db.iterator(prefix=RealmDBKeys.REALM):
            consider(key[len(RealmDBKeys.REALM):], raw)

        if sort == 'name':
            out.sort(key=lambda r: ((r.get('name') or '').lower(), -(r.get('height') or 0)))
        else:  # 'new' (default): newest registration first
            out.sort(key=lambda r: -(r.get('height') or 0))
        return out[:limit]

    def search(self, q: str, limit: int = 200) -> List[Dict[str, Any]]:
        return self.list(q=q, limit=limit)

    def stats(self) -> Dict[str, Any]:
        db_realms = sum(1 for _ in self.db.utxo_db.iterator(prefix=RealmDBKeys.REALM))
        return {
            'enabled': self.enabled,
            'total_realms': db_realms + len(self.realm_cache),
            'cache_realms': len(self.realm_cache),
        }


# API method registration (merged into GLYPH_METHODS by glyph_api.py).
REALM_METHODS = {
    'realm.list': 'realm_list',
    'realm.get_by_id': 'realm_get_by_id',
    'realm.search': 'realm_search',
    'realm.stats': 'realm_stats',
}
