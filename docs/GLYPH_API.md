# RXinDexer Glyph v2 API Reference

## Overview

RXinDexer extends the ElectrumX protocol with comprehensive Glyph v2 token indexing and querying capabilities. All methods are available through the standard JSON-RPC interface.

## Core Glyph Methods

### glyph.get_token

Get token information by Glyph ID.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `glyph_id` | string | Token ID in format `txid:vout` |

**Returns:**
```json
{
  "glyph_id": "abc123...def:0",
  "txid": "abc123...def",
  "vout": 0,
  "value": 100000000,
  "version": 2,
  "is_reveal": true
}
```

---

### glyph.get_by_ref

Get all UTXOs containing a specific reference.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | 36-byte reference in hex (72 characters) |

**Returns:** Array of UTXOs

---

### glyph.validate_protocols

Validate a protocol combination per Glyph v2 rules.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `protocols` | array | List of protocol IDs |

**Returns:**
```json
{
  "valid": true,
  "protocol_names": ["Fungible Token", "Decentralized Minting"],
  "token_type": "dMint FT"
}
```

---

### glyph.get_protocol_info

Get information about all Glyph v2 protocols.

**Parameters:** None

**Returns:** Protocol definitions dict

---

### glyph.parse_envelope

Parse a Glyph envelope from script hex.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `script_hex` | string | Script in hexadecimal |

**Returns:** Parsed envelope dict

---

## Index Methods

### glyph.get_token_info

Get full token information from the Glyph index.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | Token ref in format `txid_vout` |

**Returns:**
```json
{
  "ref": "abc123...def_0",
  "name": "My Token",
  "ticker": "MTK",
  "protocols": [1, 4],
  "token_type": "dMint FT",
  "decimals": 8,
  "total_supply": 21000000,
  "minted": 1500000,
  "metadata_hash": "..."
}
```

---

### glyph.get_balance

Get token balance for a scripthash.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `scripthash` | string | Address scripthash (64 hex chars) |
| `ref` | string | Token ref in format `txid_vout` |

**Returns:**
```json
{
  "confirmed": 1000000000,
  "unconfirmed": 0
}
```

---

### glyph.list_tokens

List all tokens held by a scripthash.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `scripthash` | string | Address scripthash (64 hex chars) |
| `limit` | int | Maximum results (default 100) |

**Returns:**
```json
[
  {
    "ref": "abc123...def_0",
    "name": "Token A",
    "balance": 1000000000
  },
  {
    "ref": "xyz789...ghi_1",
    "name": "NFT #42",
    "balance": 1
  }
]
```

---

### glyph.get_history

Get transaction history for a token.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | Token ref in format `txid_vout` |
| `limit` | int | Maximum results (default 100) |
| `offset` | int | Pagination offset (default 0) |

**Returns:**
```json
[
  {
    "tx_hash": "...",
    "height": 123456,
    "type": "transfer",
    "amount": 1000000
  }
]
```

---

### glyph.search_tokens

Search tokens by name or ticker.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `query` | string | Search query string |
| `protocols` | array | Optional list of protocol IDs to filter |
| `limit` | int | Maximum results (default 50) |

**Returns:** Array of matching tokens

---

### glyph.get_tokens_by_type

Get tokens by type.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `token_type` | int | GlyphTokenType ID (1=FT, 2=NFT, etc.) |
| `limit` | int | Maximum results (default 100) |
| `offset` | int | Pagination offset (default 0) |

**Returns:** Array of tokens

---

### glyph.get_metadata

Get full CBOR metadata for a token.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | Token ref in format `txid_vout` |

**Returns:** Parsed metadata dict

---

## dMint Contract Methods

### dmint.get_contracts

Get list of mineable dMint contracts.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `format` | string | `simple` for [[ref, outputs], ...] or `extended` for full details |

**Returns (simple):**
```json
[
  ["abc123...def0123456789...", 100],
  ["xyz789...ghi0987654321...", 50]
]
```

**Returns (extended):**
```json
[
  {
    "ref": "abc123...def_0",
    "name": "Mining Token",
    "ticker": "MINE",
    "algorithm": 1,
    "difficulty": 12345678,
    "reward": 50000000,
    "remaining_supply": 19500000
  }
]
```

---

### dmint.get_contract

Get details for a specific dMint contract.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | Contract reference (72 hex chars) |

**Returns:** Contract details dict

---

### dmint.get_by_algorithm

Get contracts filtered by mining algorithm.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `algorithm` | int | Algorithm ID (0=SHA256D, 1=BLAKE3, etc.) |

**Returns:** Array of contracts

---

### dmint.get_most_profitable

Get contracts sorted by estimated profitability.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `limit` | int | Maximum contracts (default 10) |

**Returns:** Sorted array of contracts

---

## Protocol IDs

| ID | Name | Description |
|----|------|-------------|
| 1 | GLYPH_FT | Fungible Token |
| 2 | GLYPH_NFT | Non-Fungible Token |
| 3 | GLYPH_DAT | Data Storage |
| 4 | GLYPH_DMINT | Decentralized Minting |
| 5 | GLYPH_MUT | Mutable State |
| 6 | GLYPH_BURN | Explicit Burn |
| 7 | GLYPH_CONTAINER | Container/Collection |
| 8 | GLYPH_ENCRYPTED | Encrypted Content |
| 9 | GLYPH_TIMELOCK | Timelocked Reveal |
| 10 | GLYPH_AUTHORITY | Issuer Authority |
| 11 | GLYPH_WAVE | WAVE Naming |

## dMint Algorithm IDs

| ID | Name | Description |
|----|------|-------------|
| 0 | SHA256D | Double SHA-256 |
| 1 | BLAKE3 | BLAKE3 hash |
| 2 | K12 | KangarooTwelve |
| 3 | ARGON2ID_LIGHT | Argon2id (light) |
| 4 | RANDOMX_LIGHT | RandomX (light) |

## Method Costs

| Method | Cost |
|--------|------|
| glyph.get_token | 1.0 |
| glyph.get_by_ref | 2.0 |
| glyph.validate_protocols | 0.1 |
| glyph.get_protocol_info | 0.1 |
| glyph.parse_envelope | 0.5 |
| glyph.get_token_info | 1.0 |
| glyph.get_balance | 1.0 |
| glyph.list_tokens | 2.0 |
| glyph.get_history | 2.0 |
| glyph.search_tokens | 3.0 |
| glyph.get_tokens_by_type | 2.0 |
| glyph.get_metadata | 1.5 |
| dmint.get_contracts | 1.0 |
| dmint.get_contract | 1.0 |
| dmint.get_by_algorithm | 1.5 |
| dmint.get_most_profitable | 2.0 |

## Error Handling

Methods return `null` for not found items or an error dict:

```json
{
  "error": "Glyph indexing not enabled"
}
```

## WebSocket Subscriptions

RXinDexer supports WebSocket subscriptions for real-time token updates:

- `glyph.subscribe_token` - Subscribe to token state changes
- `glyph.subscribe_balance` - Subscribe to balance changes for an address

---

*Reference: [Glyph v2 Token Standard](https://github.com/Radiant-Core/Glyph-Token-Standards)*
