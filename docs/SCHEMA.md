# RXinDexer Database Schema

This document describes the database schema for the RXinDexer, aligned with the reference `rxd-glyph-explorer` implementation.

## Token Classification

### Glyph Types (Protocol IDs)
- **1**: Fungible Token (FT)
- **2**: Non-Fungible Token (NFT)
- **3**: Data Storage (DAT)
- **4**: Decentralized Mint (DMINT)
- **5**: Mutable Token (MUT)

### Token Type Names (Photonic Wallet `payload.type`)
- **user**: User identity NFT
- **container**: Collection/container NFT
- **object** (or null): Standard NFT object

### Contract Types (for UTXOs)
- **RXD**: Standard RXD output
- **NFT**: NFT token output
- **FT**: Fungible token output
- **CONTAINER**: Container token output
- **USER**: User identity output
- **DELEGATE_BURN**: Delegate burn output
- **DELEGATE_TOKEN**: Delegate token output

---

## Core Tables

### `glyphs` (NEW - Primary Token Table)
**Unified token table matching reference implementation.** This is the primary table for all tokens (NFT, FT, DAT, CONTAINER, USER).

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `ref` | VARCHAR(72) | **Unique** 36-byte ref as hex (primary identifier) |
| `token_type` | VARCHAR(20) | NFT, FT, DAT, CONTAINER, USER |
| `p` | JSON | Array of protocol numbers/strings from payload.p |
| **Core Metadata** | | |
| `name` | VARCHAR(255) | Token name (required) |
| `ticker` | VARCHAR(50) | Token ticker symbol |
| `type` | VARCHAR(100) | User-defined type from payload (user/container/object) |
| `description` | TEXT | Token description |
| `immutable` | BOOLEAN | True unless both NFT(2) and MUT(5) protocols |
| `attrs` | JSON | Custom attributes from payload.attrs |
| **Author/Container** | | |
| `author` | VARCHAR | Author ref from payload.by |
| `container` | VARCHAR | Container ref from payload.in |
| `is_container` | BOOLEAN | Flag if this glyph IS a container |
| `container_items` | JSON | Array of glyph refs in this container |
| **State Tracking** | | |
| `spent` | BOOLEAN | Is the current UTXO spent? |
| `fresh` | BOOLEAN | Is this newly created (not yet transferred)? |
| `melted` | BOOLEAN | Has this token been melted/burned? |
| `sealed` | BOOLEAN | Is this token sealed (immutable state)? |
| `swap_pending` | BOOLEAN | Is there a pending swap for this token? |
| **Value/Location** | | |
| `value` | BIGINT | Value in satoshis (for FT amounts) |
| `location` | VARCHAR | Linked payload ref (when payload.loc is set) |
| `reveal_outpoint` | VARCHAR | txid:vout of reveal transaction |
| `last_txo_id` | INTEGER | FK to utxos.id (current UTXO) |
| `height` | INTEGER | Block height of last update |
| `timestamp` | INTEGER | Unix timestamp of last update |
| **Embedded File** | | |
| `embed_type` | VARCHAR(100) | MIME type of embedded file |
| `embed_data` | TEXT | Base64-encoded embedded file data |
| **Remote File** | | |
| `remote_type` | VARCHAR(100) | MIME type of remote file |
| `remote_url` | VARCHAR(500) | URL of remote file |
| `remote_hash` | VARCHAR | Hash of remote file |
| `remote_hash_sig` | VARCHAR | Hash signature |
| **Timestamps** | | |
| `created_at` | TIMESTAMP | Creation timestamp |
| `updated_at` | TIMESTAMP | Last update timestamp |

**Indexes:**
- `ref` (unique), `token_type`, `name`, `ticker`, `author`, `container`
- `is_container`, `height`
- Composite: `(spent, fresh)`, `(spent, token_type)`, `(spent, is_container)`, `(token_type, id)`

---

### `glyph_tokens`
Stores all fungible tokens (FT), DMINT tokens, DAT tokens, and delegate tokens.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `token_id` | VARCHAR | Unique token identifier (ref) |
| `txid` | VARCHAR | Genesis transaction ID |
| `type` | VARCHAR | Script-derived type: `ft`, `dmint`, `dat`, `delegate` |
| `owner` | VARCHAR | Current owner address |
| `token_metadata` | JSON | Full CBOR-decoded metadata |
| **Protocol Info** | | |
| `protocols` | JSON | List of protocol numbers from `p` field |
| `protocol_type` | INTEGER | Primary protocol (1=FT, 3=DAT, 4=DMINT) |
| **Core Metadata** | | |
| `name` | VARCHAR(255) | Token name |
| `description` | TEXT | Token description |
| `ticker` | VARCHAR(50) | Token ticker symbol |
| `token_type_name` | VARCHAR(100) | User-defined type (user/container/object) |
| `immutable` | BOOLEAN | True unless both NFT(2) and MUT(5) protocols present |
| `license` | VARCHAR(255) | License field from payload |
| `attrs` | JSON | Custom attributes from `payload.attrs` |
| `location` | VARCHAR | Linked payload ref (when `payload.loc` is set) |
| **Supply Fields** | | |
| `max_supply` | BIGINT | Maximum token supply |
| `current_supply` | BIGINT | Current minted supply |
| `circulating_supply` | BIGINT | Active supply in UTXOs |
| `burned_supply` | BIGINT | Melted/destroyed tokens |
| `premine` | BIGINT | Initial premine amount |
| **DMINT Fields** | | |
| `difficulty` | INTEGER | Mining difficulty |
| `max_height` | INTEGER | Block height limit |
| `reward` | BIGINT | Tokens per mint |
| `num_contracts` | INTEGER | Number of mining contracts |
| `contract_references` | JSON | References to contract outpoints |
| **Author/Container Refs** | | |
| `author` | VARCHAR | Author reference (from `by`) |
| `container` | VARCHAR | Container reference (from `in`) |
| `author_name` | VARCHAR(255) | Cached author name |
| `author_image_url` | VARCHAR(500) | Cached author image URL |
| `author_image_data` | TEXT | Cached author image (base64) |
| **Icon Fields** | | |
| `icon_mime_type` | VARCHAR(100) | Icon MIME type |
| `icon_url` | VARCHAR(500) | Remote icon URL |
| `icon_data` | TEXT | Embedded icon (base64) |
| **Location Tracking** | | |
| `genesis_height` | INTEGER | Block height at creation |
| `latest_height` | INTEGER | Block height of last update |
| `current_txid` | VARCHAR | Current transaction location |
| `current_vout` | INTEGER | Current output index |
| `reveal_txid` | VARCHAR | Reveal transaction ID |
| `reveal_vout` | INTEGER | Reveal output index |
| **Other** | | |
| `deploy_method` | VARCHAR(20) | `direct`, `dmint`, `psbt` |
| `holder_count` | INTEGER | Cached holder count |
| `created_at` | TIMESTAMP | Creation timestamp |
| `updated_at` | TIMESTAMP | Last update timestamp |
| `supply_updated_at` | TIMESTAMP | Last supply recalculation |

**Indexes:**
- `token_id`, `txid`, `type`, `owner`, `name`, `ticker`, `token_type_name`
- `container`, `author`, `genesis_height`, `reveal_txid`
- `created_at DESC`, `updated_at DESC`
- GIN index on `token_metadata::jsonb`

---

### `nfts`
Stores all NFT-contract tokens (including users, containers, and object NFTs).

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `token_id` | VARCHAR | Unique token identifier (ref) |
| `txid` | VARCHAR | Genesis transaction ID |
| `type` | VARCHAR(50) | Script-derived type: `nft`, `mutable_nft`, `delegate` |
| **Classification** | | |
| `token_type_name` | VARCHAR(100) | Payload type: `user`, `container`, or null (object) |
| **Core Metadata** | | |
| `name` | VARCHAR(255) | NFT name |
| `ticker` | VARCHAR(50) | Token ticker symbol |
| `description` | TEXT | NFT description |
| `nft_metadata` | JSON | Full CBOR-decoded metadata |
| `attrs` | JSON | Custom attributes from `payload.attrs` |
| **Author/Container Refs** | | |
| `author` | VARCHAR | Author reference (from `by`) |
| `container` | VARCHAR | Container reference (from `in`) |
| **Protocol Info** | | |
| `protocols` | JSON | List of protocol numbers |
| `protocol_type` | INTEGER | Primary protocol (2=NFT, 5=MUT) |
| `immutable` | BOOLEAN | True unless both NFT(2) and MUT(5) protocols present |
| `location` | VARCHAR | Linked payload ref (when `payload.loc` is set) |
| **Owner** | | |
| `owner` | VARCHAR | Current owner address |
| `collection` | VARCHAR | Legacy field (use `container`) |
| **Location Tracking** | | |
| `genesis_height` | INTEGER | Block height at creation |
| `latest_height` | INTEGER | Block height of last update |
| `reveal_txid` | VARCHAR | Reveal transaction ID |
| `reveal_vout` | INTEGER | Reveal output index |
| `current_txid` | VARCHAR | Current transaction location |
| `current_vout` | INTEGER | Current output index |
| **Icon Fields** | | |
| `icon_mime_type` | VARCHAR(100) | Icon MIME type |
| `icon_url` | VARCHAR(500) | Remote icon URL |
| `icon_data` | TEXT | Embedded icon (base64) |
| **Other** | | |
| `holder_count` | INTEGER | Always 1 for NFTs |
| `created_at` | TIMESTAMP | Creation timestamp |
| `updated_at` | TIMESTAMP | Last update timestamp |

**Indexes:**
- `token_id`, `txid`, `type`, `token_type_name`, `name`, `ticker`
- `author`, `container`, `owner`, `collection`
- `genesis_height`, `latest_height`, `reveal_txid`
- `created_at DESC`, `updated_at DESC`
- Composite: `(type, created_at)`, `(token_type_name, created_at)`, `(author, created_at)`, `(container, created_at)`
- GIN index on `nft_metadata::jsonb`

---

### `token_files`
Stores embedded and remote files associated with tokens.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `token_id` | VARCHAR | Reference to glyph_tokens or nfts |
| `token_type` | VARCHAR | `glyph` or `nft` |
| `file_key` | VARCHAR | Key from metadata (e.g., `icon`, `image`, `main`) |
| `mime_type` | VARCHAR | MIME type (e.g., `image/png`) |
| `file_data` | TEXT | Base64-encoded data for embedded files |
| `remote_url` | VARCHAR | URL for remote files |
| `file_hash` | VARCHAR | Hash of file content |
| `file_size` | INTEGER | Size in bytes |
| `created_at` | TIMESTAMP | Creation timestamp |

**Indexes:**
- `token_id`

---

### `containers`
Tracks container/collection NFTs and their token counts.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `container_id` | VARCHAR | The ref/token_id of the container (unique) |
| `name` | VARCHAR | Container name |
| `description` | TEXT | Container description |
| `owner` | VARCHAR | Owner address |
| `token_count` | INTEGER | Number of tokens in container |
| `container_metadata` | JSON | Additional metadata |
| `created_at` | TIMESTAMP | Creation timestamp |
| `updated_at` | TIMESTAMP | Last update timestamp |

---

## Analytics Tables

### `token_holders`
Tracks token balances per address.

| Column | Type | Description |
|--------|------|-------------|
| `token_id` | VARCHAR | Reference to glyph_tokens |
| `address` | VARCHAR | Holder's address |
| `balance` | BIGINT | Token balance |
| `percentage` | FLOAT | Percentage of circulating supply |
| `first_acquired_at` | TIMESTAMP | When first acquired |
| `last_updated_at` | TIMESTAMP | Last update |

**Unique constraint:** `(token_id, address)`

### `token_swaps`
Tracks swap offers and completed trades.

### `token_burns`
Tracks token burn (melt) events.

### `token_supply_history`
Historical supply snapshots for tokens.

### `token_price_history`
Historical price data from completed trades.

### `token_volume_daily`
Daily aggregated trading volume per token (OHLCV).

### `token_mint_events`
Tracks individual mint events for DMINT tokens.

---

## Explorer Query Patterns

### Filter by Category
```sql
-- Users (NFT with token_type_name = 'user')
SELECT * FROM nfts WHERE token_type_name = 'user' ORDER BY created_at DESC;

-- Containers (NFT with token_type_name = 'container')
SELECT * FROM nfts WHERE token_type_name = 'container' ORDER BY created_at DESC;

-- NFT Objects (NFT without user/container type)
SELECT * FROM nfts WHERE token_type_name IS NULL OR token_type_name NOT IN ('user', 'container') ORDER BY created_at DESC;

-- Fungible Tokens
SELECT * FROM glyph_tokens WHERE type = 'ft' ORDER BY created_at DESC;

-- DMINT Tokens
SELECT * FROM glyph_tokens WHERE type = 'dmint' OR protocol_type = 4 ORDER BY created_at DESC;

-- DAT Tokens
SELECT * FROM glyph_tokens WHERE type = 'dat' OR protocol_type = 3 ORDER BY created_at DESC;
```

### Sort Options
- **Age**: `ORDER BY created_at DESC/ASC`
- **Owners**: `ORDER BY holder_count DESC`
- **Mintable**: `WHERE max_supply > current_supply` (for DMINT)

---

## API Endpoints

### Token Endpoints (`/api/tokens`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/tokens` | GET | List tokens with filtering and sorting |
| `/tokens/recent` | GET | Get recently created tokens |
| `/tokens/search` | GET | Search tokens by owner, type, metadata |
| `/tokens/stats` | GET | Get token statistics |
| `/tokens/{token_id}` | GET | Get specific token details |
| `/tokens/{token_id}/history` | GET | Get token transaction history |
| `/tokens/{token_id}/files` | GET | Get token files/images |
| `/tokens/{token_id}/image` | GET | Get token image as binary |
| `/tokens/protocol/{protocol_id}` | GET | Get tokens by protocol |

**Query Parameters for `/tokens`:**
- `type`: Filter by type (`ft`, `dmint`, `dat`, `nft`)
- `limit`: Max results (default 100)
- `sort`: `created_at`, `genesis_height`, `holder_count`, `circulating_supply`, `max_supply`, `current_supply`, `mintable`
- `order`: `asc` or `desc`
- `mintable`: `true`/`false` to filter mintable tokens

### NFT Endpoints (`/api/nfts`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/nfts/recent` | GET | Get recent NFTs (optional `token_type_name` filter) |
| `/nfts/users` | GET | Get User NFTs (`token_type_name = 'user'`) |
| `/nfts/containers` | GET | Get Container NFTs (`token_type_name = 'container'`) |
| `/nfts/{token_id}` | GET | Get specific NFT details |
| `/nft/search` | GET | Search NFTs with filters |
| `/nft/collections/top` | GET | Top NFT collections |

**Query Parameters for `/nfts/recent`:**
- `token_type_name`: Filter by type (`user`, `container`, or omit for all)
- `limit`: Max results (default 100)

### Analytics Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/glyph/users/top` | GET | Top 100 users by token count |
| `/glyph/containers/top` | GET | Top 100 containers by user count |
| `/holders/token/{token_id}` | GET | Get holder count for token |

---

## Explorer Integration

### Category Tabs

The Explorer tokens page uses these categories:
- **All**: All tokens from both tables
- **Users**: NFTs with `token_type_name = 'user'` → `/api/nfts/users`
- **Containers**: NFTs with `token_type_name = 'container'` → `/api/nfts/containers`
- **FT**: Fungible tokens → `/api/tokens?type=ft`
- **NFT**: NFT objects (excluding users/containers) → `/api/nfts/recent`

### Sorting Options
- **Age**: `created_at` (default)
- **Block Height**: `genesis_height`
- **Holders**: `holder_count`
- **Supply**: `circulating_supply`, `max_supply`, `current_supply`
- **Mintable**: Filter for tokens with remaining supply

---

## Photonic Wallet Alignment

This schema aligns with Photonic Wallet's `SmartToken` interface:

| Photonic Field | glyph_tokens | nfts |
|----------------|--------------|------|
| `p` (protocols) | `protocols` | `protocols` |
| `ref` | `token_id` | `token_id` |
| `tokenType` | `type` | `type` |
| `ticker` | `ticker` | `ticker` |
| `name` | `name` | `name` |
| `type` (payload) | `token_type_name` | `token_type_name` |
| `immutable` | `immutable` | `immutable` |
| `description` | `description` | `description` |
| `author` (by) | `author` | `author` |
| `container` (in) | `container` | `container` |
| `attrs` | `attrs` | `attrs` |
| `location` (loc) | `location` | `location` |
| `license` | `license` | - |
| `embed`/`remote` | `token_files` | `token_files` |
| `height` | `genesis_height` | `genesis_height` |
| `revealOutpoint` | `reveal_txid`/`reveal_vout` | `reveal_txid`/`reveal_vout` |

### Immutability Logic
A token is **mutable** only if it has BOTH protocols:
- NFT (2) AND MUT (5)

```python
immutable = not (2 in protocols and 5 in protocols)
```

---

---

## New Tables (Reference Alignment)

### `glyph_actions`
**Tracks all token actions for history and audit trails.**

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `ref` | VARCHAR(72) | Glyph ref this action relates to |
| `type` | VARCHAR(30) | Action type (see below) |
| `txid` | VARCHAR | Transaction ID where action occurred |
| `height` | INTEGER | Block height |
| `timestamp` | TIMESTAMP | When action occurred |
| `metadata` | JSON | Action-specific data |

**Action Types:**
- `mint` - Token created
- `transfer` - Token transferred to new owner
- `melt` - Token burned/melted
- `swap` - Token swapped
- `update` - Token metadata updated (mutable tokens)
- `delegate_base` - Delegate base action
- `delegate_token` - Delegate token action
- `delegate_burn` - Delegate burn action
- `partial_melt` - Partial token melt
- `create_contract` - DMINT contract created
- `deploy_contract` - DMINT contract deployed

**Indexes:**
- `ref`, `type`, `txid`, `height`
- Composite: `(ref, type)`, `(ref, height)`, `(type, height)`

---

### `contract_groups`
**Groups contracts for DMINT tokens.**

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `first_ref` | VARCHAR(72) | First contract ref (token identifier, unique) |
| `name` | VARCHAR(255) | Token name |
| `ticker` | VARCHAR(50) | Token ticker |
| `token_type` | VARCHAR(20) | Usually 'FT' for DMINT |
| `description` | TEXT | Token description |
| `num_contracts` | INTEGER | Number of mining contracts |
| `total_supply` | BIGINT | Maximum supply |
| `minted_supply` | BIGINT | Currently minted |
| `glyph_data` | JSON | Full decoded glyph payload |
| `files` | JSON | Embedded/remote files |
| `created_at` | TIMESTAMP | Creation timestamp |
| `updated_at` | TIMESTAMP | Last update timestamp |

---

### `contracts`
**Individual DMINT mining contracts.**

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `contract_ref` | VARCHAR(72) | This contract's ref (unique) |
| `token_ref` | VARCHAR(72) | Token this contract mints |
| `location` | VARCHAR | Current UTXO location (txid:vout) |
| `output_index` | INTEGER | Output index in transaction |
| `height` | INTEGER | Current block height |
| `max_height` | INTEGER | Maximum block height for mining |
| `reward` | BIGINT | Tokens per successful mint |
| `target` | BIGINT | Mining difficulty target |
| `script` | TEXT | Contract script hex |
| `message` | VARCHAR(255) | Optional message |
| `group_id` | INTEGER | FK to contract_groups.id |
| `created_at` | TIMESTAMP | Creation timestamp |
| `updated_at` | TIMESTAMP | Last update timestamp |

---

### `contract_list`
**Quick lookup for contract refs.**

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `base_ref` | VARCHAR(72) | Base token ref |
| `count` | INTEGER | Number of contracts for this token |
| `created_at` | TIMESTAMP | Creation timestamp |
| `updated_at` | TIMESTAMP | Last update timestamp |

---

### `stats`
**Global statistics cache for dashboard queries.**

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `glyphs_total` | INTEGER | Total glyph count |
| `glyphs_nft` | INTEGER | NFT count |
| `glyphs_ft` | INTEGER | FT count |
| `glyphs_dat` | INTEGER | DAT count |
| `glyphs_containers` | INTEGER | Container count |
| `glyphs_contained_items` | INTEGER | Items in containers |
| `glyphs_users` | INTEGER | User identity count |
| `txos_total` | INTEGER | Total TxO count |
| `txos_rxd` | INTEGER | RXD TxO count |
| `txos_nft` | INTEGER | NFT TxO count |
| `txos_ft` | INTEGER | FT TxO count |
| `blocks_count` | INTEGER | Total blocks indexed |
| `latest_block_hash` | VARCHAR | Latest block hash |
| `latest_block_height` | INTEGER | Latest block height |
| `latest_block_timestamp` | TIMESTAMP | Latest block timestamp |
| `last_updated` | TIMESTAMP | Last stats update |

---

### `glyph_likes`
**User engagement - likes on glyphs.**

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `glyph_ref` | VARCHAR(72) | Glyph being liked |
| `user_address` | VARCHAR | User who liked |
| `created_at` | TIMESTAMP | When liked |

**Unique constraint:** `(glyph_ref, user_address)`

---

### `import_state`
**Tracks indexer sync state.**

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `last_block_height` | INTEGER | Last indexed block height |
| `last_block_hash` | VARCHAR | Last indexed block hash |
| `last_updated` | TIMESTAMP | Last update time |
| `is_importing` | BOOLEAN | Lock flag to prevent concurrent imports |

---

## Updated Existing Tables

### `blocks` (Updated)
Added:
- `reorg` (BOOLEAN) - Flag for reorg handling

### `utxos` (Updated)
Added:
- `date` (INTEGER) - Unix timestamp
- `change` (BOOLEAN) - Is this a change output?
- `is_glyph_reveal` (BOOLEAN) - Is this a glyph reveal transaction?
- `glyph_ref` (VARCHAR) - Reference to the glyph this UTXO belongs to
- `contract_type` (VARCHAR(20)) - RXD, NFT, FT, CONTAINER, USER, DELEGATE_BURN, DELEGATE_TOKEN

### `glyph_tokens` (Updated - Legacy)
Added:
- `spent` (BOOLEAN) - Is the current UTXO spent?
- `fresh` (BOOLEAN) - Is this newly created?
- `melted` (BOOLEAN) - Has this token been melted/burned?
- `sealed` (BOOLEAN) - Is this token sealed?
- `swap_pending` (BOOLEAN) - Is there a pending swap?
- `value` (BIGINT) - Value in satoshis

---

## Migration Order

1. `production_v2_complete` - Base tables
2. `add_token_files_containers` - TokenFile and Container tables
3. `token_indexer_enhancement` - Enhanced glyph_tokens + analytics tables
4. `nfts_table_enhancement` - Enhanced nfts table with dedicated columns
5. **`20251216_schema_alignment`** - Reference implementation alignment (NEW)
   - New `glyphs` table (unified token model)
   - New `glyph_actions` table
   - New `contract_groups`, `contracts`, `contract_list` tables (DMINT)
   - New `stats` table
   - New `glyph_likes` table
   - New `import_state` table
   - Updated `blocks`, `utxos`, `glyph_tokens` with new fields
