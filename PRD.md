# PRD: RXinDexer — Production-Ready Radiant Blockchain Indexer

**Version:** 2.0  
**Updated:** May 2026  
**Status:** Active Development

---

## 1) Summary

RXinDexer is the canonical specialized indexer for the Radiant blockchain, extending ElectrumX with first-class support for:
- **Glyph v1/v2 tokens** — FT, NFT, DAT, dMint, Encrypted, Timelocked, Container, Authority, WAVE
- **WAVE Naming System** — REP-3011 prefix-tree name resolution
- **Radiant Swap (RSWP)** — On-chain DEX order tracking and lifecycle management
- **Chain Analytics** — Address balance buckets, top holders, daily stats
- **dMint Contracts** — Mining contract discovery and state tracking for Glyph miners
- **REST API** — FastAPI HTTP layer for explorers, wallets, and DEX UIs
- **WebSocket Subscriptions** — Real-time push notifications for all indexed data
- **Mempool Glyph/Swap** — Unconfirmed token transfer and order tracking

This PRD defines the **production readiness requirements**: correctness, security, performance, observability, and operational quality that must be met before this indexer is considered production-grade.

---

## 2) Goals

- **Correctness:** All indexed data must survive reorgs cleanly. No silent data loss on reorganization.
- **Security:** Rate limiting must work behind reverse proxies. API key comparison must be timing-safe. Untrusted token metadata must be validated before storage.
- **Performance:** Hot-path block processing must avoid O(n²) loops. DB scan-based API endpoints must use incremental counters, not full scans. Cache sizes must be bounded.
- **Observability:** Prometheus metrics endpoint, structured logging of key events, parse error counters.
- **Operational quality:** All new indexers opt-in via env vars with safe defaults. RocksDB tuning params fully wired. DB schema version tracked.
- **V2 Hard Fork readiness:** dMint v2 with OP_BLAKE3/OP_K12 algorithm IDs must be correctly indexed. ASERT/LWMA DAA mode fields exposed. All parsing updated for V2 contract format.

---

## 3) Non-Goals

- Replacing Radiant Core's validation layer (consensus rules enforced by node, not indexer).
- Implementing wallet-side PSBT/PSRT signing workflows.
- Serving embedded media content (image data referenced by URL/hash only).
- Replacing the ElectrumX Electrum protocol for basic UTXO/history queries.

---

## 4) Background / Current State

### 4.1 What is working (as of May 2026)
- Glyph v1 and v2 tokens indexed: 5,112 tokens confirmed on mainnet (130 FT, 4,778 NFT, 204 dMint).
- CBOR metadata parsed and stored for 800+ reveal transactions.
- Dual-phase detection: output script ref patterns (primary) + `gly` magic byte reveal (secondary).
- Token search, balance queries, history, holder lists, dMint contract queries all functional.
- WAVE naming system indexer implemented.
- Swap order indexer implemented.
- Chain analytics indexer implemented.
- REST API (FastAPI) serving all above data.
- Mempool Glyph and Swap indexing implemented.
- WebSocket subscription infrastructure defined.
- RocksDB backend confirmed lower steady-state RAM vs LevelDB.

### 4.2 Known gaps (audit findings, May 2026)
See Section 5 (Requirements) for the full gap-closure requirements derived from audit.

Key issues found:
- Reorg safety bug: `balance_deletes` missing undo recording.
- `record_key_reveal` writes outside the batch/undo system.
- API key comparison is not timing-safe.
- Rate limiter uses `request.client.host` — bypassed behind nginx proxy.
- `_process_contract_burn` does O(n) full DB scan; needs reverse index.
- Undo serialization uses `repr`/`ast.literal_eval` — fragile and slow.
- `get_stats()` deserializes every token on every API call; needs incremental counter.
- `_TTLCache` has no capacity limit — unbounded memory growth.
- No Prometheus `/metrics` endpoint.
- No Glyph DB schema version tracking.
- RocksDB tuning env vars documented in PRD but not fully wired to `storage.py`.
- All four indexers default to `True` with no RAM/disk warning.
- Swap detection gated on Glyph envelope presence — may miss RSWP-only txs.
- `_known_refs` set grows without bound; not pruned across flushes.
- O(n²) ref-mint detection in `advance_txs`.

---

## 5) Product Requirements

### 5.1 Correctness & Reorg Safety

**R1. Balance Delete Undo**
- `balance_deletes` entries must call `_record_undo` before deletion, matching the pattern used for `balance_cache` writes.
- Regression test: reorg that crosses a flush boundary where a balance was zeroed must restore the balance correctly.

**R2. Key Reveal Atomic Write**
- `record_key_reveal` must write inside the block's write batch (not via a direct `utxo_db.put`), and must register an undo entry so the reveal can be reverted on reorg.

**R3. Swap Detection Independence**
- `swap_index.process_tx()` must be called for every transaction in a block, independent of whether a Glyph envelope was found.
- RSWP OP_RETURN detection must not be conditional on Glyph metadata presence.

**R4. `_find_output_ref` Loop Fix**
- The `while` loop in `_find_output_ref` that immediately returns must be corrected to advance `idx` when the candidate byte is part of data rather than a genuine opcode boundary.

**R5. `get_mint_history` Key Unpacking**
- Replace negative-index key unpacking (`key[-6:-2]`, `key[-2:]`) with the same absolute-offset approach used in `get_token_history` to avoid fragility when key lengths change.

**R6. Contract Burn Reverse Index**
- Add a `GlyphDBKeys.CONTRACT_TO_TOKEN` (`GC`) reverse index: `GC + contract_ref_bytes → token_ref_bytes`, written at deploy time.
- `_process_contract_burn` must use this index instead of linear scan.

### 5.2 Security

**R7. Timing-Safe API Key Comparison**
- Replace `x_api_key != required_key` with `hmac.compare_digest(x_api_key, required_key)` in `_require_api_key`.

**R8. Proxy-Aware Rate Limiting**
- Rate limiter must read client IP from `X-Forwarded-For` or `X-Real-IP` headers when `TRUST_PROXY=1` env var is set, with the number of trusted proxy hops configurable via `TRUST_PROXY_HOPS` (default 1).
- Fall back to `request.client.host` only when `TRUST_PROXY` is not set.

**R9. Token Metadata Input Validation**
- Token `name` capped at 200 characters, `ticker` at 16 characters.
- Both fields stripped of control characters (characters with `ord() < 32`).
- Validation applied in `_index_token_reveal` before storage.

**R10. REST CORS Restoration**
- Restore the FastAPI `CORSMiddleware` with `ALLOWED_ORIGINS` env var support (default: `*` in dev, must be explicit list in prod).
- Add `ELECTRUMX_ENV=prod|dev|test` check — if `prod` and `ALLOWED_ORIGINS` is `*`, emit a startup warning.

### 5.3 Performance

**R11. Incremental Token Stats Counter**
- Maintain a `GlyphDBKeys.STATS` key in the DB storing JSON or packed counts: `{total, ft, nft, dat, dmint, v1, v2}`.
- Increment/decrement at flush time. `get_stats()` reads this single key — no full DB scan.

**R12. Bounded TTL Cache**
- `_TTLCache` in `rest_api.py` must enforce a maximum entry count (`REST_CACHE_MAX_ENTRIES`, default 500).
- Evict oldest entry (FIFO or LRU) when capacity is reached, regardless of TTL.

**R13. O(1) Ref-Mint Detection**
- In `advance_txs` and `_backup_txs`, pre-build a `set` of `txin.prev_hash + to_le_uint32(txin.prev_idx)` once per transaction.
- Replace all `any(txin.prev_hash == ref[:32] and ...)` linear scans with set lookups.

**R14. Bounded `_known_refs`**
- After each `flush()` call, clear `_known_refs` (or limit it to the last N=100,000 entries with an LRU eviction).
- Repopulate from DB on next `_is_known_token` miss as already designed.

**R15. Initial FT Supply Ref Detection**
- Replace `b'\xd0' in out.pk_script` byte search with `_extract_refs_from_script()` to correctly identify the FT output and avoid false positives.

**R16. Cursor-Based Pagination for List Queries**
- `get_tokens_by_type`, `get_dmint_tokens`, `get_balances_for_scripthash`, `get_token_holders`, `list_encrypted_tokens` must support cursor-based pagination (accept `cursor` = last key seen) in addition to `offset`.
- Offset-based pagination may remain for backward compat but must skip via DB seek, not linear scan.

**R17. Rate Limiter Time-Based Cleanup**
- In addition to the request-count-based cleanup every 1000 requests, also run cleanup when `now - _last_cleanup_ts > 60.0` to handle low-traffic periods.

### 5.4 Observability

**R18. Prometheus `/metrics` Endpoint**
- Add a `/metrics` endpoint to the REST API (or a sidecar on port 8000).
- Required gauges/counters:
  - `rxindexer_sync_height` — current indexed block height
  - `rxindexer_tokens_total{type}` — total indexed tokens by type
  - `rxindexer_block_processing_seconds` — histogram of per-block processing time
  - `rxindexer_cache_size{cache}` — sizes of utxo_cache, ref_loc_cache, glyph token_cache, balance_cache
  - `rxindexer_glyph_parse_errors_total` — CBOR/envelope parse failures
  - `rxindexer_swap_orders_total{status}` — open/filled/cancelled order counts
  - `rxindexer_reorg_total` — number of reorgs processed
  - `rxindexer_flush_total` — number of DB flushes
  - `rxindexer_rest_requests_total{endpoint, status}` — REST API request counter

**R19. Block Processing Timing Logs**
- Log per-block processing time at DEBUG level: `block {height}: {n_txs} txs, {elapsed_ms}ms, {n_tokens} glyph tokens found`.
- Log flush timing at INFO level: `flush: {n_tokens} tokens, {n_balances} balances, {elapsed_ms}ms`.

**R20. Parse Error Counters**
- Count and log CBOR decode failures, malformed Glyph envelopes, and RSWP parse failures separately.
- Expose counts via `/metrics` (`rxindexer_glyph_parse_errors_total`, `rxindexer_swap_parse_errors_total`).

### 5.5 DB Schema & Versioning

**R21. Glyph DB Schema Version**
- On startup, read `GlyphDBKeys.SCHEMA_VERSION` key from DB.
- If absent (fresh DB), write current version (e.g. `1`).
- If present but older, log a warning and either migrate or refuse to start with a clear error message explaining a reindex is needed.
- Current schema version: **2** (adds `GC` reverse index, `STATS` counter key).

**R22. Undo Serialization Format**
- Replace `repr(entries).encode()` / `ast.literal_eval(raw.decode())` undo serialization in all indexers (`glyph_index.py`, `wave_index.py`, `swap_index.py`, `analytics_index.py`) with a compact binary format:
  - Each entry: `uint16 key_len + key_bytes + uint32 value_len + value_bytes` (value_len=0xFFFFFFFF signals `None`/delete).
- This applies to all four `UNDO` key handlers.

**R23. RocksDB Tuning Env Vars Fully Wired**
- All env vars listed in Section 11 must be read and applied in `storage.py` `RocksDB.open()`:
  - `ROCKSDB_WRITE_BUFFER_SIZE`, `ROCKSDB_MAX_WRITE_BUFFER_NUMBER`, `ROCKSDB_MIN_WRITE_BUFFER_NUMBER_TO_MERGE`
  - `ROCKSDB_MAX_BACKGROUND_COMPACTIONS`, `ROCKSDB_MAX_BACKGROUND_FLUSHES`
  - `ROCKSDB_COMPRESSION` (lz4 default for prod)
  - `ROCKSDB_BLOCK_CACHE_MB`, `ROCKSDB_BLOOM_BITS_PER_KEY`, `ROCKSDB_BLOCK_SIZE`
  - `ROCKSDB_MAX_OPEN_FILES` (differentiated for sync vs serving via `ELECTRUMX_ENV`)

**R24. `RocksDB.close()` Fix**
- `storage.py` `RocksDB.close()` must call `del self.db` as first step (triggers the destructor/close in python-rocksdb), then null out all references, then `gc.collect()`.

### 5.6 Configuration & Operations

**R25. Indexer Opt-In Defaults**
- Change defaults: `GLYPH_INDEX`, `WAVE_INDEX`, `SWAP_INDEX`, `ANALYTICS_INDEX` default to `True` but emit a startup log line listing enabled indexers and estimated additional RAM (e.g. `Glyph index: ~200MB extra RAM`).
- Add `MINIMAL_MODE=1` env var that disables all optional indexers and REST API, running as pure ElectrumX.

**R26. `REORG_LIMIT` Default**
- Default `REORG_LIMIT` to `100` (not the coin default which may be too low for safety). Document that values below 10 risk data loss on deep reorgs.

**R27. Glyph Subscription Wiring Verification**
- Verify (or implement) that `GlyphSubscriptionManager.set_notify_callback()` is called from the session layer on startup.
- Add a startup log: `Glyph subscriptions: callback wired` or `Glyph subscriptions: WARNING no callback set — notifications disabled`.

### 5.7 V2 Hard Fork (Activation Block 410,000)

**R28. dMint V2 Algorithm ID Indexing**
- Correctly parse and store `algo_id` from V2 contract scripts for OP_BLAKE3 (0x01) and OP_K12 (0x02).
- `DMintContractsManager.ALGORITHM_NAMES` already has these; verify `_parse_deploy_contract_state` correctly reads `algo_id` from the script for V2 contracts.

**R29. V2 dMint Contract State Parsing**
- V2 dMint contracts use OP_BLAKE3/OP_K12 for on-chain PoW validation. The indexer does NOT validate PoW (node enforces) but must correctly read the `target`, `reward`, `algo_id`, `daa_mode` fields from the V2 contract output script.
- ASERT and LWMA DAA modes (0x02, 0x03) must be correctly parsed and stored.

**R30. `GLYPH_DB_VERSION` → 2 Migration Check**
- On startup with an existing DB at schema version 1, log a warning that the `GC` reverse index and `STATS` counter are missing and will be built on next full reindex.
- Do not crash — operate in degraded mode (fall back to O(n) scan for contract burn lookup).

---

## 6) UX / API Expectations

- All list endpoints paginated with `limit` + `offset` (and optionally `cursor`).
- All responses deterministic (sorted by height/tx_idx unless stated otherwise).
- REST API returns structured JSON errors: `{"error": "...", "code": <int>}`.
- Token search returns partial matches on name prefix (exact hash match is insufficient; see R16 note).
- dMint contract list always reflects live on-chain state (no stale JSON file dependency in critical path).
- WebSocket subscription notifications delivered within one block of the triggering event.

---

## 7) Data / Storage Design

### 7.1 Key Prefix Registry (Complete)

| Prefix | Indexer | Description |
|--------|---------|-------------|
| `GT` | Glyph | Token info by ref |
| `GM` | Glyph | Metadata by hash |
| `GB` | Glyph | Balance by scripthash+ref |
| `GH` | Glyph | History by ref+height+tx_idx |
| `GY` | Glyph | By-type index |
| `GN` | Glyph | By-name-hash index |
| `GK` | Glyph | By-ticker index |
| `GS` | Glyph | Holder by ref (secondary) |
| `GR` | Glyph | Key reveal records |
| `GXU` | Glyph | Undo data by height |
| `GKR` | Glyph | Key reveal (timelock) |
| `GC` | Glyph | **NEW** Contract→Token reverse index |
| `GSTAT` | Glyph | **NEW** Incremental stats counter |
| `WT` | WAVE | Prefix tree |
| `WN` | WAVE | Name hash → ref |
| `WZ` | WAVE | Zone records |
| `WO` | WAVE | Owner scripthash |
| `SO` | Swap | Order info |
| `SP` | Swap | Open orders by pair |
| `SM` | Swap | Open orders by maker |
| `SH` | Swap | Swap history |
| `SS` | Swap | Pair statistics |
| `SF` | Swap | Fill records |
| `SWU` | Swap | Undo data |
| `AB` | Analytics | Balance buckets |
| `AD` | Analytics | Display data |
| `AU` | Analytics | UTXO metadata |
| `AS` | Analytics | Summary/daily stats |
| `AXU` | Analytics | Undo data |

### 7.2 Undo Data Format (New Binary Format)

Replaces `repr`/`ast.literal_eval`:
```
undo_entry: uint16 key_len + key_bytes + uint32 value_len + value_bytes
value_len = 0xFFFFFFFF means value was None (entry should be deleted on reorg)
Multiple entries concatenated.
```

### 7.3 dMint Contracts File (Deprecated as Critical Path)
- `contracts.json` and `contracts_extended.json` remain for backward compatibility with existing miners.
- The REST API `/dmint/contracts` endpoint serves data **directly from DB** — no file dependency.
- File is written on-demand (e.g. every N blocks or on explicit flush), not every block.

---

## 8) Testing Plan

### 8.1 Unit Tests (Required Coverage)

| Module | Tests Required |
|--------|---------------|
| `glyph_index.py` | Reorg undo for balance_deletes; contract burn reverse index; stats counter increment/decrement |
| `block_processor.py` | O(1) ref-mint set lookup; V2 algo ID parsing |
| `rest_api.py` | Timing-safe key comparison; proxy-aware rate limiting; TTL cache eviction at max capacity |
| `storage.py` | RocksDB tuning env vars applied; close() behavior |
| All indexers | Undo serialization round-trip with binary format |

### 8.2 Integration Tests

| Scenario | Expected Behavior |
|----------|------------------|
| Single block reorg crossing a flush | All Glyph balances restored; zero-balance entries restored from undo |
| Key reveal followed by reorg | Key reveal record removed on reorg |
| dMint contract burn followed by reorg | `is_spent` reverted to `False` |
| Swap order created then spent in reorg | Order reverted to open status |
| 6-block deep reorg | All indexers consistent with node |

### 8.3 Performance Benchmarks

| Metric | Target | Current Baseline |
|--------|--------|-----------------|
| Steady-state RSS (RocksDB, all indexers) | < 600 MB | ~561 MB |
| Full sync time | < 90 min | ~65 min |
| Peak RSS during sync | < 12 GB | ~10.4 GB |
| `/glyphs/stats` response time | < 5 ms | ~500 ms (full scan) |
| Block processing time (avg, mainnet) | < 50 ms | TBD |
| DB write batch time (flush) | < 200 ms | TBD |

### 8.4 Security Tests

- API key timing: measure response time for correct vs incorrect key (must be < 1ms difference).
- Rate limit bypass: send 1000 requests from the same IP via `X-Forwarded-For` header (must be rate limited when `TRUST_PROXY=1`).
- Long token name: deploy token with 10,000-char name; confirm indexer stores at most 200 chars.

### 8.5 API Latency Micro-Benchmark (ms)

Prior RocksDB baseline (preserve for regression tracking):

| Method | RocksDB avg | RocksDB p50 | RocksDB p95 |
|--------|------------|------------|------------|
| getinfo | 0.73 | 0.51 | 0.55 |
| getopenorders | 0.63 | 0.52 | 0.54 |
| getswaphistory | 0.57 | 0.54 | 0.56 |
| blockchain.scripthash.get_balance | 0.58 | 0.57 | 0.61 |
| glyph.get_token | TBD | TBD | TBD |
| glyph.get_stats | TBD (target <5ms) | — | — |

---

## 9) Go/No-Go Criteria for Production Release

**Must pass (blockers):**
1. R1 (balance delete undo) — reorg test passes across flush boundary.
2. R2 (key reveal atomic write) — key reveal survives reorg correctly.
3. R7 (timing-safe API key) — implemented.
4. R8 (proxy-aware rate limiting) — `TRUST_PROXY=1` mode implemented and tested.
5. R18 (Prometheus `/metrics`) — endpoint responds with valid text/plain exposition format.
6. R21 (schema version) — startup check implemented.
7. R22 (undo binary format) — all four indexers migrated.

**Should pass (strongly recommended):**
8. R3 (swap detection independence).
9. R6 (contract burn reverse index).
10. R11 (incremental stats counter).
11. R12 (bounded TTL cache).
12. R13 (O(1) ref-mint detection).
13. R23 (RocksDB tuning wired).

**Nice to have (post-release):**
14. R16 (cursor-based pagination).
15. R25 (MINIMAL_MODE).
16. R27 (subscription callback wiring verification).

---

## 10) Acceptance Criteria

**Primary targets:**
1. Steady-state RAM (RocksDB, serving): **≤ 600 MB** with all indexers enabled.
2. API latency: **no regression > 5%** vs prior RocksDB baseline.
3. DB size: **flat or < +10%** after adding `GC` reverse index and `GSTAT` counter.
4. All 7 blocker Go/No-Go criteria passing.

**Secondary targets:**
5. Block processing avg < 50 ms on mainnet hardware.
6. `/glyphs/stats` response time < 5 ms (incremental counter).
7. Full test suite (`pytest tests/`) passing with ≥ 90% coverage on new modules.

---

## 11) RocksDB Production Configuration

**Decision:** RocksDB is the required backend for production. LevelDB is legacy/dev only.

Env vars and recommended prod values:

| Env Var | Recommended (prod) | Notes |
|---------|-------------------|-------|
| `DB_ENGINE` | `rocksdb` | Required |
| `ELECTRUMX_ENV` | `prod` | Controls defaults |
| `ROCKSDB_COMPRESSION` | `lz4` | Best ratio/speed tradeoff |
| `ROCKSDB_BLOCK_CACHE_MB` | `256` | Tune up if RAM available |
| `ROCKSDB_WRITE_BUFFER_SIZE` | `67108864` (64MB) | Per memtable |
| `ROCKSDB_MAX_WRITE_BUFFER_NUMBER` | `3` | |
| `ROCKSDB_MIN_WRITE_BUFFER_NUMBER_TO_MERGE` | `1` | |
| `ROCKSDB_TARGET_FILE_SIZE_BASE` | `33554432` (32MB) | |
| `ROCKSDB_MAX_BACKGROUND_COMPACTIONS` | `4` | |
| `ROCKSDB_MAX_BACKGROUND_FLUSHES` | `2` | |
| `ROCKSDB_BLOOM_BITS_PER_KEY` | `10` | For prefix-heavy keys |
| `ROCKSDB_MAX_OPEN_FILES` | `512` (sync), `256` (serving) | Via `ELECTRUMX_ENV` |
| `ROCKSDB_USE_FSYNC` | `true` (prod), `false` (dev) | |

---

## 12) Prometheus Scrape Config (Production)

```yaml
scrape_configs:
  - job_name: radiant-core
    scrape_interval: 15s
    static_configs:
      - targets: ["127.0.0.1:9332"]

  - job_name: rxindexer
    scrape_interval: 15s
    static_configs:
      - targets: ["127.0.0.1:8000"]
    metrics_path: /metrics
```

Recommended Grafana dashboard panels:
- Sync height vs node height (lag indicator)
- Block processing time histogram
- Cache hit rates (UTXO, Glyph token, balance)
- RSS over time
- Glyph parse errors/block
- REST request rate and error rate by endpoint

---

## 13) Environment Variable Reference (Complete)

```bash
# === Core ===
DB_ENGINE=rocksdb
DB_DIRECTORY=/data/rxindexer
DAEMON_URL=http://user:pass@127.0.0.1:7332/
COIN=Radiant
NET=mainnet
ELECTRUMX_ENV=prod              # prod|dev|test
CACHE_MB=1200
REORG_LIMIT=6

# === Glyph Indexing ===
GLYPH_INDEX=1
GLYPH_SUBSCRIPTIONS=1
GLYPH_SEARCH_COST=5
GLYPH_METADATA_CACHE_MB=50

# === WAVE Indexing ===
WAVE_INDEX=1
WAVE_GENESIS_REF=<txid>_0
WAVE_HOT_NAMES=10000

# === Swap Indexing ===
SWAP_INDEX=1
SWAP_HISTORY_BLOCKS=10000
SWAP_CACHE_MB=10

# === Analytics ===
ANALYTICS_INDEX=1

# === Mempool ===
MEMPOOL_GLYPH_INDEX=1
MEMPOOL_SWAP_INDEX=1

# === REST API ===
REST_API_KEY=<secret>
REST_RATE_LIMIT_PER_MIN=600
REST_RATE_LIMIT_BURST=600
REST_CACHE_MAX_ENTRIES=500
ALLOWED_ORIGINS=https://yourexplorer.com
TRUST_PROXY=1
TRUST_PROXY_HOPS=1

# === RocksDB Tuning ===
ROCKSDB_COMPRESSION=lz4
ROCKSDB_BLOCK_CACHE_MB=256
ROCKSDB_WRITE_BUFFER_SIZE=67108864
ROCKSDB_MAX_WRITE_BUFFER_NUMBER=3
ROCKSDB_MIN_WRITE_BUFFER_NUMBER_TO_MERGE=1
ROCKSDB_TARGET_FILE_SIZE_BASE=33554432
ROCKSDB_MAX_BACKGROUND_COMPACTIONS=4
ROCKSDB_MAX_BACKGROUND_FLUSHES=2
ROCKSDB_BLOOM_BITS_PER_KEY=10
ROCKSDB_MAX_OPEN_FILES=512
ROCKSDB_USE_FSYNC=true

# === Operational ===
MINIMAL_MODE=0                  # 1 = pure ElectrumX, no custom indexers
LOG_LEVEL=info
```

---

## 14) Additional Suggestions / Future Work

1. **Token name prefix search:** Replace SHA256-hash-based `BY_NAME` index with a raw-byte-prefix index (first 8 bytes of name, lowercased) to enable true prefix search (e.g. "BTR" matches "BTRADIANT").
2. **RocksDB column families:** Migrate to column families per indexer (Glyph tokens, balances, WAVE tree, Swap orders) to allow per-CF compaction tuning and bloom filter configuration.
3. **Backfill/reindex command:** CLI command `rxindexer_reindex --from-height=N --indexer=glyph` to rebuild specific indexes without a full resync.
4. **DAT token content serving:** Optional endpoint to return raw embedded DAT content from the DB (base64-encoded), for DAT-aware explorers.
5. **dMint contract live probing:** REST endpoint to query live contract UTXO state from node RPC for real-time `outputs` count (already partially implemented in Glyph-miner).
6. **Token royalty tracking:** Index the `royalty` *metadata field* (basis points + beneficiary address) for marketplaces. Note: royalty is a payload field, **not** a protocol `p` code — there is no `GLYPH_ROYALTY`; code 8 is `GLYPH_ENCRYPTED`.
7. **Authority chain validation:** At index time, validate that an authority token's parent chain is intact before trusting metadata updates.
