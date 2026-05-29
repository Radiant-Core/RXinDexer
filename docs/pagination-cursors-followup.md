# Follow-up: extend cursor pagination to remaining methods

`glyph.get_history` shipped with stable cursor pagination (see
`docs/pagination-cursors.md`). The pattern (opaque base64 RocksDB seek
key + dual-shape response gated on whether the client passes a `cursor`
argument) still needs to roll out to the other paginated RPCs.

Until that work lands, the Radiant MCP server must keep its
"PAGINATION CAVEAT" notes on the affected tool descriptions.

## Methods still on offset-only

| RPC method | Index function | File | Notes |
|---|---|---|---|
| `glyph.list_tokens` | `get_balances_for_scripthash` | `electrumx/server/glyph_index.py:1627` | Index already supports cursor; only the handler [glyph_api.py:330](electrumx/server/glyph_api.py:330) needs to expose it and stop dropping `next_cursor`. |
| `glyph.search_tokens` | `search_tokens` | `electrumx/server/glyph_index.py:1773` | Iterates `GN + name_hash` prefix. Cursor = next-unread key. Note the protocol-filter skip keeps cursor stable as long as the cursor stores the raw key, not a logical row number. |
| `swap.get_orders` | `get_open_orders` | `electrumx/server/swap_index.py:782` | Iterates `OPEN_BY_PAIR` prefix with a `status in (OPEN, PARTIAL)` post-filter. Same cursor-on-raw-key approach as `search_tokens`. Beware: the orderbook view (when both refs are supplied) calls `get_orderbook` which does *two* iterators (bids + asks) — needs a compound cursor (`{asks_cursor, bids_cursor}`) or restrict orderbook to single-page. |
| `swap.get_history` | `get_swap_history` | `electrumx/server/swap_index.py:826` | Iterates `HISTORY + base_ref` in **reverse** (newest first). Cursor must encode both the seek key and the reverse-direction flag, or the index can store `~height` (max-uint32 minus height) so forward iteration is implicitly newest-first. |
| `wave.get_subdomains` | `get_subdomains` | `electrumx/server/wave_index.py:892` | Fixed 37-iteration loop over a small alphabet — offset is naturally stable. Recommend accepting `cursor` parameter for API consistency but documenting that the offset path is canonical here. No live-chain instability to fix. |

## Migration steps per method

For each row above:

1. Add `cursor: Optional[str] = None` and the dual-shape return to the
   index method, mirroring `get_token_history`.
2. Update the handler in `electrumx/server/glyph_api.py` to use the
   `_CURSOR_UNSET` sentinel so the legacy list shape is preserved when
   the client doesn't pass a cursor.
3. Add tests modelled on `tests/server/test_pagination_cursors.py`:
   single-page, full-walk uniqueness, stable-under-insertion, bounded
   cursor size, malformed cursor.
4. Update CHANGELOG and the REST endpoint (if one exists).
5. Once *all* methods ship: the Radiant MCP server can drop the
   "PAGINATION CAVEAT" lines from these tool descriptions in
   [src/register-tools.ts](https://github.com/Radiant-Core/radiant-mcp-server/blob/radiant-mcp-server/src/register-tools.ts):
   - `radiant_get_history` (line 163)
   - `radiant_list_tokens` (line 308)
   - `radiant_search_tokens` (line 353)
   - `radiant_get_token_history` (line 390)
   - `radiant_get_tokens_by_type` (line 410) — actually already cursor-capable on the indexer side; just an MCP-doc cleanup.
   - `radiant_wave_subdomains` (line 600)
   - `radiant_get_swap_orders` (line 638)

## Open questions

* `get_orderbook` returns *both* bids and asks in one call. A compound
  cursor (`{"asks": "...", "bids": "..."}`) is the cleanest API but
  doubles cursor size. Alternative: split into `get_orderbook_asks` /
  `get_orderbook_bids` or keep `get_orderbook` non-paginated (clients
  use the orderbook for snapshot views and the per-side feeds for
  deep walks).
* `search_tokens` currently filters by protocol after iteration. A
  large protocol-filtered query could need many DB reads per returned
  row; cursor pagination doesn't fix that, but it does make the
  per-page work bounded. Consider a separate index by protocol if this
  becomes a hot path.
