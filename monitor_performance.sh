#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/monitor_performance.sh
# This script monitors PostgreSQL performance and detects slow queries
# It provides real-time metrics to help verify our optimizations

echo "========== RXinDexer Database Performance Monitor =========="
echo "Starting performance monitoring at $(date)"
echo

# Check container resource usage
echo "===== Container Resource Usage ====="
docker stats --no-stream rxindexer-db

# Check for active connections
echo
echo "===== Active Database Connections ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
SELECT 
    datname as database,
    usename as username,
    count(*) as connection_count
FROM pg_stat_activity
GROUP BY datname, usename
ORDER BY count(*) DESC;"

# Check for slow queries
echo
echo "===== Currently Running Queries ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
SELECT 
    pid,
    usename as username,
    datname as database,
    application_name as app,
    client_addr as client,
    EXTRACT(EPOCH FROM (NOW() - query_start)) as duration_sec,
    state,
    LEFT(query, 100) as query_preview
FROM pg_stat_activity
WHERE state = 'active'
  AND pid <> pg_backend_pid()
  AND query NOT LIKE '%pg_stat_activity%'
ORDER BY duration_sec DESC
LIMIT 10;"

# Check materialized view status
echo
echo "===== Materialized View Status ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
SELECT 
    relname as view_name,
    pg_size_pretty(pg_relation_size(oid)) as size,
    (SELECT last_refresh FROM refresh_tracking WHERE view_name = 'address_balances') as last_refresh,
    (SELECT COUNT(*) FROM address_balances) as row_count
FROM pg_class
WHERE relname = 'address_balances';"

# Check index usage
echo
echo "===== Index Usage Statistics ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
SELECT
    t.schemaname || '.' || t.relname as table_name,
    ix.relname as index_name,
    pg_size_pretty(pg_relation_size(i.indexrelid)) as index_size,
    idx_scan as scans,
    idx_tup_read as tuples_read,
    idx_tup_fetch as tuples_fetched
FROM pg_stat_user_indexes i
JOIN pg_index x ON i.indexrelid = x.indexrelid
JOIN pg_class t ON i.relid = t.oid
JOIN pg_class ix ON i.indexrelid = ix.oid
WHERE t.relname = 'utxos' OR t.relname = 'holders'
ORDER BY idx_scan DESC
LIMIT 10;"

# Check for bloat
echo
echo "===== Table Bloat Analysis ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
SELECT
    schemaname || '.' || relname as table_name,
    n_live_tup as live_tuples,
    n_dead_tup as dead_tuples,
    CASE WHEN n_live_tup > 0 THEN 
        ROUND((n_dead_tup::float / n_live_tup::float) * 100, 2) 
    ELSE 0 END as dead_tuple_percentage
FROM pg_stat_user_tables
WHERE (schemaname || '.' || relname) IN ('public.utxos', 'public.holders')
ORDER BY dead_tuple_percentage DESC;"

# Check cache hit ratios
echo
echo "===== Cache Hit Ratios ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
SELECT
    'index cache hit rate' as name,
    ROUND((sum(idx_blks_hit) * 100.0 / (sum(idx_blks_hit) + sum(idx_blks_read))), 2) as ratio
FROM pg_statio_user_indexes
UNION ALL
SELECT
    'table cache hit rate' as name,
    ROUND((sum(heap_blks_hit) * 100.0 / (sum(heap_blks_hit) + sum(heap_blks_read))), 2) as ratio
FROM pg_statio_user_tables;"

# Monitor for temporary tables
echo
echo "===== Temporary Tables ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
SELECT 
    nspname as schema_name,
    relname as table_name,
    pg_size_pretty(pg_relation_size(c.oid)) as size
FROM pg_class c
LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE nspname LIKE 'pg_temp%'
ORDER BY pg_relation_size(c.oid) DESC
LIMIT 10;"

# Check for query plans that are not using indexes
echo
echo "===== Balance Query Plan Check ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
EXPLAIN (ANALYZE, BUFFERS) 
SELECT address, total_balance 
FROM address_balances 
WHERE total_balance > 1000000000;"

echo
echo "===== Original Query Performance Using View ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
EXPLAIN (ANALYZE, BUFFERS)
SELECT address, balance 
FROM optimized_utxos_unspent 
WHERE spent = FALSE 
GROUP BY address 
HAVING SUM(amount) > 1000000000;"

echo
echo "===== Optimized Query Performance Using Function ====="
docker exec rxindexer-db psql -U postgres -d rxindexer -c "
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM get_large_balances_direct(1000000000);"

echo 
echo "========== Monitoring Complete =========="
echo "Monitoring completed at $(date)"
