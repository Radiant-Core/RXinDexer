# RXinDexer Production Readiness Plan

## Scope
This plan covers production hardening for RXinDexer (ElectrumX + Glyph/WAVE/Swap extensions) with **public exposure** of:
- Electrum protocol: TCP/SSL/WSS
- REST API: HTTP(S)

It does **not** change Radiant Core consensus logic.

## Deploy-Blocking Items (must be addressed before public launch)
### 1) Security boundaries and ports
- Separate **admin RPC (aiorpcx LocalRPC)** from public interfaces.
- Default admin RPC binding:
  - `rpc://127.0.0.1:8001`
- Default public services:
  - `ssl://0.0.0.0:50012`
  - `wss://0.0.0.0:50011`
  - (optional) `tcp://0.0.0.0:50010` disabled by default in production.

**Status:** Completed

### 2) REST API hardening (public)
- Require explicit CORS allowlist via `ALLOWED_ORIGINS` in production.
- Enforce API key via `REST_API_KEY` for public deployments.
- Add in-process rate limiting per client IP for REST endpoints.
- Provide `/health/live` and `/health/ready` endpoints.

**Status:** Completed

### 3) Docker hardening
- Run containers as non-root.
- Avoid `network_mode: host` by default.
- Avoid publishing Radiant Core RPC (`7332`) publicly by default.

**Status:** Completed

### 4) Secrets hygiene
- Remove/sanitize any committed env files containing real credentials.
- Ensure `.gitignore` covers local env files (`.env`, `test.env`, etc.).

**Status:** Completed

## High Priority Improvements
- Add CI security scanning (`pip-audit`) and dependency automation (`dependabot`).
- Align documented RocksDB tuning knobs with actual implementation.
- Add explicit startup validation for dangerous configurations (e.g., public admin RPC).

**Status:**
- CI security scanning + dependabot: Completed
- RocksDB tuning alignment: Pending
- Startup validation for dangerous configs: Pending

## Code Audit Targets (correctness and reorg safety)
- `electrumx/server/block_processor.py`
- `electrumx/server/db.py` and history/UTXO storage
- `electrumx/server/mempool*.py`
- `electrumx/server/glyph_index.py`, `swap_index.py`, `wave_index.py`
- Reorg unwind/redo paths and persistence invariants

**Status:** In progress

### Current findings (reorg safety)
- The core DB layer supports reorg via `flush_backup()` and UTXO/history undo info.
- Glyph/WAVE/Swap indexes are flushed via `DB.flush_dbs(..., glyph_index=..., wave_index=..., swap_index=...)`.
- Additional work is required to ensure **Glyph/WAVE/Swap persisted keys are correctly reverted on reorg** (index-specific undo / rebuild strategy).

## Deliverables
- Hardened default configs (docker + env templates)
- REST API security controls
- CI security workflow + dependabot
- Targeted fixes/tests for any indexing/reorg safety gaps discovered

**Status:**
- Hardened configs: Completed
- REST API security controls: Completed
- CI security workflow + dependabot: Completed
- Indexing/reorg fixes + tests: In progress
