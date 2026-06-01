# Scope: Real dMint Burn Detection (per-contract liveness)

Status: proposed · Owner: TBD · Related: commits `05c29ac` (orphan latch), `31a081b` (is_spent exclusion)

## 1. Problem

The indexer cannot tell a **partially-mined-but-still-mineable** dMint token from a
**dead** one. Two band-aids already shipped:

- `31a081b` made `DMintContractsManager` ignore the glyph `is_spent` flag and
  decide mineability purely from supply (`percent_mined < 100`). This *unhid*
  legitimately-mineable tokens (e.g. **GRASS** — `8409cd9f…f3_0`, 75.9% mined,
  21 contracts, still mineable).
- The cost: a genuinely-**burned** token (all contracts destroyed with supply
  remaining) now shows as mineable. The miner's load flow fails gracefully on a
  dead contract, but the listing is wrong.

This doc scopes restoring **correct** burn detection without re-hiding live tokens.

## 2. Root cause (current model)

`GlyphTokenInfo` (`glyph_index.py:158-175`) models a dMint token with a **single**
`contract_ref` and a `num_contracts` *count* — it does **not** track the set of live
contract singletons.

- Deploy (`_index_token_reveal` → `_find_contract_ref`, `glyph_index.py:1095`,
  `_find_contract_ref`): records only the **first** non-token singleton ref and writes
  one `CONTRACT_TO_TOKEN` (`GC`) reverse-index entry.
- Mint (`_process_mint`, `:887`): counts contract outputs carrying singleton+token_ref
  per tx × `reward` → `mined_supply`. **This is correct** (percent_mined is accurate).
- Burn (Phase 4, `:705-724` → `_process_contract_burn`, `:987`): when *any* singleton
  ref is spent in inputs but not recreated in a non-`OP_RETURN` output, it marks the
  **entire token** `is_spent=True`.

A dMint contract singleton is **stable across mines** (the ref reappears in the
recreated output), so normal mining does not trigger Phase 4. But two events do:

1. **Terminal completion** — a contract reaching `maxHeight` is spent on its final
   mine and *not* recreated. Normal, expected — yet it's treated as a token burn.
2. **OP_RETURN burn** — a contract genuinely destroyed.

For a multi-contract token, **either event on one contract kills the whole token.**
GRASS has 21 contracts (`21 × maxHeight 10000 × reward 100 = 21,000,000` supply); once
the first contract completed, `_process_contract_burn` set `is_spent=True` at 75.9%,
while ~5 contracts were still mineable.

`is_spent` is overloaded across three states: fully-mined (`:967-968`), one-contract
terminal/destroyed (false burn, `:1019`), and real burn.

## 3. Goal — correct per-token semantics

Track **live mineable contracts** per token and derive status from supply + liveness:

| State | Condition |
|---|---|
| mineable | `live_contracts > 0` AND `mined_supply < total_supply` |
| fully&nbsp;mined | `mined_supply >= total_supply` |
| burned (terminated early) | `live_contracts == 0` AND `mined_supply < total_supply` |

`DMintContractsManager` lists a token as active iff **mineable**. `is_spent` is no
longer used for the mineability decision (it stays as a raw on-chain flag).

## 4. Design

### 4.1 New per-token state
Add to `GlyphTokenInfo.__slots__` + serialization (bump the record `version` byte in
`to_bytes`/`from_bytes`, `:255-335`):
- `live_contracts: int` — count of unspent contract singletons.
- (Phase 2, optional) `contract_heights: dict[ref→height]` for exact
  `mineable_remaining` = `Σ (maxHeight − height) × reward`.

MVP can store just the count. Storing the live **set** (or per-contract heights) is
only needed for exact remaining-supply reporting and is the bigger persistence cost.

### 4.2 Deploy — register all contracts
Replace `_find_contract_ref` with `_find_all_contract_refs(tx, token_ref)` returning the
**per-contract** singleton (0xd8) refs. **Filter carefully:** a dMint contract script
contains *multiple* singletons — the real per-contract ones share the **token ref's
txid** (`GEN:1, GEN:2, …` where the token ref is `GEN:0`), while covenant/state
singletons sit at *other* txids and must be excluded. Filter rule:
`ref_type == singleton AND ref.txid == token_ref.txid AND ref.vout != token_ref.vout`.
(Verified on GRASS: 21 contracts = `GEN:1..21`; the script also carried unrelated
singletons `7a539c…`, `c0ea79…` that the naive "any singleton ≠ token_ref" rule would
wrongly register.) For each kept ref: write `CONTRACT_TO_TOKEN` (`:1097-1104`). Set
`live_contracts = len(refs)` (cross-check vs metadata `num_contracts`). Confirm whether
any dMint variant creates contracts in follow-up txs; if so, register on first sight in
`_process_mint`.

> **Assumption VALIDATED (2026-06-01):** dMint mines preserve the contract singleton
> ref. Traced GRASS singleton `8409cd9f…f3:1`: mint tx `6ad2ec9d…7482` @307645 both
> **spent** `…f3:1` from an input and **recreated** `…f3:1` in an output. So normal
> mining keeps the ref in outputs (never enters Phase 4's `destroyed` set); a singleton
> only leaves the live set on terminal completion (maxHeight, no recreate) or an
> OP_RETURN burn — exactly the decrement signal this design relies on.

### 4.3 Spend — decrement, don't nuke
Rework Phase 4 (`:705-724`) + `_process_contract_burn`:
- For each destroyed singleton mapped to a token, **decrement `live_contracts`** (floor
  0) instead of setting token `is_spent`. (Whether it was terminal completion or an
  OP_RETURN burn is irrelevant to liveness — that contract is gone either way;
  `mined_supply` already reflects the mints that occurred.)
- Set the token-level burned flag only when `live_contracts == 0` and supply remains.
- Keep recording per-event history (MINT / contract-BURN) for explorers.

### 4.4 Reorg / undo
`live_contracts` lives in the token record, which is snapshotted by the `GXU` undo
mechanism (`:53`, `:421-433`). Verify the undo captures the **full pre-image** of the
token record on every mutation so a reorg restores `live_contracts` exactly; add
explicit coverage. This is the highest-risk correctness area.

### 4.5 Consumers
- `glyph_index` token dict (`:2091-2097`, `:1943-1944`): add `live_contracts`,
  `mineable_remaining`, and a derived `mineable` bool.
- `dmint_contracts.py` `sync_from_index`: gate active on `token['mineable']` (or
  `live_contracts > 0 and percent_mined < 100`) instead of supply alone; this *re-adds*
  correct hiding of dead tokens removed in `31a081b` while keeping GRASS-style tokens.
- v2 API (`_to_token_summary_item`): populate `contracts.mineable_remaining` (currently
  always `0`/`null`) and keep `is_fully_mined` supply-derived.

## 5. Backfill / migration
Existing token records have wrong `is_spent`/no `live_contracts`. Options:
- **(A) Full glyph reindex** (~10 h, already a routine op) — recomputes everything
  correctly. Simplest, recommended to finalize.
- **(B) One-off targeted backfill** — for each dMint token, recompute `live_contracts`
  by checking each registered contract singleton's UTXO liveness (node/index lookup).
  Faster, but only as good as the contract-set we can reconstruct from existing data
  (we only stored one `contract_ref` historically, so this likely still needs a scan).
- **(C) Forward-only** — deploy logic, let it self-correct on future activity; stale
  dead tokens remain mismarked until a reindex.

Because `31a081b` already makes these tokens *visible*, burn detection is an **accuracy**
improvement, not an outage fix — (A) on the next planned reindex is acceptable.

## 6. Testing
- Unit: multi-contract token where 1 of N contracts completes → token stays mineable;
  all N complete → fully mined; some burned (OP_RETURN) with supply left → burned/unmineable.
- Reorg: spend that decrements `live_contracts`, then reorg → count restored.
- Regression: GRASS-shaped fixture (21 contracts, partial completion) stays active.
- Serialization round-trip for the new field + old-record upgrade path.

## 7. Effort, risk, phasing
- **Effort:** ~1–2 weeks. Touches glyph parsing, `GlyphTokenInfo` schema + serialization
  version, deploy/mint/burn indexing, undo/reorg, dMint manager, v2 API, tests, + a reindex.
- **Risk:** medium-high — changes core indexing state and reorg undo; serialization
  version bump needs an upgrade path; correctness depends on the (verified-but-confirm)
  assumption that contract singleton refs are stable across mines.
- **Phasing:**
  - **P1 (MVP):** `live_contracts` count; decrement-on-destroy; `mineable` flag consumed
    by the dMint manager; reindex. Restores correct burn hiding. *(bulk of the value)*
  - **P2:** per-contract heights → exact `mineable_remaining` in the API.
  - **P3:** explorer-facing burn history surfacing / `/dmint` burned analytics.

## 8. Decisions
1. **P1 approved** + reindex via backfill option A (2026-06-01).
2. **Singleton-stability assumption CONFIRMED** on-chain (see §4.2 validation note).
3. **P2 deferred.** The mineable/not boolean is enough for the miner UI today (Claimed%
   is supply-derived and accurate; the list works off the boolean). P2's real payoff is
   letting the client retire its slow per-token contract-count probing
   (`enrichContractSummariesWithVerifiedCounts` in Glyph-miner `deployments.ts`, which
   binary-searches sub-contract refs via live `fetchRef` RPCs) in favour of an
   authoritative server count — a latency/UX win, not correctness. Revisit P2 if/when we
   decide to remove that client probing.
