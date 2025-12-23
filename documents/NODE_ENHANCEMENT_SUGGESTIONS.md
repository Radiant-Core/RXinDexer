# Node Enhancement Suggestions for Swap Tracking

**Created:** 2025-12-11
**Purpose:** Document suggested enhancements to the Radiant node to improve swap/trade tracking efficiency

---

## Overview

The Photonic Wallet implements atomic swaps using **Partially Signed Radiant Transactions (PSRT)**. Currently, detecting and tracking these swaps requires parsing raw transaction data and analyzing signature types. Node-level enhancements could significantly improve indexer efficiency.

---

## Current Swap Mechanism

### How PSRT Works

1. **Seller creates PSRT:**
   - Moves tokens/RXD to a dedicated `swapAddress`
   - Signs with `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY | SIGHASH_FORKID`
   - This signature only commits to one input→output pair

2. **PSRT structure:**
   ```
   Input[0]: Seller's token/RXD (signed with SIGHASH_SINGLE|ANYONECANPAY)
   Output[0]: What seller wants in return (token/RXD to seller's address)
   ```

3. **Buyer completes:**
   - Adds their input(s) to provide what seller wants
   - Adds output(s) to receive seller's token/RXD
   - Signs their inputs normally
   - Broadcasts completed transaction

### Signature Flags Used

```
SIGHASH_SINGLE     = 0x03  // Sign only corresponding output
SIGHASH_ANYONECANPAY = 0x80  // Allow others to add inputs
SIGHASH_FORKID     = 0x40  // Radiant fork ID
Combined: 0xC3 (195 decimal)
```

---

## Suggested Node Enhancements

### 1. PSRT Transaction Indexing

**Problem:** Identifying swap transactions requires parsing every transaction's scriptSig to check signature hash types.

**Suggestion:** Add an index or flag for transactions containing PSRT-style signatures.

```cpp
// In transaction validation/indexing
bool HasPartialSignature(const CTransaction& tx) {
    for (const auto& input : tx.vin) {
        // Check if any input uses SIGHASH_SINGLE | SIGHASH_ANYONECANPAY
        uint8_t hashType = GetSigHashType(input.scriptSig);
        if ((hashType & 0x83) == 0x83) {  // SINGLE | ANYONECANPAY
            return true;
        }
    }
    return false;
}
```

**New RPC method:**
```
getpsrttransactions [start_height] [end_height]
```
Returns transactions containing partial signatures within block range.

---

### 2. Mempool Swap Tracking

**Problem:** Pending swap offers exist as unbroadcast PSRTs shared off-chain. There's no way to discover available swaps without a centralized order book.

**Suggestion:** Add optional mempool support for incomplete PSRTs.

**Option A: PSRT Relay Network**
- New P2P message type for PSRT offers
- Nodes can optionally relay PSRTs
- Indexers can query pending offers

**Option B: OP_RETURN Order Book**
- Standard format for publishing swap offers on-chain
- Seller broadcasts small tx with OP_RETURN containing:
  - Offer ID
  - Token ref being sold
  - Amount
  - Desired token/RXD amount
  - PSRT hash or location

```
OP_RETURN <"SWAP"> <version> <from_ref> <from_amount> <to_ref> <to_amount> <psrt_hash>
```

---

### 3. Token Reference Queries

**Problem:** Finding all UTXOs for a specific token ref requires scanning the entire UTXO set.

**Current:** ElectrumX provides `blockchain.ref.get` but it's limited.

**Suggestion:** Enhanced RPC methods:

```
# Get all UTXOs containing a specific token ref
gettokenutxos <ref> [include_spent]

# Get token transfer history
gettokenhistory <ref> [start_height] [limit]

# Get current holders of a token
gettokenholders <ref> [limit]
```

---

### 4. Burn Detection

**Problem:** Detecting token burns requires comparing inputs vs outputs for every transaction.

**How burns work:**
- Token UTXO is spent
- No output contains the same token ref
- Token "disappears" from UTXO set

**Suggestion:** Track burn events at the node level:

```cpp
// During transaction validation
void TrackTokenBurns(const CTransaction& tx) {
    std::set<std::string> inputRefs = GetInputTokenRefs(tx);
    std::set<std::string> outputRefs = GetOutputTokenRefs(tx);
    
    for (const auto& ref : inputRefs) {
        if (outputRefs.find(ref) == outputRefs.end()) {
            // Token ref in input but not in any output = burned
            LogTokenBurn(ref, tx.GetHash(), GetInputAmount(tx, ref));
        }
    }
}
```

**New RPC method:**
```
gettokenburns <ref> [start_height] [end_height]
```

---

### 5. Price Oracle / Trade Aggregation

**Problem:** Calculating token prices requires analyzing completed swap transactions.

**Suggestion:** Node-level trade tracking:

```
# Get recent trades for a token
gettokentrades <ref> [limit]

# Get aggregated price data
gettokenprice <ref> [period: 1h|24h|7d]
```

Response:
```json
{
  "ref": "abc123...",
  "last_price_rxd": 0.00001234,
  "volume_24h": 1000000,
  "trades_24h": 42,
  "high_24h": 0.00001500,
  "low_24h": 0.00001000
}
```

---

## Implementation Priority

### High Priority (Most Impact)
1. **PSRT Transaction Indexing** - Enables efficient swap detection
2. **Token Reference Queries** - Essential for holder tracking

### Medium Priority
3. **Burn Detection** - Improves supply accuracy
4. **Mempool Swap Tracking** - Enables real-time order book

### Lower Priority (Nice to Have)
5. **Price Oracle** - Can be done at indexer level

---

## Alternative: Indexer-Only Approach

If node modifications aren't feasible, the indexer can implement all tracking by:

1. **Parsing all transactions** for signature types
2. **Maintaining token ref → UTXO mappings** in PostgreSQL
3. **Comparing input/output refs** for burn detection
4. **Aggregating trade data** from completed swaps

This is more resource-intensive but doesn't require node changes.

---

## References

- Photonic Wallet swap implementation: `packages/app/src/swap.ts`
- PSRT creation: `packages/lib/src/transfer.tsx:208-228`
- Signature flags: `@radiantblockchain/radiantjs` crypto module
