# Glyph Token Parsing — RXinDexer

This document describes how RXinDexer detects and parses Glyph v1 and v2 token
envelopes during block indexing.

> **Source files:**
> - `electrumx/lib/glyph.py` — envelope parser, metadata decoder, helpers
> - `electrumx/server/glyph_index.py` — block-level indexing (Phases 1 / 2 / 2b)

---

## 1. On-Chain Formats

Glyph tokens embed metadata in a **commit-reveal** pattern.  The *reveal*
transaction carries the actual CBOR payload that describes the token (name,
ticker, protocols, embedded files, etc.).  Three distinct byte-level formats
exist on chain:

### 1.1 v1 — Script-Push (mainnet, in scriptSig)

```
... OP_PUSHBYTES_3(0x03)  'gly'(676c79)  <push>(CBOR_payload) ...
```

- **Location:** scriptSig of the reveal transaction input.
- The 3-byte push `gly` acts as the magic marker.
- The *next* data push in the script is raw CBOR (a dict/map).
- No version byte, no flags byte — those are v2 additions.
- This is the format used by **all tokens on mainnet today** (created by
  Photonic Wallet v1).

### 1.2 v2 Style A — OP_RETURN (output script)

**Commit:**
```
OP_RETURN  <push>( 'gly' || version(0x02) || flags || commit_hash(32) [...] )
```

**Reveal:**
```
OP_RETURN  <push>( 'gly' || version(0x02) || flags )
           <push>( CBOR_metadata )
           [<push>( file_chunk )]...
```

- **Location:** an OP_RETURN output of the reveal transaction.
- The magic bytes are *concatenated* with version + flags inside one push.
- For reveals the `is_reveal` flag (bit 7) is set; metadata is in the **next**
  push.
- For commits the remaining bytes after flags hold the 32-byte commit hash
  and optional fields (content root, controller).
- Maximum payload ≤ 100 KB.

### 1.3 v2 Style B — OP_3 Chunked (scriptSig)

**Commit:**
```
... OP_3(0x53)  <push>('gly')  <push>( version || flags || commit_hash [...] ) ...
```

**Reveal:**
```
... OP_3(0x53)  <push>('gly')  <push>( CBOR_metadata )  [<push>(file_chunk)]... ...
```

- **Location:** scriptSig of the reveal transaction input.
- `OP_3` (0x53) acts as a delimiter preceding the magic push.
- The `gly` magic is again a standalone 3-byte push (identical byte pattern
  to v1).
- The *next* push is raw CBOR for reveals, or `version+flags+commit` for
  commits.
- Supports large payloads up to `MAX_TX_SIZE` (12 MB).

---

## 2. Flags Byte (v2 only)

| Bit | Name              | Description                              |
|-----|-------------------|------------------------------------------|
| 0   | `has_content_root`| Merkle root of content present           |
| 1   | `has_controller`  | Mutable controller specified             |
| 2   | `has_profile_hint`| App profile hint included                |
| 3-6 | Reserved          | Must be zero                             |
| 7   | `is_reveal`       | Style A: distinguish commit from reveal  |

---

## 3. Parser Implementation

### 3.1 `_parse_script_pushes(data)`

Extracts an ordered list of data-push payloads from raw script bytes.  Skips
non-push opcodes (OP_RETURN, OP_3, OP_DROP, etc.) and correctly advances past
Radiant ref opcodes (0xd0–0xd3, 0xd8) which embed 36-byte inline data.

### 3.2 `parse_glyph_envelope(data)`

Iterates over the push list looking for `gly`:

1. **Standalone push** (`push == b'gly'`):
   - Next push decoded as CBOR → **reveal** (v1 or v2 Style B).
   - Next push starts with version byte (0x01/0x02) → **v2 commit**.

2. **Prefix of a larger push** (`push[:3] == b'gly'`):
   - Bytes after magic are version + flags (v2 Style A).
   - `is_reveal` flag set → metadata in next push.
   - `is_reveal` flag clear → commit hash follows inline.

### 3.3 Commit vs. Reveal Disambiguation

| Format       | Reveal indicator                                    |
|--------------|-----------------------------------------------------|
| v1           | Always a reveal (CBOR dict after magic push)        |
| v2 Style A   | Flags byte bit 7 (`is_reveal`) set                  |
| v2 Style B   | Payload is valid CBOR dict (not version+flags)      |

---

## 4. Indexer Pipeline

### Phase 1 — Output Scanning (all txs)

Scans every output script for `OP_PUSHINPUTREF` (0xd0, FT) and
`OP_PUSHINPUTREFSINGLETON` (0xd8, NFT) to register token refs, track
balances, and detect new mints.

### Phase 2 — Input Scanning (reveal detection, v1 + v2 Style B)

For each input scriptSig, checks `contains_glyph_magic()`.  If found,
calls `parse_glyph_envelope()` → `parse_glyph_metadata()`.  Links the
reveal to its token ref via `_find_output_ref()`.

### Phase 2b — Output Scanning (reveal detection, v2 Style A)

Only runs when Phase 2 found no reveals.  For each output, checks
`is_glyph_op_return()`.  If found, parses the OP_RETURN envelope the
same way as Phase 2.  The token ref is resolved from other outputs in
the same transaction.

---

## 5. Version Compatibility

- **v2 indexers MUST support v1 tokens** (per Glyph v2 whitepaper §7).
- The parser tries CBOR decode *first* (v1 path) before falling back to
  the v2 structured header, ensuring v1 tokens are never missed.
- `is_glyph_op_return()` is a lightweight pre-filter that avoids parsing
  non-Glyph OP_RETURN outputs.

---

## 6. References

- [Glyph v1 Photonic Implementation](../../Glyph%20Token%20Standards/Glyph_v1_Photonic_Implementation.md)
- [Glyph v2 Token Standard Whitepaper](../../Glyph%20Token%20Standards/Glyph_v2_Token_Standard_Whitepaper.md)
- Radiant `OP_PUSHINPUTREF` / `OP_PUSHINPUTREFSINGLETON` — REP-0003
