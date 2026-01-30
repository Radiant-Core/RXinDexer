# RXinDexer Architecture

## Overview

RXinDexer extends ElectrumX with comprehensive indexing for the Radiant ecosystem:

```
┌─────────────────────────────────────────────────────────────────┐
│                        RXinDexer                                │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │   Electrum  │  │    Glyph    │  │    WAVE     │             │
│  │    Core     │  │   Indexer   │  │   Indexer   │             │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
│         │                │                │                     │
│  ┌──────┴────────────────┴────────────────┴──────┐             │
│  │              Unified Database Layer            │             │
│  │         (RocksDB / LevelDB backends)          │             │
│  └───────────────────────┬───────────────────────┘             │
│                          │                                      │
│  ┌───────────────────────┴───────────────────────┐             │
│  │              Radiant Core (RPC)               │             │
│  └───────────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

## 1. Glyph Token Indexing

### 1.1 Protocol Detection

Glyph tokens are identified by the magic bytes `0x676c79` ("gly") in OP_RETURN outputs.

```python
GLYPH_MAGIC = b'gly'

def is_glyph_output(script: bytes) -> bool:
    """Check if script contains Glyph protocol data"""
    if script[0] == OP_RETURN:
        return script[2:5] == GLYPH_MAGIC
    return False
```

### 1.2 Protocol IDs

| ID | Protocol | Description |
|----|----------|-------------|
| 1 | GLYPH_FT | Fungible Token |
| 2 | GLYPH_NFT | Non-Fungible Token |
| 3 | GLYPH_DAT | Data Attachment |
| 4 | GLYPH_DMINT | Decentralized Minting |
| 5 | GLYPH_MUT | Mutable Metadata |
| 6 | GLYPH_ENCRYPTED | Encrypted Content |
| 7 | GLYPH_CONTAINER | Collection Container |
| 8 | GLYPH_ROYALTY | On-chain Royalties |
| 9 | GLYPH_TIMELOCK | Timelocked Reveals |
| 10 | GLYPH_AUTH | Authority Tokens |
| 11 | GLYPH_WAVE | WAVE Naming System |

### 1.3 Database Schema Extensions

```
# Glyph token index
glyph_tokens:
  key: ref (txid_vout)
  value: {
    protocols: [int],
    metadata_hash: bytes32,
    deploy_height: int,
    current_supply: int (FT only),
    is_spent: bool
  }

# Glyph metadata cache
glyph_metadata:
  key: metadata_hash
  value: CBOR-encoded metadata

# Address token holdings
glyph_balances:
  key: scripthash + ref
  value: amount (for FT) or 1 (for NFT)

# Token history
glyph_history:
  key: ref + height + tx_idx
  value: {txid, type: 'mint'|'transfer'|'burn'}
```

### 1.4 Glyph v1 vs v2

| Feature | v1 | v2 |
|---------|----|----|
| Metadata version | v: 1 | v: 2 |
| Protocol field | type: string | p: [int, ...] |
| Mutable support | ❌ | ✅ (p includes 5) |
| dMint support | ❌ | ✅ (p includes 4) |
| Containers | ❌ | ✅ (p includes 7) |

## 2. WAVE Naming System

### 2.1 Prefix Tree Structure

WAVE names are indexed using a prefix tree where each character maps to an output index:

```
Character Set (37 chars):
a=0, b=1, c=2, ..., z=25, 0=26, 1=27, ..., 9=35, -=36

Tree Structure:
ROOT (genesis)
├── output[1] = 'a' claims
│   ├── output[1] = 'aa' claims
│   ├── output[2] = 'ab' claims
│   └── ...
├── output[2] = 'b' claims
└── ...

Output Index = char_index + 1  (output 0 is always the claim token)
```

### 2.2 Resolution Algorithm

```python
def resolve_wave_name(name: str) -> Optional[WaveRecord]:
    """
    Resolve a WAVE name to its zone records.
    
    1. Start at WAVE_GENESIS_REF
    2. For each character in name:
       a. Calculate output_index = WAVE_CHAR_MAP[char] + 1
       b. Find UTXO at that output index
       c. If not found, name is available
       d. If found, continue to next character
    3. Return zone records from final UTXO's metadata
    """
    current_ref = WAVE_GENESIS_REF
    
    for char in name:
        output_index = WAVE_CHAR_TO_INDEX[char] + 1
        utxo = db.get_wave_child(current_ref, output_index)
        
        if utxo is None:
            return None  # Name not registered
        
        current_ref = utxo.ref
    
    return db.get_wave_metadata(current_ref)
```

### 2.3 Database Schema

```
# WAVE prefix tree index
wave_tree:
  key: parent_ref + output_index
  value: child_ref

# WAVE name -> ref mapping (for reverse lookups)
wave_names:
  key: normalized_name
  value: ref

# WAVE zone records
wave_zones:
  key: ref
  value: {
    address: str,
    content_hash: bytes,
    custom: dict,
    updated_height: int
  }
```

### 2.4 Zone Record Types

| Record | Key | Description |
|--------|-----|-------------|
| Address | `address` | Radiant payment address |
| Avatar | `avatar` | Avatar URL or content hash |
| Display | `display` | Display name (Unicode) |
| Description | `desc` | Profile description |
| URL | `url` | Website URL |
| Email | `email` | Contact email |
| A | `A` | IPv4 address |
| AAAA | `AAAA` | IPv6 address |
| CNAME | `CNAME` | Canonical name alias |
| TXT | `TXT` | Text records (array) |
| MX | `MX` | Mail exchange records |
| NS | `NS` | Nameserver records |
| Custom | `x-*` | Custom records with x- prefix |

### 2.5 WAVE API Methods (Implemented)

```
wave.resolve(name)
  -> {name, ref, zone, owner, available} or null

wave.check_available(name)
  -> {available: bool, name, ref?, error?}

wave.get_subdomains(parent_name, limit?, offset?)
  -> [{char, ref}, ...]

wave.reverse_lookup(scripthash, limit?)
  -> [{ref}, ...] names owned by address

wave.stats()
  -> {enabled, genesis_configured, tree_cache_size, ...}
```

### 2.6 Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `WAVE_INDEX` | `1` | Enable WAVE indexing |
| `WAVE_GENESIS_REF` | (network) | Genesis ref for prefix tree root |
| `WAVE_HOT_NAMES` | `10000` | Size of hot name cache |

### 2.7 Original Zone Record Type (for reference)

| Record | Key | Description |
|--------|-----|-------------|
| Address | `address` | Radiant address for payments |
| Content | `content` | IPFS/Arweave hash |
| Avatar | `avatar` | Profile image ref |
| Custom | `*` | Application-specific data |

## 3. API Endpoints

### 3.1 Glyph Methods

```
blockchain.glyph.get_token(ref)
  -> {protocols, metadata, deploy_height, supply}

blockchain.glyph.get_balance(scripthash, ref)
  -> {confirmed: int, unconfirmed: int}

blockchain.glyph.list_tokens(scripthash, limit, offset)
  -> [{ref, protocols, amount}, ...]

blockchain.glyph.get_history(ref, limit, offset)
  -> [{txid, height, type}, ...]

blockchain.glyph.search_tokens(query, protocols, limit)
  -> [{ref, name, ticker, ...}, ...]
```

### 3.2 WAVE Methods

```
blockchain.wave.resolve(name)
  -> {ref, zone: {address, content, ...}, owner}

blockchain.wave.check_available(name)
  -> {available: bool, ref: str|null}

blockchain.wave.get_subdomains(parent_name, limit, offset)
  -> [{name, ref}, ...]

blockchain.wave.reverse_lookup(address)
  -> [{name, ref}, ...]
```

### 3.3 Swap Methods

```
blockchain.swap.get_orders(sell_ref, buy_ref, limit, offset)
  -> [{txid, vout, sell_amount, buy_amount, partial}, ...]

blockchain.swap.get_history(ref, limit, offset)
  -> [{txid, height, sell_ref, buy_ref, amounts}, ...]
```

### 3.4 dMint Contracts Methods (for Glyph Miner)

```
dmint.get_contracts(format='simple')
  -> Simple: [["ref", outputs], ...]
  -> Extended: {version, updated_at, contracts: [{ref, outputs, ticker, ...}]}

dmint.get_contract(ref)
  -> {ref, outputs, ticker, name, algorithm, difficulty, reward, percent_mined, active}

dmint.get_by_algorithm(algorithm)
  -> [{ref, outputs, ...}, ...] filtered by algorithm ID

dmint.get_most_profitable(limit=10)
  -> [{ref, outputs, ...}, ...] sorted by reward/difficulty ratio
```

**Contracts File Format:**

Simple format (`data/contracts.json`) - backward compatible with existing miners:
```json
[
  ["a443d9df469692306f7a2566536b19ed7909d8bf264f5a01f5a9b171c7c3878b00000001", 32],
  ["90bdd6401e92f45fef9686d1d2ab499397bc31a5c2f98adf01bca91ddf78fe2e00000001", 8]
]
```

Extended format (`data/contracts_extended.json`) - for enhanced miners:
```json
{
  "version": 1,
  "updated_at": "2026-01-28T22:43:00Z",
  "updated_height": 123456,
  "contracts": [
    {
      "ref": "a443d9df469692306f7a2566536b19ed7909d8bf264f5a01f5a9b171c7c3878b00000001",
      "outputs": 32,
      "ticker": "EXAMPLE",
      "name": "Example Token",
      "algorithm": 1,
      "difficulty": 12345678,
      "reward": 100000000,
      "percent_mined": 45.5,
      "active": true,
      "deploy_height": 100000
    }
  ]
}
```

**Algorithm IDs:**
| ID | Algorithm |
|----|-----------|
| 0 | None |
| 1 | SHA256D |
| 2 | RadiantHash |

### 3.5 Mempool Glyph/Swap Methods

Real-time unconfirmed transaction queries for wallets and DEX UIs:

```
glyph.get_unconfirmed_balance(scripthash, ref)
  -> int (balance delta: positive=incoming, negative=outgoing)

glyph.get_unconfirmed_txs(scripthash)
  -> [{txid, ref, type, amount, confirmed: false}, ...]

glyph.get_token_unconfirmed(ref)
  -> [{txid, type, amount, to, confirmed: false}, ...]

swap.get_unconfirmed_orders(base_ref?, quote_ref?)
  -> [{order_id, txid, base_ref, quote_ref, side, price, amount, confirmed: false}, ...]

swap.get_user_unconfirmed(scripthash)
  -> [{order_id, txid, ...}, ...] user's pending orders

mempool.glyph_stats()
  -> {enabled, glyph_txs, glyph_refs_tracked, swap_orders, ...}
```

**Mempool Indexing Rules:**
| Transaction Type | Indexed in Mempool | Rationale |
|------------------|-------------------|-----------|
| Glyph transfers | ✅ Yes | Wallets need unconfirmed balance |
| Swap orders | ✅ Yes | DEX needs real-time orderbook |
| dMint reveals | ❌ No | Prevent gaming; wait for confirmation |
| WAVE claims | ❌ No | Prevent front-running; wait for confirmation |

### 3.6 WebSocket Subscription Methods

Real-time push notifications for wallets, explorers, and DEX UIs:

```
# Glyph Balance Subscriptions
glyph.subscribe.balance(scripthash, ref)
  -> true | Notifications: {method: "glyph.balance", params: {scripthash, ref, balance, delta}}

glyph.unsubscribe.balance(scripthash, ref)
  -> true/false

# Token State Subscriptions
glyph.subscribe.token(ref)
  -> true | Notifications: {method: "glyph.token", params: {ref, data: {...}}}

glyph.subscribe.transfers(ref)
  -> true | Notifications: {method: "glyph.transfer", params: {ref, txid, from, to, amount, height}}

# Swap Orderbook Subscriptions
swap.subscribe.orderbook(base_ref, quote_ref)
  -> true | Notifications: {method: "swap.orderbook", params: {base_ref, quote_ref, change, order}}

swap.subscribe.fills(base_ref, quote_ref)
  -> true | Notifications: {method: "swap.fill", params: {base_ref, quote_ref, fill}}

swap.subscribe.user_orders(scripthash)
  -> true | Notifications: {method: "swap.user_order", params: {scripthash, change, order}}

# WAVE Name Subscriptions
wave.subscribe.name(name)
  -> true | Notifications: {method: "wave.name", params: {name, owner, txid, height}}

# dMint Mining Subscriptions
dmint.subscribe.token(ref)
  -> true | Notifications: {method: "dmint.update", params: {ref, data: {difficulty, reward, mint_count, ...}}}
```

**Notification Triggers:**
| Event | Subscriptions Notified |
|-------|------------------------|
| Token transfer confirmed | `glyph.balance`, `glyph.token`, `glyph.transfers` |
| Token transfer in mempool | `glyph.balance` (with unconfirmed flag) |
| New swap order | `swap.orderbook`, `swap.user_orders` |
| Order filled | `swap.orderbook`, `swap.fills`, `swap.user_orders` |
| Order cancelled | `swap.orderbook`, `swap.user_orders` |
| WAVE name claimed | `wave.name` |
| dMint block mined | `dmint.token`, `glyph.token` |

## 4. Implementation Plan

### Phase 1: Glyph v1 Complete (Week 1-2)
- [ ] Glyph magic byte detection
- [ ] CBOR metadata parsing
- [ ] FT/NFT/DAT indexing
- [ ] Balance queries
- [ ] Token history

### Phase 2: Glyph v2 Support (Week 3-4)
- [ ] Protocol array parsing
- [ ] dMint token tracking
- [ ] Mutable metadata updates
- [ ] Container relationships
- [ ] Authority token validation

### Phase 3: WAVE Indexing (Week 5-6)
- [ ] Prefix tree construction
- [ ] Name resolution API
- [ ] Zone record caching
- [ ] Reverse lookups
- [ ] Subdomain queries

### Phase 4: Advanced Features (Week 7-8)
- [ ] Full-text token search
- [ ] WebSocket subscriptions for token events
- [ ] Swap order book optimization
- [ ] Performance benchmarks
- [ ] Documentation

## 5. Database Considerations

### 5.1 Column Families (RocksDB)

```python
COLUMN_FAMILIES = [
    'default',           # Standard Electrum data
    'glyph_tokens',      # Token metadata index
    'glyph_balances',    # Address -> token balances
    'glyph_history',     # Token transaction history
    'wave_tree',         # WAVE prefix tree
    'wave_names',        # Name -> ref mapping
    'wave_zones',        # Zone record cache
    'swap_orders',       # Active swap orders
]
```

### 5.2 Compaction Strategy

- Glyph data: Level compaction (read-heavy)
- WAVE tree: Universal compaction (write-then-read)
- Swap orders: FIFO compaction (high churn)

## 6. Migration from ElectrumX

RXinDexer is backward-compatible with existing ElectrumX databases. New indexes are built incrementally:

```bash
# Start with existing ElectrumX database
export DB_DIRECTORY=/path/to/existing/electrumdb

# Enable new indexing features
export GLYPH_INDEX=1
export WAVE_INDEX=1

# Run RXinDexer - it will build new indexes
python3 rxindexer_server
```

Initial index build time: ~2-4 hours depending on chain size.

## 7. Testing

```bash
# Run all tests
pytest tests/

# Run Glyph-specific tests
pytest tests/server/test_glyph_api.py

# Run WAVE tests
pytest tests/server/test_wave_api.py

# Integration tests
pytest tests/integration/
```

## 8. Configuration

New environment variables:

```bash
# Glyph indexing
GLYPH_INDEX=1              # Enable Glyph token indexing
GLYPH_METADATA_CACHE_MB=50 # Metadata cache size

# WAVE indexing
WAVE_INDEX=1               # Enable WAVE name indexing
WAVE_GENESIS_REF=<txid>_0  # WAVE genesis ref (mainnet)

# Swap indexing
SWAP_INDEX=1               # Enable swap order indexing
SWAP_HISTORY_BLOCKS=10000  # Blocks of history to keep
```

## 9. Design Decisions & Defaults

This section documents architectural decisions for a fully-featured yet scalable indexer.

### 9.1 Reorg Handling

| Setting | Value | Rationale |
|---------|-------|-----------|
| **Max Reorg Depth** | 6 blocks | Radiant's roll-back protection ensures finality after 6 confirmations |
| **Undo Buffer** | 6 blocks of Glyph/Swap state changes | Enables clean revert on reorg |
| **Confirmation Threshold** | 1 block (display), 6 blocks (final) | Balance UX vs. safety |

```bash
REORG_LIMIT=6  # Maximum blocks to undo on chain reorganization
```

### 9.2 Mempool Indexing

**Decision: ENABLED** - Essential for wallet/explorer/DEX user experience.

| Feature | Mempool Indexed | Rationale |
|---------|-----------------|-----------|
| Glyph transfers | ✅ Yes | Wallets need unconfirmed balance |
| Swap orders | ✅ Yes | DEX needs real-time orderbook |
| dMint reveals | ❌ No | Prevent gaming; wait for confirmation |
| WAVE claims | ❌ No | Prevent front-running; wait for confirmation |

**Tradeoffs:**
- **Memory:** ~50-200MB additional for mempool index
- **Complexity:** Requires mempool refresh on block arrival
- **Benefit:** Users see pending transactions immediately

```bash
MEMPOOL_GLYPH_INDEX=1   # Index Glyph transfers in mempool
MEMPOOL_SWAP_INDEX=1    # Index swap orders in mempool
```

### 9.3 WebSocket Subscriptions

**Decision: FULL SUBSCRIPTION SUPPORT** - Required for real-time applications.

| Subscription | Method | Use Case |
|--------------|--------|----------|
| Balance changes | `glyph.subscribe.balance(scripthash, ref)` | Wallet balance updates |
| Token state | `glyph.subscribe.token(ref)` | Explorer token pages |
| Token transfers | `glyph.subscribe.transfers(ref)` | Activity feeds |
| Orderbook | `swap.subscribe.orderbook(base_ref, quote_ref)` | DEX trading UI |
| Trade fills | `swap.subscribe.fills(base_ref, quote_ref)` | DEX trade history |
| User orders | `swap.subscribe.user_orders(scripthash)` | User's open orders |
| WAVE name | `wave.subscribe.name(name)` | Name ownership alerts |
| dMint stats | `dmint.subscribe.token(ref)` | Mining dashboard |

**Implementation:** Extend ElectrumX's existing notification system with Glyph/Swap events.

```bash
GLYPH_SUBSCRIPTIONS=1   # Enable Glyph WebSocket subscriptions
SWAP_SUBSCRIPTIONS=1    # Enable Swap WebSocket subscriptions
```

### 9.4 Storage Strategy

| Data Type | Strategy | Rationale |
|-----------|----------|-----------|
| **Token metadata** | Store full CBOR | Small, frequently accessed |
| **Images/icons** | Reference + LRU cache | Store ref only; cache decoded in memory |
| **DAT data** | Hash + lazy load | Large data fetched on-demand from chain |
| **WAVE tree** | Hybrid (hot in memory, cold on disk) | Balance speed vs. memory |
| **dMint stats** | Aggregate only | Per-miner stats = 10x storage, minimal value |
| **Container contents** | Index all items | Essential for explorer navigation |
| **Authority chains** | Validate at index-time | Query-time validation too slow |

```bash
GLYPH_METADATA_CACHE_MB=50    # In-memory metadata cache
GLYPH_IMAGE_CACHE_MB=100      # In-memory decoded image cache
WAVE_HOT_NAMES=10000          # Number of WAVE names to keep in memory
```

### 9.5 API Rate Limiting

**Decision: TIERED LIMITS** - Protect server from expensive queries.

| Query Type | Cost Multiplier | Examples |
|------------|-----------------|----------|
| Simple lookup | 1x | `get_token`, `get_balance` |
| List queries | 2x | `list_tokens`, `get_history` |
| Search queries | 5x | `search_tokens` |
| Orderbook | 3x | `get_orderbook` |
| Bulk queries | 10x | `get_tokens_batch` |

```bash
COST_SOFT_LIMIT=1000   # Requests before throttling
COST_HARD_LIMIT=10000  # Requests before disconnect
GLYPH_SEARCH_COST=5    # Cost multiplier for search queries
```

### 9.6 Database Backend

**Decision: RocksDB (default)**

| Aspect | RocksDB | LevelDB |
|--------|---------|---------|
| RAM usage | ~52% lower | Higher |
| Prefix iterators | Native bloom filters | Emulated |
| Compression | LZ4, Snappy, ZSTD | Snappy only |
| Column families | ✅ Supported | ❌ No |
| Production | ✅ Recommended | Legacy only |

```bash
DB_ENGINE=rocksdb
ROCKSDB_COMPRESSION=lz4
ROCKSDB_BLOCK_CACHE_MB=256
```

### 9.7 Glyph Token Fields (Complete)

All fields indexed for explorers, wallets, and exchanges:

```
Core Identity:
  - ref, protocols, type, name, ticker, decimals
  - description, author, license

Supply Tracking:
  - total_supply, current_supply, premine, mined_supply
  - percent_mined (computed)

Content:
  - icon_ref, icon_type, icon_size
  - embedded_data_hash (for DAT tokens)

dMint Specific:
  - contract_ref, algorithm, start_difficulty, current_difficulty
  - reward, halving_interval, daa_mode, mint_count

Relationships:
  - container_ref, authority_ref, parent_ref

NFT Specific:
  - attrs (attributes array)
```

### 9.8 Swap Order Fields (Complete)

All fields indexed for DEX interfaces and market data:

```
Order Identity:
  - order_id, tx_hash, vout, height, timestamp

Maker Info:
  - maker_scripthash, maker_address

Trading Pair:
  - base_ref, quote_ref, base_ticker, quote_ticker

Order Details:
  - side (buy/sell), price, amount
  - filled_amount, remaining_amount, percent_filled
  - min_fill, fee_rate

Status:
  - status (open/partial/filled/cancelled/expired)
  - expiry_height, cancel_height, cancel_txid

Execution:
  - fill_count, last_fill_height, avg_fill_price

Pair Statistics:
  - last_price, high_24h, low_24h
  - volume_24h_base, volume_24h_quote, trade_count_24h
  - open_order_count, bid_depth, ask_depth
```

## 10. Environment Variable Reference

Complete list of RXinDexer-specific environment variables:

```bash
# === Glyph Indexing ===
GLYPH_INDEX=1                  # Enable Glyph token indexing
GLYPH_METADATA_CACHE_MB=50     # Metadata cache size (MB)
GLYPH_IMAGE_CACHE_MB=100       # Decoded image cache size (MB)
GLYPH_SUBSCRIPTIONS=1          # Enable WebSocket subscriptions
GLYPH_SEARCH_COST=5            # Cost multiplier for search queries

# === WAVE Indexing ===
WAVE_INDEX=1                   # Enable WAVE name indexing
WAVE_GENESIS_REF=<txid>_0      # WAVE genesis ref (mainnet)
WAVE_HOT_NAMES=10000           # Names to keep in memory

# === Swap Indexing ===
SWAP_INDEX=1                   # Enable swap order indexing
SWAP_HISTORY_BLOCKS=10000      # Blocks of history to keep
SWAP_CACHE_MB=10               # Order cache size (MB)
SWAP_SUBSCRIPTIONS=1           # Enable WebSocket subscriptions

# === Mempool Indexing ===
MEMPOOL_GLYPH_INDEX=1          # Index Glyph in mempool
MEMPOOL_SWAP_INDEX=1           # Index swaps in mempool

# === Reorg Handling ===
REORG_LIMIT=6                  # Maximum reorg depth (blocks)

# === Database ===
DB_ENGINE=rocksdb              # Database backend
ROCKSDB_COMPRESSION=lz4        # Compression algorithm
ROCKSDB_BLOCK_CACHE_MB=256     # Block cache size
```

## References

- [Glyph v2 Token Standard Whitepaper](../../../Glyph%20Token%20Standards/Glyph_v2_Token_Standard_Whitepaper.md)
- [REP-3011: WAVE Naming System](../../../REP/REP-3011.md)
- [@radiantblockchain/constants](https://github.com/radiant-core/radiantblockchain-constants)
