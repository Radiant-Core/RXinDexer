# Database Maintenance Service

This service handles routine database maintenance tasks for the RXinDexer, including partition management, statistics updates, and performance monitoring.

## Features

- **Automatic Partition Management**: Creates and maintains PostgreSQL table partitions for the `utxos` table
- **Statistics Updates**: Keeps database statistics up-to-date for optimal query planning
- **Vacuum Operations**: Reclaims space and prevents table bloat
- **Health Monitoring**: Tracks long-running queries, locks, and other performance issues
- **Scheduled Maintenance**: Runs maintenance tasks on a configurable schedule

## Configuration

The service is configured via environment variables:

- `DATABASE_URL`: PostgreSQL connection string (default: `postgresql://postgres:postgres@db:5432/rxindexer`)
- `LOG_LEVEL`: Logging level (default: `INFO`)
- `REDIS_HOST`: Redis host for distributed locking (default: `redis`)
- `REDIS_PORT`: Redis port (default: `6379`)
- `ENVIRONMENT`: Environment name (e.g., `production`, `development`)

## Maintenance Tasks

### Partition Management

Partitions are automatically created based on block height ranges (50,000 blocks per partition). The service ensures that:

1. New partitions are created in advance
2. Partitions are properly indexed
3. Partition statistics are up-to-date

### Statistics Updates

Runs `ANALYZE` on key tables to keep statistics current for the query planner.

### Vacuum Operations

Runs `VACUUM ANALYZE` on key tables to reclaim space and update statistics.

### Health Monitoring

Monitors for:

- Long-running queries (>5 minutes)
- Blocking locks
- Partition status and sizes

## Running the Service

### As a Daemon (Production)

```bash
python -m src.utils.db_maintenance --daemon
```

### One-time Maintenance

```bash
# Run all maintenance tasks once
python -m src.utils.db_maintenance

# Run partition maintenance only
python -m src.utils.db_maintenance --partitions
```

## Monitoring

Access the Grafana dashboard at `http://localhost:3000` to monitor:

- UTXO partition sizes and row counts
- Database table sizes
- Query performance metrics
- Long-running queries and locks

## Troubleshooting

Check the logs in the `logs/` directory for detailed information about maintenance operations and any errors that occur.

Common issues:

1. **Permission denied** when accessing database: Ensure the database user has the necessary permissions
2. **Connection refused**: Check if the database is running and accessible
3. **Partition creation failed**: Verify that the `maintain_utxo_partitions()` function exists and is accessible
