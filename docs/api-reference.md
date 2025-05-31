# /Users/radiant/Desktop/RXinDexer/docs/api-reference.md
# This file provides comprehensive documentation for the RXinDexer API endpoints.
# It describes all available API endpoints, their parameters, and response formats.

# RXinDexer API Reference

## Overview

The RXinDexer API provides a comprehensive interface for querying the Radiant blockchain, including transactions, addresses, balances, and Glyph tokens. This reference documents all available endpoints, their parameters, and response formats.

## Base URL

All API endpoints are prefixed with `/api/v1/`. For example, to access the health check endpoint, use:

```
http://<host>:<port>/api/v1/health
```

## Authentication

Currently, the API is accessible without authentication. Rate limiting may be implemented in future versions.

## Response Format

All responses are returned in JSON format. Successful responses will have an appropriate HTTP status code (usually 200 OK) and contain the requested data. Error responses will have an appropriate HTTP status code (4xx or 5xx) and include an error message.

Example error response:
```json
{
  "detail": "Address not found"
}
```

## Endpoints

### Health Check

#### GET /health
#### GET /api/v1/health

Check the health status of the API and its connected components.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": 1748623724.3140604,
  "components": {
    "api": "online",
    "database": "connected"
  }
}
```

### Address Endpoints

#### GET /api/v1/address/{address}/balance

Get the current balance for a specific address.

**Parameters:**
- `address` (path parameter): The Radiant address to query

**Response:**
```json
{
  "address": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
  "rxd_balance": "15.75000000",
  "glyph_tokens": {
    "glyph.token1": "10.0",
    "glyph.token2": "5.0"
  }
}
```

#### GET /api/v1/address/{address}/utxos

Get UTXOs for a specific address.

**Parameters:**
- `address` (path parameter): The Radiant address to query
- `unspent_only` (query parameter, boolean, default: true): Show only unspent UTXOs
- `page` (query parameter, integer, default: 1): Page number for pagination
- `page_size` (query parameter, integer, default: 50): Number of results per page

**Response:**
```json
{
  "address": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
  "utxos": [
    {
      "txid": "7f5d1f8e8a76d32a95601518d183e030e9d73788d79c18583c04adff13092160",
      "vout": 0,
      "amount": "5.00000000",
      "token_ref": null,
      "spent": false,
      "block_height": 329105
    },
    {
      "txid": "8c9547680f8e8a76d32a95601518d183e030e9d7e8d79c18583c04adff1309200",
      "vout": 1,
      "amount": "10.75000000",
      "token_ref": null,
      "spent": false,
      "block_height": 329106
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total_items": 2,
    "total_pages": 1
  }
}
```

#### GET /api/v1/address/{address}/transactions

Get transaction history for a specific address.

**Parameters:**
- `address` (path parameter): The Radiant address to query
- `page` (query parameter, integer, default: 1): Page number for pagination
- `page_size` (query parameter, integer, default: 50): Number of results per page

**Response:**
```json
{
  "address": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
  "transactions": [
    {
      "txid": "7f5d1f8e8a76d32a95601518d183e030e9d73788d79c18583c04adff13092160",
      "block_height": 329105,
      "utxos": [
        {
          "vout": 0,
          "amount": "5.00000000",
          "token_ref": null,
          "spent": false
        }
      ]
    },
    {
      "txid": "8c9547680f8e8a76d32a95601518d183e030e9d7e8d79c18583c04adff1309200",
      "block_height": 329106,
      "utxos": [
        {
          "vout": 1,
          "amount": "10.75000000",
          "token_ref": null,
          "spent": false
        }
      ]
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total_items": 2,
    "total_pages": 1
  }
}
```

### Transactions Endpoints

#### GET /api/v1/transactions/{txid}

Get detailed information about a specific transaction.

**Parameters:**
- `txid` (path parameter): The transaction ID to query

**Response:**
```json
{
  "txid": "7f5d1f8e8a76d32a95601518d183e030e9d73788d79c18583c04adff13092160",
  "block_hash": "000000000000003e695fe357d1abd89b0d8ce74ff1b89a626786f368874c4147",
  "block_height": 329105,
  "block_time": 1748620426,
  "confirmations": 25,
  "fee": "0.00000100",
  "size": 255,
  "inputs": [
    {
      "txid": "5a8d1f8e8a76d32a95601518d183e030e9d73788d79c18583c04adff13092160",
      "vout": 1,
      "address": "rx1qk2e95tp4j962f2c9vq885psy8fmtm3tepkfmz4",
      "value": "5.75000100"
    }
  ],
  "outputs": [
    {
      "n": 0,
      "address": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
      "value": "5.00000000",
      "spent": false
    },
    {
      "n": 1,
      "address": "rx1qk2e95tp4j962f2c9vq885psy8fmtm3tepkfmz4",
      "value": "0.75000000",
      "spent": true
    }
  ]
}
```

### Token Endpoints

#### GET /api/v1/tokens/{token_id}

Get information about a specific Glyph token.

**Parameters:**
- `token_id` (path parameter): The Glyph token ID to query

**Response:**
```json
{
  "token_id": "glyph.token1",
  "name": "Example Token",
  "symbol": "EXTKN",
  "decimals": 8,
  "total_supply": "1000000.00000000",
  "holder_count": 156,
  "metadata": {
    "description": "An example Glyph token",
    "image": "https://example.com/token-image.png",
    "attributes": {
      "type": "fungible",
      "created_at": 1748620000
    }
  }
}
```

#### GET /api/v1/tokens/{token_id}/holders

Get holders of a specific Glyph token.

**Parameters:**
- `token_id` (path parameter): The Glyph token ID to query
- `page` (query parameter, integer, default: 1): Page number for pagination
- `page_size` (query parameter, integer, default: 50): Number of results per page

**Response:**
```json
{
  "token_id": "glyph.token1",
  "holders": [
    {
      "address": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
      "balance": "10.00000000",
      "percentage": "0.001"
    },
    {
      "address": "rx1qk2e95tp4j962f2c9vq885psy8fmtm3tepkfmz4",
      "balance": "250.00000000",
      "percentage": "0.025"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total_items": 156,
    "total_pages": 4
  }
}
```

#### GET /api/v1/tokens/{token_id}/transfers

Get transfer history for a specific token.

**Parameters:**
- `token_id` (path parameter): The token ID to query
- `page` (query parameter, integer, default: 1): Page number for pagination
- `page_size` (query parameter, integer, default: 50): Number of results per page

**Response:**
```json
{
  "token_id": "glyph.token1",
  "transfers": [
    {
      "txid": "7f5d1f8e8a76d32a95601518d183e030e9d73788d79c18583c04adff13092160",
      "block_height": 329105,
      "timestamp": 1748620426,
      "from_address": "rx1qk2e95tp4j962f2c9vq885psy8fmtm3tepkfmz4",
      "to_address": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
      "amount": "10.00000000"
    },
    {
      "txid": "8c9547680f8e8a76d32a95601518d183e030e9d7e8d79c18583c04adff1309200",
      "block_height": 329106,
      "timestamp": 1748620500,
      "from_address": "rx1qwrp539d7j9s8h7pger325gjpwn3f8kmal47tfs",
      "to_address": "rx1qk2e95tp4j962f2c9vq885psy8fmtm3tepkfmz4",
      "amount": "50.00000000"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total_items": 120,
    "total_pages": 3
  }
}
```

### NFT Endpoints

#### GET /api/v1/nfts/{token_id}

Get information about a specific NFT.

**Parameters:**
- `token_id` (path parameter): The NFT token ID to query

**Response:**
```json
{
  "token_id": "glyph.nft123",
  "name": "Example NFT",
  "owner": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
  "collection_id": "collection123",
  "media_url": "https://example.com/nft-image.png",
  "metadata": {
    "description": "A unique Glyph NFT",
    "attributes": [
      {
        "trait_type": "Background",
        "value": "Blue"
      },
      {
        "trait_type": "Rarity",
        "value": "Uncommon"
      }
    ],
    "created_at": 1748620000
  }
}
```

#### GET /api/v1/nfts/collection/{collection_id}

Get NFTs in a specific collection.

**Parameters:**
- `collection_id` (path parameter): The collection ID to query
- `page` (query parameter, integer, default: 1): Page number for pagination
- `page_size` (query parameter, integer, default: 50): Number of results per page

**Response:**
```json
{
  "collection_id": "collection123",
  "name": "Example Collection",
  "nfts": [
    {
      "token_id": "glyph.nft123",
      "name": "Example NFT #1",
      "owner": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
      "media_url": "https://example.com/nft1-image.png"
    },
    {
      "token_id": "glyph.nft124",
      "name": "Example NFT #2",
      "owner": "rx1qk2e95tp4j962f2c9vq885psy8fmtm3tepkfmz4",
      "media_url": "https://example.com/nft2-image.png"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total_items": 100,
    "total_pages": 2
  }
}
```

### User and Container Endpoints

#### GET /api/v1/users/{user_id}

Get information about a specific user profile.

**Parameters:**
- `user_id` (path parameter): The user ID (typically an address) to query

**Response:**
```json
{
  "user_id": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
  "profile": {
    "username": "example_user",
    "avatar": "https://example.com/avatar.png",
    "bio": "Radiant blockchain enthusiast",
    "created_at": 1748620000,
    "last_active": 1748623724
  },
  "activity": {
    "transaction_count": 156,
    "nft_count": 5,
    "container_count": 2
  }
}
```

#### GET /api/v1/containers/{container_id}

Get information about a specific container.

**Parameters:**
- `container_id` (path parameter): The container ID to query

**Response:**
```json
{
  "container_id": "container123",
  "name": "Example Container",
  "owner": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
  "created_at": 1748620000,
  "item_count": 10,
  "metadata": {
    "description": "A container for collectibles",
    "type": "collection",
    "attributes": {
      "visibility": "public",
      "category": "art"
    }
  }
}
```

#### GET /api/v1/containers/{container_id}/contents

Get contents of a specific container.

**Parameters:**
- `container_id` (path parameter): The container ID to query
- `page` (query parameter, integer, default: 1): Page number for pagination
- `page_size` (query parameter, integer, default: 50): Number of results per page

**Response:**
```json
{
  "container_id": "container123",
  "contents": [
    {
      "item_id": "glyph.nft123",
      "type": "nft",
      "name": "Example NFT #1",
      "added_at": 1748620100
    },
    {
      "item_id": "glyph.nft124",
      "type": "nft",
      "name": "Example NFT #2",
      "added_at": 1748620200
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total_items": 10,
    "total_pages": 1
  }
}
```

### Analytics Endpoints

#### GET /api/v1/analytics/richlist

Get the richest addresses by RXD balance.

**Parameters:**
- `limit` (query parameter, integer, default: 100): Number of addresses to return

**Response:**
```json
{
  "addresses": [
    {
      "address": "rx1qvz9qsdp9e4xrx4l79ej9t0qttf26yczfrevlxu",
      "balance": "10000.00000000",
      "percentage": "0.01"
    },
    {
      "address": "rx1qk2e95tp4j962f2c9vq885psy8fmtm3tepkfmz4",
      "balance": "8500.00000000",
      "percentage": "0.0085"
    }
  ],
  "total_supply": "100000000.00000000",
  "timestamp": 1748623724.3140604
}
```

#### GET /api/v1/analytics/activity

Get network activity metrics.

**Parameters:**
- `period` (query parameter, string, default: "24h"): Time period for metrics (1h, 24h, 7d, 30d)

**Response:**
```json
{
  "period": "24h",
  "transaction_count": 12568,
  "active_addresses": 3452,
  "volume": "125698.75000000",
  "average_fee": "0.00001250",
  "timestamp": 1748623724.3140604
}
```

#### GET /api/v1/analytics/metrics

Get time-series metrics for various data points.

**Parameters:**
- `type` (query parameter, string, required): Metric type (transactions, addresses, volume)
- `interval` (query parameter, string, default: "1d"): Time interval (1h, 1d, 1w, 1m)
- `start_time` (query parameter, integer, optional): Start timestamp
- `end_time` (query parameter, integer, optional): End timestamp

**Response:**
```json
{
  "type": "transactions",
  "interval": "1d",
  "metrics": [
    {
      "timestamp": 1748537324,
      "value": 12458
    },
    {
      "timestamp": 1748623724,
      "value": 12568
    }
  ],
  "total": 25026
}
```

## Error Codes

| HTTP Status Code | Description |
|------------------|-------------|
| 400 | Bad Request - The request was invalid |
| 404 | Not Found - The specified resource was not found |
| 429 | Too Many Requests - Rate limit exceeded |
| 500 | Internal Server Error - Something went wrong on the server |

## Rate Limiting

Currently, there are no rate limits enforced. Future versions may implement rate limiting to ensure fair usage.

## Versioning

The current API version is v1. Future versions will be available at `/api/v2/`, etc.

## Support

For issues or questions about the API, please contact the RXinDexer team or open an issue in the GitHub repository.
