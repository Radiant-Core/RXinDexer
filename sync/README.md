# RXinDexer Consolidated Sync

## Overview

This module provides a high-performance, robust blockchain synchronization solution for the Radiant (RXD) blockchain. It indexes transactions, UTXO states, and Glyph tokens while maintaining consistency and accuracy.

The implementation combines the best features from previous sync implementations:

1. **Transaction Safety**: Uses isolated database connections to avoid transaction conflicts.
2. **Standardized Timestamps**: Consistently uses PostgreSQL's `timestamp without time zone` type with `NOW()` for all timestamp fields.
3. **Bulk Loading**: Employs high-performance database techniques for fast initial sync.
4. **Parallel Processing**: Utilizes thread and process pools for maximum throughput.
5. **Redis Caching**: Optional caching for frequently accessed data to reduce RPC calls.
6. **Bloom Filters**: Fast negative lookups to avoid redundant database queries.
7. **Chain Reorganization Handling**: Detects and handles blockchain reorganizations safely.

## Features

- **Progressive Sync Strategy**: Two-phase sync approach with fast bulk loading followed by incremental updates.
- **Token Balance Tracking**: Maintains accurate balances for all Glyph tokens with JSONB storage.
- **Collection Tracking**: Groups NFTs into collections based on token metadata.
- **Safe Query Design**: Avoids problematic JOIN queries that caused transaction issues in earlier implementations.
- **Materialized Views**: Automatically refreshes materialized views for API optimization.

## Requirements

- Python 3.9+
- PostgreSQL 16
- Radiant Node 1.2.0+
- Redis (optional, for caching)

## Dependencies

```
psycopg2-binary>=2.9.6
cbor2>=5.4.6
redis>=5.0.8
requests>=2.28.2
pybloom-live>=4.0.0
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| DB_HOST | Database host | db |
| DB_PORT | Database port | 5432 |
| DB_NAME | Database name | rxindexer |
| DB_USER | Database username | postgres |
| DB_PASSWORD | Database password | postgres |
| RADIANT_RPC_URL | Radiant node RPC URL | http://radiant:7332 |
| RADIANT_RPC_USER | Radiant node RPC username | rxin |
| RADIANT_RPC_PASSWORD | Radiant node RPC password | securepassword |
| SYNC_BATCH_SIZE | Number of blocks per batch | 1000 |
| SYNC_MAX_WORKERS | Maximum worker threads for block processing | 8 |
| UTXO_MAX_WORKERS | Maximum worker threads for UTXO processing | 4 |
| BLOCK_PARALLEL_THRESHOLD | Minimum block gap to use parallel processing | 100 |
| PROGRESSIVE_SYNC | Whether to use two-phase sync strategy | True |
| INITIAL_SYNC_MINIMAL | Optimize database for initial bulk loading | True |
| USE_REDIS_CACHE | Enable Redis caching | False |
| REDIS_URL | Redis connection URL | redis://redis:6379/0 |
| GLYPH_DEEP_INDEXING | Enable deep indexing of Glyph tokens | True |
| GLYPH_COLLECTION_TRACKING | Group NFTs into collections | True |

## Usage

### Command Line Options

```bash
# Initialize database tables
python rxindex_sync.py --initialize

# Run sync once and exit
python rxindex_sync.py --sync

# Run continuous sync (default behavior)
python rxindex_sync.py --continuous

# Run continuous sync with custom interval (in seconds)
python rxindex_sync.py --continuous --interval 30

# Update token balances only
python rxindex_sync.py --update-balances

# Refresh materialized views only
python rxindex_sync.py --refresh-views
```

### As a Module

```python
from rxindex_sync import RXinDexerSync, initialize_database

# Initialize database if needed
initialize_database()

# Create sync manager
sync_manager = RXinDexerSync()

# Run sync once
sync_manager.run_sync()

# Update token balances
sync_manager.update_token_balances()

# Refresh materialized views
sync_manager.refresh_materialized_views()
```

## Database Schema

The sync module creates and maintains the following tables:

- **blocks**: Stores block headers with height, hash, and timestamp.
- **transactions**: Records all transactions with their block information.
- **utxos**: Tracks the UTXO set with spent status and token references.
- **glyph_tokens**: Stores Glyph token metadata from CBOR-encoded payloads.
- **holders**: Maintains address balances for tokens using JSONB.
- **sync_state**: Tracks the current sync progress.

## Performance Optimization

For large initial syncs, the script:

1. Disables synchronous commits
2. Temporarily drops non-essential indices
3. Disables triggers
4. Uses bulk operations for inserts
5. Recreates indices after sync completes
6. Runs VACUUM ANALYZE for query optimization

## Lessons Learned

This consolidated implementation incorporates lessons from previous sync scripts:

1. **Avoid Transaction Conflicts**: Use autocommit mode and isolated connections.
2. **Standardize Timestamps**: Use PostgreSQL timestamp type consistently.
3. **Avoid Complex JOINs**: Split complex operations into separate transactions.
4. **Optimize Bulk Loading**: Use database-specific optimizations for initial sync.
5. **Handle Chain Reorganizations**: Always verify block hashes and detect forks.

## Contributing

When modifying this sync script, follow these guidelines:

1. Maintain transaction safety with isolated connections.
2. Preserve timestamp consistency across all tables.
3. Test with both small and large block ranges.
4. Verify chain reorganization handling.
5. Ensure proper cleanup of resources.
