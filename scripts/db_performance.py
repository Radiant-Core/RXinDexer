# /Users/radiant/Desktop/RXinDexer/scripts/db_performance.py
# This script monitors PostgreSQL database performance and helps identify CPU bottlenecks.
# It connects to the database and reports on key performance indicators.

import os
import sys
import time
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection parameters
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/rxindexer")

def get_db_connection():
    """Create a connection to the database."""
    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        sys.exit(1)

def check_table_stats(conn):
    """Get statistics about table sizes and index usage."""
    with conn.cursor() as cur:
        # Table sizes
        cur.execute("""
            SELECT relname, 
                   pg_size_pretty(pg_total_relation_size(relid)) as total_size,
                   pg_size_pretty(pg_relation_size(relid)) as table_size,
                   pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) as index_size
            FROM pg_catalog.pg_statio_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT 10;
        """)
        tables = cur.fetchall()
        
        print("\n=== Top 10 Tables by Size ===")
        print(f"{'Table Name':<30} {'Total Size':<15} {'Table Size':<15} {'Index Size':<15}")
        for table in tables:
            print(f"{table[0]:<30} {table[1]:<15} {table[2]:<15} {table[3]:<15}")
        
        # Index usage statistics
        cur.execute("""
            SELECT
                relname AS table_name,
                indexrelname AS index_name,
                idx_scan AS index_scans,
                idx_tup_read AS tuples_read,
                idx_tup_fetch AS tuples_fetched
            FROM
                pg_stat_user_indexes
            JOIN
                pg_stat_user_tables ON pg_stat_user_indexes.relid = pg_stat_user_tables.relid
            ORDER BY
                idx_scan DESC
            LIMIT 10;
        """)
        indexes = cur.fetchall()
        
        print("\n=== Top 10 Most Used Indexes ===")
        print(f"{'Table Name':<20} {'Index Name':<30} {'Scans':<10} {'Tuples Read':<15} {'Tuples Fetched':<15}")
        for idx in indexes:
            print(f"{idx[0]:<20} {idx[1]:<30} {idx[2]:<10} {idx[3]:<15} {idx[4]:<15}")

def check_query_stats(conn):
    """Check for slow queries and high CPU queries."""
    with conn.cursor() as cur:
        # Find slow queries
        cur.execute("""
            SELECT query, calls, total_time, mean_time, rows
            FROM pg_stat_statements
            ORDER BY mean_time DESC
            LIMIT 5;
        """)
        try:
            slow_queries = cur.fetchall()
            
            print("\n=== Top 5 Slowest Queries ===")
            print(f"{'Calls':<10} {'Total Time (ms)':<20} {'Avg Time (ms)':<20} {'Rows':<10} {'Query':<50}")
            for q in slow_queries:
                # Truncate query text for display
                query_text = q[0][:50] + "..." if len(q[0]) > 50 else q[0]
                print(f"{q[1]:<10} {q[2]:<20.2f} {q[3]:<20.2f} {q[4]:<10} {query_text}")
        except Exception as e:
            print(f"Could not retrieve query stats: {e}")
            print("You may need to enable pg_stat_statements extension.")

def check_system_stats(conn):
    """Check system-level statistics."""
    with conn.cursor() as cur:
        # Database activity
        cur.execute("SELECT count(*) FROM pg_stat_activity;")
        connections = cur.fetchone()[0]
        
        # Cache hit ratio
        cur.execute("""
            SELECT 
                sum(heap_blks_read) as heap_read,
                sum(heap_blks_hit)  as heap_hit,
                sum(heap_blks_hit) / (sum(heap_blks_hit) + sum(heap_blks_read)) as ratio
            FROM 
                pg_statio_user_tables;
        """)
        cache_stats = cur.fetchone()
        
        print("\n=== System Statistics ===")
        print(f"Active connections: {connections}")
        if cache_stats[2]:
            cache_hit_ratio = float(cache_stats[2]) * 100
            print(f"Cache hit ratio: {cache_hit_ratio:.2f}%")
            if cache_hit_ratio < 95:
                print("  ⚠️ Cache hit ratio is below 95% - consider increasing shared_buffers")
        
        # Check for bloat
        cur.execute("""
            SELECT count(*) FROM pg_stat_user_tables 
            WHERE n_dead_tup > 10000 OR n_mod_since_analyze > 10000;
        """)
        bloated_tables = cur.fetchone()[0]
        
        if bloated_tables > 0:
            print(f"  ⚠️ {bloated_tables} tables may need VACUUM (high dead tuples or modifications)")

def main():
    print(f"=== PostgreSQL Performance Check === {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    conn = get_db_connection()
    
    # Run checks
    check_system_stats(conn)
    check_table_stats(conn)
    check_query_stats(conn)
    
    # Close connection
    conn.close()
    
    print("\nRecommendations:")
    print("1. If index sizes are much larger than table sizes, consider removing unused indexes")
    print("2. Look for indexes with low scan counts but high storage costs")
    print("3. For high-CPU queries, check if better indexes or query rewrites would help")
    print("4. Consider VACUUM ANALYZE on tables with many dead tuples")

if __name__ == "__main__":
    main()
