# Stable Cursor Pagination

## Problem

Offset/limit pagination is unstable on a live chain. When new blocks land
between a client's page-1 and page-2 calls, rows shift: an entry that was
on page 1 may reappear on page 2 (double-count) or vanish entirely (miss).
Several RXinDexer RPCs that still speak `(limit, offset)` exhibit this:
`glyph.get_history`, `glyph.search_tokens`, `wave.get_subdomains`,
`swap.get_orders`, `swap.get_history`.

Several other methods — `glyph.get_tokens_by_type`,
`glyph.get_balances_for_scripthash`, `get_dmint_tokens`,
`list_encrypted_tokens` — already use opaque cursors. This document
codifies that pattern and extends it to the remaining methods.

## Design — opaque cursor token

The server returns a `next_cursor` field; the client passes it back on
the next call. The cursor encodes the indexer's continuation point —
typically the next RocksDB seek key. Cursors are server-defined and
opaque; clients MUST NOT parse them.

### Encoding

For methods backed by a RocksDB prefix scan, the cursor is the raw
next-unread key, base64-encoded. Helpers already live in
`GlyphIndex._encode_cursor` / `_decode_cursor`. Cursor size is bounded
by the underlying key size; for `glyph.get_history` the key is
`GH(2) + ref(36) + height(4) + tx_idx(2)` = 44 bytes raw, 60 bytes
after base64 — well under the 256-byte cap.

For filter-then-sort methods that can't seek (e.g. `list_encrypted_tokens`),
the cursor is the integer offset into the sorted post-filter list, also
base64-encoded.

### Stability under mempool churn

Because the cursor encodes the *next key to seek to*, not a row number,
entries iterated before the cursor stay iterated even if new rows insert
themselves later in the keyspace. A client that fully paginates sees
each entry at most once.

Reorgs can still invalidate a cursor (the row it points at may have been
undone). Treat reorg as a "restart pagination" event, the same way any
client of a live chain must.

### Backwards compatibility

Each upgraded handler adds `cursor: Optional[str] = None` as a trailing
parameter. Behavior:

- **No `cursor` passed**: handler returns the legacy shape (bare list for
  list-returning methods, dict without `next_cursor` for dict-returning
  methods). Old MCP clients see no change.
- **`cursor` passed** (including `null` literal): handler returns the new
  dict shape `{entries, next_cursor, has_more}`. `next_cursor` is `null`
  on the final page; `has_more` is `false` when `next_cursor` is `null`.

This keeps the existing `(limit, offset)` callers working byte-for-byte
while letting cursor-aware clients opt in.

## Migration path

1. `glyph.get_history` ships first (this PR) as the reference implementation.
2. `swap.get_orders`, `swap.get_history`, `glyph.search_tokens` follow,
   each using the same `_encode_cursor`/`_decode_cursor` helpers in their
   respective index modules.
3. `wave.get_subdomains` iterates a fixed 37-element loop — the offset
   is naturally stable there. Cursor support is not required but the
   handler will accept a cursor parameter for API consistency (echoing
   it back as `next_cursor` only when more rows remain).
4. Once all methods ship, the Radiant MCP server can drop the
   "PAGINATION CAVEAT" lines from its tool descriptions
   ([src/register-tools.ts](https://github.com/Radiant-Core/radiant-mcp-server/blob/radiant-mcp-server/src/register-tools.ts)
   lines 163, 308, 353, 390, 410, 600, 638).
