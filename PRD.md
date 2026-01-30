# PRD: ElectrumX-Core Efficiency + Radiant Swap Broadcast Support

## 1) Summary
ElectrumX-Core needs to (1) reduce resource usage (CPU/memory/IO), and (2) track swap advertisements broadcast on-chain by Radiant Core (RSWP OP_RETURN). This PRD specifies changes to improve server efficiency while adding first-class support for swap advertisement discovery and query, aligned with Radiant Core’s `-swapindex` semantics and swap payload format.

## 2) Goals
- **Efficiency (Priority: steady-state RAM):** Minimize ElectrumX steady-state RAM while serving mainnet.
- **Efficiency (Secondary):** Reduce CPU and disk IO where possible without destabilizing swap indexing.
- **Swap Tracking:** Detect and index on-chain swap advertisements (RSWP OP_RETURN) and surface them via Electrum protocol / RPC.
- **Compatibility:** Maintain backward compatibility with existing ElectrumX protocol and Radiant network behavior.

## 3) Non-Goals
- Replacing Radiant Core’s swap index (ElectrumX should complement, not duplicate, full node indexing).
- Implementing wallet-side PSRT workflows or off-chain offer sharing.
- Changing Radiant Core rules or consensus logic.
- Modifying Radiant Core node setup, configuration, or code. ElectrumX-Core changes only.

## 4) Background / Current State (key refs)
- ElectrumX architecture & caching is documented in `docs/architecture.rst`.
- ElectrumX performance notes and design intent in `docs/features.rst` and `docs/PERFORMANCE-NOTES`.
- Radiant Core broadcasts swaps as transactions with `OP_RETURN` “RSWP” payloads; indexing handled by `SwapIndex` when `-swapindex=1` (see `doc/glyphswap-psrt-guide.md`).
- Swap offers support **v1** and **v2** payloads.

## 5) Product Requirements

### 5.1 Efficiency Improvements
**R1. CPU Reduction**
- Optimize hot paths in block processing and mempool handling.
- Reduce per-tx redundant parsing via re-use where possible.
- Maintain existing batching and caching patterns.

**R2. Memory Reduction**
- Provide stable cache caps for mempool + UTXO + history indexes with Radiant scale-appropriate defaults.
- Avoid long-lived Python objects when short-lived buffers suffice.
- Allow config to disable or downscale swap-specific indexing.

**R3. Disk IO / DB Efficiency**
- Batch writes for swap indexes and other new tables.
- Ensure swap indexing is append/batch friendly and avoids high churn.
- Use compact representations where possible.

**R4. Mempool Efficiency**
- Avoid full mempool scans when only incremental updates are needed.
- Ensure mempool refresh is bounded and measured.

### 5.2 Swap Tracking Support (RSWP OP_RETURN)
**R5. Parse & Index Swap Offers**
- Parse `RSWP` `OP_RETURN` outputs in block processing.
- Support **v1** and **v2** payload formats:
  - v1: `RSWP`, `version`, `type`, `tokenID`, `offeredUTXOHash`, `offeredUTXOIndex`, `priceTerms`, `signature`.
  - v2: `RSWP`, `version=2`, `flags`, `offeredType`, `termsType`, `tokenID`, optional `wantTokenID`, `offeredUTXOHash`, `offeredUTXOIndex`, `terms_part[]`, `signature`.
- Store parsed offers in ElectrumX DB for query.

**R6. Swap Offer Lifecycle**
- When an offered UTXO is spent, mark offer as **spent** / move to history.
- Handle reorgs: revert spent state to open as required.

**R7. RPC / Protocol Methods (ElectrumX)**
- Add RPC endpoints for:
  - `getopenorders(token_ref, limit, offset, max_age?)`
  - `getswaphistory(token_ref, limit, offset)`
  - `getswapcount(token_ref)`
  - `getopenordersbywant(want_token_ref, ...)`
  - `getswaphistorybywant(want_token_ref, ...)`
  - `getswapcountbywant(want_token_ref)`
- Align method response fields with Radiant Core (version, flags, offered_type, terms_type, tokenid, want_tokenid, utxo, price_terms, signature, block_height).
- Ensure filtering of spent offers accounts for mempool (like Radiant Core).

**R8. Config Controls**
- Add ElectrumX config toggles:
  - `SWAP_INDEX=1|0`
  - `SWAP_HISTORY_BLOCKS=<n>`
  - `SWAP_CACHE_MB=<n>`

### 5.3 Reliability / Safety
**R9. Backward Compatibility**
- Existing Electrum clients should not be broken.
- New swap APIs should be optional and non-breaking.

**R10. Observability**
- Emit metrics/logs for:
  - Swap offers parsed
  - Swap offers added/removed
  - Swap index DB size
  - Cache hit rates

## 6) UX / API Expectations
- Swap offers are discovered **on-chain** (RSWP OP_RETURN).
- ElectrumX should mirror Radiant Core semantics where possible.
- API responses must be deterministic and paginated.

## 7) Data / Storage Design
- Introduce swap-index tables with prefixes:
  - Open offers by tokenID
  - Open offers by wantTokenID
  - History offers by tokenID
  - History offers by wantTokenID
- Store block height for offer creation and spend height for history.

## 8) Testing Plan

### 8.1 Baseline Build/Setup (Prod, full mainnet, full sync)
**Radiant Core build (prod)**
- Build node per `doc/build-unix.md` and `README.md`.
- Run recommended tests (`ninja check`).

**ElectrumX build**
- Install requirements and DB backend.
- Use RocksDB for production (recommended for heavy write workloads).

### 8.2 Baseline Workload
Collect metrics across same fixed workload

**ElectrumX**
- Initial sync time to same height
- Peak and steady-state RAM
- CPU usage during:
  - block processing
  - mempool refresh
- DB size + write amplification (if available)

### 8.3 Post-Upgrade Testing
Repeat **exact same workload** and environment to ensure direct comparison.

## 9) Benchmark Comparison Output
A comparison report must be produced after post-upgrade testing:

| Metric | Baseline (LevelDB) | RocksDB v2 | RocksDB v3 (prod tuned) | Notes |
|---|---:|---:|---:|---|
| Node startup time | | | | Radiant Core: `benchmarks/radiant.startup_estimate.txt` |
| Node full sync time | | | | Radiant Core (fresh datadir artifacts): `benchmarks/radiant_fresh.*` |
| ElectrumX sync time | 01h 00m 29s | 01h 12m 01s | 01h 05m 46s | v3: `benchmarks/bench-v3.log` |
| Peak RSS | 8.135GiB | 8.77GiB | 10.43GiB | v3: `benchmarks/bench-v3.samples.log` |
| Steady-state RSS | 1.175GiB | 414.6MiB | 561.3MiB | v3: `benchmarks/bench-v3.docker_stats.final.txt` |
| Avg CPU | | | 100% / 0.5% | v3: sync / serving |
| DB size | 3.8G | 3.9G | 3.8G | v3: `benchmarks/bench-v3.dbsize.final.txt` |
| Mempool refresh time | | 0.000s | | Debug log (empty mempool at capture): `benchmarks/electrumx_mempool_refresh.leveldb.debug.txt` |

### 9.1 ElectrumX API latency micro-benchmark (ms)
Artifacts:
- `benchmarks/electrumx_latency.leveldb.txt`
- `benchmarks/electrumx_latency.rocksdb.isolated.txt`

| Method | LevelDB avg | LevelDB p50 | LevelDB p95 | RocksDB avg | RocksDB p50 | RocksDB p95 |
|---|---:|---:|---:|---:|---:|---:|
| getinfo | 0.91 | 0.53 | 1.25 | 0.73 | 0.51 | 0.55 |
| getswapcount | 0.89 | 0.70 | 0.72 | 1.66 | 0.64 | 0.89 |
| getopenorders | 0.59 | 0.58 | 0.60 | 0.63 | 0.52 | 0.54 |
| getswaphistory | 0.68 | 0.68 | 0.70 | 0.57 | 0.54 | 0.56 |
| getswapcountbywant | 0.54 | 0.53 | 0.54 | 0.53 | 0.51 | 0.59 |
| getopenordersbywant | 0.56 | 0.55 | 0.55 | 0.56 | 0.52 | 0.63 |
| getswaphistorybywant | 0.52 | 0.50 | 0.54 | 0.48 | 0.44 | 0.52 |
| server.version | 0.48 | 0.48 | 0.50 | 0.62 | 0.46 | 0.79 |
| server.features | 0.47 | 0.48 | 0.50 | 0.43 | 0.40 | 0.48 |
| blockchain.headers.subscribe | 0.53 | 0.57 | 0.60 | 0.46 | 0.44 | 0.50 |
| blockchain.block.header | 0.66 | 0.61 | 0.66 | 0.65 | 0.58 | 0.59 |
| blockchain.estimatefee | 0.50 | 0.42 | 0.44 | 0.49 | 0.50 | 0.50 |
| blockchain.scripthash.get_balance | 0.51 | 0.50 | 0.56 | 0.58 | 0.57 | 0.61 |

### 9.2 Go/No-Go
**Decision:** Go with RocksDB for production deployments where the priority is low steady-state RAM. Sync time impact is accepted, but should be monitored and improved via tuning.

## 10) Acceptance Criteria (Recommended)
**Primary targets**
1) ElectrumX steady-state RAM (serving): **-50%**
2) No regression in API latency (±5%)
3) DB size: **-10% or flat** despite new swap indexes

**Secondary targets**
4) Mempool refresh time: **-20%**
5) ElectrumX steady CPU: **-25%**
6) ElectrumX full-sync time: Monitor (regressions acceptable if steady-state RAM target is met)

## 11) RocksDB Production Recommendation (Low Steady-State RAM)
**Decision:** Use RocksDB as the production DB backend (configure `DB_ENGINE=rocksdb`).

Observed tradeoffs from collected artifacts:
- Steady-state RSS is materially lower with RocksDB (see `benchmarks/rocksdb2.docker_stats.final.txt`).
- Full sync time can be higher vs LevelDB; tune RocksDB to reduce regressions.

Tuning goals:
- Reduce write amplification / compaction overhead during sync.
- Preserve low steady-state RSS while serving.

Recommended RocksDB tuning knobs (to be implemented in ElectrumX RocksDB backend configuration):
- `max_open_files`
- `write_buffer_size`
- `max_write_buffer_number`
- `min_write_buffer_number_to_merge`
- `target_file_size_base`
- `level_compaction_dynamic_level_bytes`
- `compression` (and per-level compression)
- `block_cache` sizing (bounded)
- Bloom filters for frequently-hit key spaces (bounded)

Implemented tuning env vars (see `electrumx/server/storage.py`):
- `ELECTRUMX_ENV` = `prod|dev|test` (controls defaults)
- `ROCKSDB_MAX_OPEN_FILES`
- `ROCKSDB_MAX_OPEN_FILES_SYNC`
- `ROCKSDB_MAX_OPEN_FILES_SERVING`
- `ROCKSDB_USE_FSYNC`
- `ROCKSDB_USE_FSYNC_SYNC`
- `ROCKSDB_USE_FSYNC_SERVING`
- `ROCKSDB_TARGET_FILE_SIZE_BASE`
- `ROCKSDB_WRITE_BUFFER_SIZE`
- `ROCKSDB_MAX_WRITE_BUFFER_NUMBER`
- `ROCKSDB_MIN_WRITE_BUFFER_NUMBER_TO_MERGE`
- `ROCKSDB_MAX_BACKGROUND_COMPACTIONS`
- `ROCKSDB_MAX_BACKGROUND_FLUSHES`
- `ROCKSDB_COMPRESSION` = `none|snappy|lz4|zstd|zlib` (subject to python-rocksdb support)
- `ROCKSDB_BLOCK_CACHE_MB`
- `ROCKSDB_BLOOM_BITS_PER_KEY`
- `ROCKSDB_BLOCK_SIZE`

Environments:
- RocksDB recommended.

## 12) Validation Checklist (RocksDB)
- [ ] Run a fresh mainnet sync with RocksDB tuning enabled; record `sync time` and peak RSS.
- [ ] Record steady-state RSS after sync (target: meets Primary target #1).
- [ ] Record DB size after sync (target: meets Primary target #3).
- [ ] Re-run API latency micro-bench (target: meets Primary target #2).
- [ ] Re-run swap RPC tests (`tests/server/test_swap_rpc.py`).

## 13) Prometheus Scrape (Production Recommendation)
Radiant Core already exposes `/metrics`. Use this for production consistency.

### 13.1 Example Prometheus scrape config
```yaml
scrape_configs:
  - job_name: radiant-core
    scrape_interval: 15s
    static_configs:
      - targets: ["127.0.0.1:9332"]

  - job_name: electrumx
    scrape_interval: 15s
    static_configs:
      - targets: ["127.0.0.1:8000"]
```

### 13.2 ElectrumX metrics endpoint
If ElectrumX does not expose metrics natively, add a minimal exporter or sidecar (preferred) rather than instrumenting code paths directly.

Recommended options:
- Use **node_exporter** for system metrics (CPU/RAM/IO).
- Add a simple **Prometheus exporter** for ElectrumX process stats (RSS, CPU, open files).

## 14) Additional Suggestions
1) **Incremental profiling hooks:** Add counters around block processing and mempool refresh.
2) **Swap discovery metrics:** Record offer parse failures for data quality.
3) **Optional “swap-only” mode:** Minimal load config for swap indexing + query only.
