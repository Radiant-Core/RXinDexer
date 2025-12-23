# Token Indexer Roadmap

**Created:** 2025-12-11
**Last Updated:** 2025-12-23
**Status:** Phase 1-7 Complete

## Overview

This document tracks the roadmap for implementing a fully-featured Glyph token indexer with comprehensive metadata, supply tracking, holder information, and swap/trade functionality.

---

## Phase 1: Comprehensive Token Metadata ⏳

### 1.1 Database Schema Updates ✅ COMPLETED
- [x] Add columns to `glyph_tokens` table:
  - `name` (VARCHAR) - Token name from CBOR
  - `description` (TEXT) - Token description
  - `author_ref` (VARCHAR) - Author's token ref
  - `author_name` (VARCHAR) - Resolved author name
  - `author_image` (TEXT) - Resolved author image (base64 or URL)
  - `container_ref` (VARCHAR) - Container ref
  - `icon_data` (TEXT) - Embedded icon (base64)
  - `icon_url` (VARCHAR) - Remote icon URL
  - `icon_mime_type` (VARCHAR) - Icon MIME type
  - `total_supply` (BIGINT) - Maximum supply (for FT)
  - `premine` (BIGINT) - Initial premine amount
  - `deploy_method` (VARCHAR) - direct/dmint/psbt

### 1.2 DMINT-Specific Fields ✅ COMPLETED
- [x] Add columns for DMINT tokens:
  - `difficulty` (INTEGER) - Mining difficulty
  - `max_height` (INTEGER) - Block height limit
  - `reward` (BIGINT) - Tokens per mint
  - `num_contracts` (INTEGER) - Number of mining contracts

### 1.3 CBOR Decoding Function ✅ COMPLETED
- [x] Create `decode_glyph_payload()` function in `script_utils.py`
- [x] Extract all metadata fields from CBOR payload
- [x] Handle embedded files (main, icon, etc.)
- [x] Handle remote files with URLs
- [x] Support all protocol types (FT=1, NFT=2, DAT=3, DMINT=4, MUT=5)

**Implementation:** `indexer/script_utils.py` - Added `extract_glyph_metadata()`, `decode_and_extract_glyph()`, `detect_token_burn()`, `detect_psrt_signature()`

---

## Phase 2: Supply Tracking 📊

### 2.1 Circulating Supply ✅ SCHEMA COMPLETED
- [x] Create `token_supply` table or add to `glyph_tokens`:
  - `circulating_supply` (BIGINT) - Currently active supply
  - `burned_supply` (BIGINT) - Melted/destroyed tokens
  - `last_supply_update` (TIMESTAMP)

### 2.2 Burn Detection (Melt) ✅ COMPLETED
- [x] Detect "melt" transactions where:
  - FT: Token inputs consumed with only P2PKH change outputs (no FT outputs)
  - NFT: Token input consumed with only P2PKH change outputs (no NFT outputs)
  - The token ref is NOT present in any output
- [x] Track burned amounts per token

**Implementation:** `indexer/script_utils.py:detect_token_burn()`, `indexer/token_tracking.py:record_token_burn()`

### 2.3 Supply Calculation ✅ COMPLETED
- [x] For direct deploy: supply = sum of all UTXO values with token ref
- [x] For DMINT: track minted amounts from mining contracts
- [x] Implement periodic supply recalculation job

**Implementation:** `indexer/token_tracking.py` - `calculate_circulating_supply()`, `calculate_supply_from_holders()`, `recalculate_all_supplies()`

---

## Phase 3: Holder Tracking 👥

### 3.1 Token Holders Table ✅ SCHEMA COMPLETED
- [x] Create `token_holders` table:
  - `token_id` (VARCHAR) - Token reference
  - `address` (VARCHAR) - Holder address
  - `balance` (BIGINT) - Amount held
  - `percentage` (DECIMAL) - % of circulating supply
  - `first_acquired` (TIMESTAMP)
  - `last_updated` (TIMESTAMP)

### 3.2 Balance Tracking ✅ COMPLETED
- [x] Update holder balances on each token transfer
- [x] Calculate percentage of circulating supply
- [ ] Create API endpoint for top holders (pending)

**Implementation:** `indexer/token_tracking.py` - `update_token_holders()`, `calculate_holder_percentages()`, `get_token_holders()`, `count_token_holders()`

---

## Phase 4: Swap/Trade Tracking 🔄

### 4.1 Understanding Swaps (from Photonic Wallet) ✅ ANALYZED
Swaps use **Partially Signed Radiant Transactions (PSRT)**:
1. Seller moves tokens to a dedicated `swapAddress`
2. Creates PSRT with `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY`
3. PSRT specifies: input (seller's token) → output (what seller wants)
4. Buyer completes transaction by adding their input and outputs
5. Broadcast completed transaction

### 4.2 Swap Detection ✅ COMPLETED
- [x] Detect PSRT transactions by signature flags
- [x] Track swap offers (pending PSRTs)
- [x] Track completed swaps
- [x] Calculate trade prices

**Implementation:** `indexer/script_utils.py:detect_psrt_signature()`, `indexer/token_tracking.py` - `record_swap()`, `complete_swap()`, `get_active_swaps()`, `get_completed_trades()`

### 4.3 Swap Tables ✅ SCHEMA COMPLETED
- [x] Create `token_swaps` table:
  - `id` (SERIAL)
  - `txid` (VARCHAR) - Transaction ID
  - `from_token_id` (VARCHAR) - Token being sold (NULL for RXD)
  - `from_amount` (BIGINT)
  - `to_token_id` (VARCHAR) - Token being bought (NULL for RXD)
  - `to_amount` (BIGINT)
  - `seller_address` (VARCHAR)
  - `buyer_address` (VARCHAR)
  - `status` (VARCHAR) - pending/completed/cancelled
  - `created_at` (TIMESTAMP)
  - `completed_at` (TIMESTAMP)

### 4.4 Node Enhancement Suggestions ✅ DOCUMENTED
See [NODE_ENHANCEMENT_SUGGESTIONS.md](./NODE_ENHANCEMENT_SUGGESTIONS.md) for detailed suggestions.
- [x] **Index PSRT transactions** - Flag transactions using SIGHASH_SINGLE|ANYONECANPAY
- [x] **Swap mempool tracking** - Track pending swap offers in mempool
- [x] **Price oracle endpoint** - Aggregate recent trade prices per token

---

## Phase 5: Author Resolution 🎨

### 5.1 Author Lookup ✅ COMPLETED
- [x] When `by` field contains a ref, fetch that token's metadata
- [x] Extract author's name and image from the referenced token
- [x] Cache resolved author info in `glyph_tokens`

**Implementation:** `indexer/author_resolver.py` - `resolve_author()`, `update_token_author_info()`, `batch_resolve_authors()`

### 5.2 Container Resolution ✅ COMPLETED
- [x] Similarly resolve `in` (container) references
- [x] Support nested container hierarchies

**Implementation:** `indexer/author_resolver.py` - `resolve_container()`, `batch_resolve_containers()`

---

## Phase 6: API Endpoints 🌐

### 6.1 Enhanced Token Endpoints
- [x] `GET /tokens/{id}` - Full token details
- [x] `GET /tokens/{id}/holders` - Paginated holder list
- [x] `GET /tokens/{id}/supply` - Supply breakdown
- [x] `GET /tokens/{id}/trades` - Trade history
- [x] `GET /tokens/{id}/burns` - Burn history
- [x] `GET /tokens/{id}/price` - Price history
- [x] `GET /tokens/{id}/ohlcv` - Daily OHLCV
- [x] `GET /tokens/{id}/mints` - Mint events (DMINT)
- [x] `GET /tokens/{id}/image` - Token icon/image

### 6.2 Market Endpoints
- [x] `GET /market/swaps` - Active swap offers
- [x] `GET /market/trades` - Recent completed trades
- [x] `GET /market/volume` - Trading volume stats

---

## Phase 7: Historical Data (Future) 📈

### 7.1 Price History ✅ SCHEMA COMPLETED
- [x] Store historical trade prices (`token_price_history` table)
- [x] Calculate OHLCV data for charts (`token_volume_daily` table)
- [ ] Track 24h/7d/30d price changes (implementation pending)

### 7.2 Supply History ✅ SCHEMA COMPLETED
- [x] Track supply changes over time (`token_supply_history` table)
- [x] Mint/burn event log (`token_mint_events`, `token_burns` tables)

---

## Technical Notes

### Primary Token Table
The system supports a unified `glyphs` table as the primary representation of Glyph tokens, while keeping legacy `glyph_tokens`/`nfts` tables for compatibility during migration.

### Burn/Melt Detection
Tokens are "melted" (burned) by spending them WITHOUT creating a new output with the token ref:
```
Input: FT UTXO (ref: abc123, value: 1000 tokens)
Output: P2PKH (just RXD change, no token ref)
```
The token ref disappears from the UTXO set = burned.

### Swap Mechanism
Uses Bitcoin's SIGHASH flags for trustless atomic swaps:
- `SIGHASH_SINGLE` - Only sign one input→output pair
- `SIGHASH_ANYONECANPAY` - Allow others to add inputs
- Combined: Seller signs their token→desired output, buyer completes

### Protocol Types
```
GLYPH_FT = 1    // Fungible Token
GLYPH_NFT = 2   // Non-Fungible Token
GLYPH_DAT = 3   // Data storage
GLYPH_DMINT = 4 // Proof-of-Work mintable
GLYPH_MUT = 5   // Mutable NFT
```

---

## Priority Order

1. **Phase 1** - Token metadata (foundation for everything)
2. **Phase 3** - Holder tracking (high user value)
3. **Phase 2** - Supply tracking (depends on holder tracking)
4. **Phase 5** - Author resolution (enhances display)
5. **Phase 6** - API endpoints (expose the data)
6. **Phase 4** - Swap tracking (complex, can be added later)
7. **Phase 7** - Historical data (nice to have)
