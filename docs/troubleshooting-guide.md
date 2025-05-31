# /Users/radiant/Desktop/RXinDexer/docs/troubleshooting-guide.md
# This guide provides solutions to common issues encountered when running RXinDexer.
# It includes diagnostic steps, error messages, and recommended fixes.

# RXinDexer Troubleshooting Guide

This document provides solutions to common issues encountered when running RXinDexer. It includes diagnostic steps, error messages, and recommended fixes.

## Table of Contents

1. [Database Connection Issues](#database-connection-issues)
2. [RPC Connection Problems](#rpc-connection-problems)
3. [Sync Process Failures](#sync-process-failures)
4. [API Service Issues](#api-service-issues)
5. [Performance Problems](#performance-problems)
6. [Docker Environment Issues](#docker-environment-issues)
7. [Balance Calculation Issues](#balance-calculation-issues)
8. [Token Indexing Problems](#token-indexing-problems)

## Database Connection Issues

### Symptoms

- API service fails to start with database connection errors
- Logs show `OperationalError: could not connect to server`
- Sync process cannot write to database

### Diagnostic Steps

1. **Check database status**:
   ```bash
   # For PostgreSQL
   systemctl status postgresql
   # For Docker deployment
   docker-compose ps rxindexer-db
   ```

2. **Verify connection string**:
   - Check the `DATABASE_URL` in your `.env` file
   - Ensure hostname, port, username, and password are correct

3. **Test database connection manually**:
   ```bash
   psql -U username -h hostname -p port -d database_name
   ```

4. **Check database logs**:
   ```bash
   # For PostgreSQL
   tail -f /var/log/postgresql/postgresql-16-main.log
   # For Docker deployment
   docker-compose logs rxindexer-db
   ```

### Common Solutions

1. **Start the database service**:
   ```bash
   systemctl start postgresql
   # Or for Docker
   docker-compose start rxindexer-db
   ```

2. **Fix connection parameters**:
   - Update `DATABASE_URL` in your `.env` file
   - For Docker deployment, ensure service names match in `docker-compose.yml`

3. **Adjust PostgreSQL configuration**:
   - Ensure `postgresql.conf` has appropriate `listen_addresses`
   - Check `pg_hba.conf` for correct client authentication settings

4. **Recreate the database** (if corrupted):
   ```bash
   dropdb rxindexer
   createdb rxindexer
   python -m src.db.init_db
   ```

## RPC Connection Problems

### Symptoms

- Errors like `Failed to connect to Radiant node`
- Sync process stalls or fails with RPC errors
- Health check shows RPC status as disconnected

### Diagnostic Steps

1. **Check Radiant node status**:
   ```bash
   # For local node
   radiant-cli getblockcount
   # For Docker deployment
   docker-compose exec rxindexer-radiant radiant-cli getblockcount
   ```

2. **Verify RPC configuration**:
   - Check the `RADIANT_RPC_URL`, `RADIANT_RPC_USER`, and `RADIANT_RPC_PASSWORD` in your `.env` file
   - Ensure the RPC port is accessible

3. **Test RPC connection manually**:
   ```bash
   curl -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"1.0","id":"test","method":"getblockcount","params":[]}' \
     http://username:password@hostname:port/
   ```

4. **Check RPC logs**:
   ```bash
   # For local node
   tail -f ~/.radiant/debug.log
   # For Docker deployment
   docker-compose logs rxindexer-radiant
   ```

### Common Solutions

1. **Start the Radiant node**:
   ```bash
   # For local node
   radiantd
   # For Docker deployment
   docker-compose start rxindexer-radiant
   ```

2. **Fix RPC configuration**:
   - Update `radiant.conf` with correct RPC settings:
     ```
     rpcuser=your_username
     rpcpassword=your_password
     rpcallowip=127.0.0.1
     rpcbind=0.0.0.0
     server=1
     ```
   - Restart the Radiant node after changing configuration

3. **Check network connectivity**:
   - Ensure firewall rules allow RPC port access
   - For Docker, check network definitions in `docker-compose.yml`

4. **Implement connection retry logic**:
   - The RPC client should already have retry logic built in
   - Adjust `RADIANT_MAX_RETRIES` and `CONNECTION_RETRY_DELAY` in `.env`

## Sync Process Failures

### Symptoms

- Sync process stops unexpectedly
- Error messages related to block processing
- Incomplete or stalled blockchain synchronization

### Diagnostic Steps

1. **Check sync logs**:
   ```bash
   # For local deployment
   tail -f logs/sync.log
   # For Docker deployment
   docker-compose logs --tail=100 -f rxindexer-indexer
   ```

2. **Verify database state**:
   ```sql
   -- Connect to database and check sync status
   SELECT * FROM sync_status ORDER BY height DESC LIMIT 1;
   ```

3. **Examine specific error messages** in the logs for clues

4. **Check system resources**:
   ```bash
   htop  # CPU and memory usage
   df -h # Disk space
   ```

### Common Solutions

1. **Restart the sync process**:
   ```bash
   # For local deployment
   python -m sync.rxindex_sync --continuous
   # For Docker deployment
   docker-compose restart rxindexer-indexer
   ```

2. **Reduce batch size**:
   - Set `SYNC_BATCH_SIZE` to a smaller value (e.g., 100) in `.env`
   - Restart the sync process

3. **Fix database inconsistencies**:
   ```sql
   -- Identify the last valid block
   SELECT height FROM blocks ORDER BY height DESC LIMIT 10;
   
   -- Truncate from that point if necessary
   DELETE FROM blocks WHERE height > 123456; -- Replace with actual height
   DELETE FROM transactions WHERE block_height > 123456;
   DELETE FROM utxos WHERE block_height > 123456;
   ```

4. **Increase log verbosity**:
   - Set `LOG_LEVEL=DEBUG` in `.env`
   - Restart the sync process to get more detailed logs

5. **Run the refresh balances function** to recalculate balances:
   ```sql
   SELECT refresh_balances_now();
   ```

## API Service Issues

### Symptoms

- API endpoints return errors
- Service won't start or crashes
- Performance degradation

### Diagnostic Steps

1. **Check API logs**:
   ```bash
   # For local deployment
   tail -f logs/api.log
   # For Docker deployment
   docker-compose logs -f rxindexer-api
   ```

2. **Test health endpoint**:
   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/api/v1/health
   ```

3. **Check API process status**:
   ```bash
   # For systemd service
   systemctl status rxindexer-api
   # For Docker
   docker-compose ps rxindexer-api
   ```

### Common Solutions

1. **Restart the API service**:
   ```bash
   # For systemd service
   systemctl restart rxindexer-api
   # For Docker
   docker-compose restart rxindexer-api
   ```

2. **Adjust worker configuration**:
   - For Gunicorn, modify the `-w` parameter in the service file
   - For Docker, adjust the environment variable `API_WORKERS`

3. **Check for code errors**:
   - Review recent code changes
   - Check for syntax errors in Python files
   - Verify API endpoint implementation

4. **Enable API debug mode**:
   - Set `DEBUG=True` in `.env`
   - Restart the service for more detailed error output

## Performance Problems

### Symptoms

- Slow API response times
- High CPU or memory usage
- Database query timeouts

### Diagnostic Steps

1. **Monitor system resources**:
   ```bash
   htop
   iostat -x 1
   ```

2. **Check database performance**:
   ```sql
   -- Look for slow queries
   SELECT query, calls, total_time, mean_time
   FROM pg_stat_statements
   ORDER BY mean_time DESC
   LIMIT 10;
   
   -- Check for missing indexes
   SELECT relname, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch
   FROM pg_stat_user_tables
   ORDER BY seq_scan DESC;
   ```

3. **Monitor API response times**:
   ```bash
   # Using curl with timing
   curl -w "\nTime: %{time_total}s\n" http://localhost:8000/api/v1/address/rx1...
   ```

### Common Solutions

1. **Optimize database indexes**:
   ```sql
   -- Example: Add index on address for faster lookups
   CREATE INDEX IF NOT EXISTS idx_utxos_address ON utxos(address);
   ```

2. **Implement or tune caching**:
   - Enable Redis caching by setting `ENABLE_CACHE=true` in `.env`
   - Adjust `CACHE_TTL_SECONDS` for appropriate cache lifetime

3. **Optimize batch size parameters**:
   - Adjust `SYNC_BATCH_SIZE` based on system capacity
   - Set `SYNC_MAX_WORKERS` to match available CPU cores

4. **Database maintenance**:
   ```sql
   -- Analyze tables for query optimization
   VACUUM ANALYZE;
   
   -- Rebuild indexes
   REINDEX TABLE utxos;
   ```

5. **Optimize PostgreSQL configuration**:
   - Increase `shared_buffers` for more caching
   - Adjust `work_mem` for complex query operations
   - Set appropriate `max_connections` based on load

## Docker Environment Issues

### Symptoms

- Docker containers fail to start
- Services can't communicate with each other
- Volume mounting issues

### Diagnostic Steps

1. **Check container status**:
   ```bash
   docker-compose ps
   ```

2. **Examine container logs**:
   ```bash
   docker-compose logs rxindexer-api
   docker-compose logs rxindexer-indexer
   ```

3. **Inspect Docker networks**:
   ```bash
   docker network ls
   docker network inspect rxindexer_default
   ```

4. **Check volume mounts**:
   ```bash
   docker volume ls
   docker inspect rxindexer-db-data
   ```

### Common Solutions

1. **Rebuild containers**:
   ```bash
   docker-compose down
   docker-compose build
   docker-compose up -d
   ```

2. **Fix network issues**:
   - Ensure service names in `docker-compose.yml` match references in `.env`
   - Check that containers can resolve each other by hostname

3. **Address volume problems**:
   - Ensure proper permissions on host directories
   - Verify volume paths in `docker-compose.yml`
   ```bash
   # Fix permissions if needed
   sudo chown -R 5432:5432 /path/to/postgres/data
   ```

4. **Clean up Docker environment**:
   ```bash
   # Remove unused containers/networks/volumes
   docker system prune
   
   # For a complete reset (warning: removes ALL data)
   docker-compose down -v
   docker-compose up -d
   ```

## Balance Calculation Issues

### Symptoms

- Incorrect address balances
- Missing token balances
- Discrepancies between calculated and expected values

### Diagnostic Steps

1. **Check balance calculation in database**:
   ```sql
   -- View balance for specific address
   SELECT * FROM wallet_holders WHERE address = 'rx1...';
   
   -- View token balances
   SELECT * FROM token_balances WHERE address = 'rx1...';
   ```

2. **Examine UTXO data**:
   ```sql
   -- Count UTXOs for address
   SELECT COUNT(*), SUM(amount) FROM utxos WHERE address = 'rx1...' AND spent = false;
   ```

3. **Verify balance refresh function**:
   ```sql
   -- Check if the function exists
   SELECT routine_name FROM information_schema.routines 
   WHERE routine_type = 'FUNCTION' AND routine_name = 'refresh_balances_now';
   ```

### Common Solutions

1. **Run the balance refresh function**:
   ```sql
   SELECT refresh_balances_now();
   ```

2. **Fix missing database functions**:
   ```bash
   # Ensure database functions are applied
   python -m src.db.init_db --apply-functions
   ```

3. **Repair inconsistent UTXO data**:
   ```sql
   -- Mark double-spent outputs correctly
   UPDATE utxos 
   SET spent = true 
   WHERE id IN (
     SELECT u1.id 
     FROM utxos u1
     JOIN transactions t ON u1.txid = t.txid
     JOIN inputs i ON i.txid = t.txid AND i.vout = u1.vout
   );
   ```

4. **Rescan specific address ranges** (if available in your implementation):
   ```bash
   python -m sync.address_scanner --address rx1... --rescan
   ```

## Token Indexing Problems

### Symptoms

- Missing token data
- Incorrect token metadata
- Failed token parsing

### Diagnostic Steps

1. **Check token tables in database**:
   ```sql
   -- View token records
   SELECT * FROM tokens LIMIT 10;
   
   -- Check token transactions
   SELECT * FROM token_transfers LIMIT 10;
   ```

2. **Examine parser logs**:
   ```bash
   grep "token parser" logs/sync.log
   ```

3. **Verify CBOR decoder functionality**:
   - Review the implementation in `src/sync/parser/glyph_parser.py`
   - Check for errors related to CBOR decoding

### Common Solutions

1. **Update token parsing logic**:
   - Ensure the latest CBOR parsing code is deployed
   - Check for changes in token format specifications

2. **Rescan token transactions**:
   ```bash
   # If your implementation supports selective rescanning
   python -m sync.token_scanner --rescan-all
   ```

3. **Fix token metadata**:
   ```sql
   -- Update token metadata from JSON
   UPDATE tokens SET metadata = '{"name": "Example Token", "symbol": "EXT", "decimals": 8}'::jsonb
   WHERE token_id = 'glyph.token1';
   ```

4. **Check for NFT-specific issues**:
   - Verify media URL storage and retrieval
   - Ensure collection relationships are correctly established

## Additional Resources

- **API Documentation**: [/docs/api-reference.md](/docs/api-reference.md)
- **Deployment Guide**: [/docs/deployment-guide.md](/docs/deployment-guide.md)
- **Development Guide**: [/docs/development-guide.md](/docs/development-guide.md)
- **GitHub Issues**: [https://github.com/Radiant-Core/RXinDexer/issues](https://github.com/Radiant-Core/RXinDexer/issues)

If you encounter an issue not covered in this guide, please report it on GitHub with detailed steps to reproduce the problem.
