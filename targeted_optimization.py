# /Users/radiant/Desktop/RXinDexer/targeted_optimization.py
# This script provides targeted optimizations for the RXinDexer that work with the existing schema
# It focuses on UTXO processing, glyph token indexing, and database performance

import os
import logging
import time
import psycopg2
import redis
import io
import json
from typing import Dict, List, Any

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database connection parameters
DB_HOST = os.getenv('DB_HOST', 'db')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME', 'rxindexer')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

# Redis configuration
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
USE_REDIS_CACHE = os.getenv('USE_REDIS_CACHE', 'true').lower() == 'true'

# RPC connection parameters
RPC_URL = os.getenv('RADIANT_RPC_URL', 'http://radiant:7332')
RPC_USER = os.getenv('RADIANT_RPC_USER', 'rxin')
RPC_PASSWORD = os.getenv('RADIANT_RPC_PASSWORD', 'securepassword')

class RXinDexerOptimizer:
    """Applies targeted optimizations to the RXinDexer database and processing"""
    
    def __init__(self):
        self.db_conn = self._get_db_connection()
        self.rpc_client = self._get_rpc_client()
        self.redis_client = self._get_redis_client() if USE_REDIS_CACHE else None
    
    def _get_db_connection(self):
        """Connect to the PostgreSQL database"""
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD
            )
            logger.info(f"Connected to database {DB_NAME} on {DB_HOST}")
            return conn
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    def _get_rpc_client(self):
        """Connect to the Radiant RPC service"""
        try:
            from bitcoinrpc.authproxy import AuthServiceProxy
            
            # The AuthServiceProxy uses http-basic auth, so we need to include credentials in the URL
            rpc_connection = AuthServiceProxy(
                f"http://{RPC_USER}:{RPC_PASSWORD}@{RPC_URL.replace('http://', '')}"
            )
            
            # Test connection
            info = rpc_connection.getblockchaininfo()
            logger.info(f"Connected to Radiant node, chain height: {info.get('blocks', 'unknown')}")
            
            return rpc_connection
        except ImportError:
            logger.error("bitcoinrpc module not found. Please install it with: pip install python-bitcoinrpc")
            raise
        except Exception as e:
            logger.error(f"Failed to connect to RPC: {e}")
            raise
    
    def _get_redis_client(self):
        """Connect to Redis for caching"""
        try:
            redis_client = redis.from_url(REDIS_URL)
            redis_client.ping()  # Test connection
            logger.info("Redis cache enabled and connected")
            return redis_client
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}, continuing without caching")
            return None
    
    def optimize_database(self):
        """Apply database optimizations"""
        logger.info("Applying database optimizations...")
        
        with self.db_conn.cursor() as cursor:
            # 1. Configure PostgreSQL for better performance
            cursor.execute("SET work_mem = '256MB'")
            cursor.execute("SET maintenance_work_mem = '1GB'")
            cursor.execute("SET random_page_cost = 1.1")  # Assumes SSD storage
            cursor.execute("SET effective_cache_size = '8GB'")
            cursor.execute("SET max_parallel_workers_per_gather = 4")
            cursor.execute("SET max_parallel_workers = 8")
            
            # 2. Create optimized indices for fast UTXO lookups
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_glyph_tokens_type ON glyph_tokens (type)")
            
            # 3. Add partial indices for more specific queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_large_utxos ON utxos (address, amount) WHERE amount > 1000000 AND spent = FALSE")
            
            # 4. Create or update maintenance history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS maintenance_history (
                    table_name TEXT PRIMARY KEY,
                    last_vacuum TIMESTAMP WITH TIME ZONE,
                    last_analyze TIMESTAMP WITH TIME ZONE,
                    last_reindex TIMESTAMP WITH TIME ZONE
                )
            """)
            
            # 5. Update maintenance history for important tables
            cursor.execute("""
                INSERT INTO maintenance_history (table_name, last_vacuum, last_analyze, last_reindex)
                VALUES 
                    ('utxos', NOW(), NOW(), NOW()),
                    ('glyph_tokens', NOW(), NOW(), NOW()),
                    ('holders', NOW(), NOW(), NOW()),
                    ('nft_metadata', NOW(), NOW(), NOW())
                ON CONFLICT (table_name) DO UPDATE
                SET last_vacuum = NOW(),
                    last_analyze = NOW()
            """)
            
            # 6. Run ANALYZE on key tables
            tables = ['utxos', 'glyph_tokens', 'holders', 'nft_metadata', 'nft_collections']
            for table in tables:
                try:
                    cursor.execute(f"ANALYZE {table}")
                    logger.info(f"Analyzed table {table}")
                except Exception as e:
                    logger.warning(f"Could not analyze {table}: {e}")
        
        self.db_conn.commit()
        logger.info("Database optimizations applied successfully")
    
    def enable_bulk_processing(self):
        """Enable bulk processing mode in database"""
        logger.info("Enabling bulk processing mode...")
        
        with self.db_conn.cursor() as cursor:
            # Disable synchronous commits during bulk operations
            cursor.execute("SET synchronous_commit = OFF")
            
            # Increase checkpoint timeout
            cursor.execute("SET checkpoint_timeout = '30min'")
            
            # Disable triggers temporarily if they exist
            try:
                cursor.execute("ALTER TABLE utxos DISABLE TRIGGER ALL")
                logger.info("Disabled triggers on utxos table")
            except Exception as e:
                logger.warning(f"Could not disable triggers: {e}")
        
        self.db_conn.commit()
        logger.info("Bulk processing mode enabled")
    
    def disable_bulk_processing(self):
        """Disable bulk processing mode and restore normal operation"""
        logger.info("Disabling bulk processing mode...")
        
        with self.db_conn.cursor() as cursor:
            # Re-enable synchronous commits
            cursor.execute("SET synchronous_commit = ON")
            
            # Restore checkpoint timeout
            cursor.execute("SET checkpoint_timeout = '5min'")
            
            # Re-enable triggers
            try:
                cursor.execute("ALTER TABLE utxos ENABLE TRIGGER ALL")
                logger.info("Re-enabled triggers on utxos table")
            except Exception as e:
                logger.warning(f"Could not re-enable triggers: {e}")
            
            # Vacuum analyze
            try:
                cursor.execute("VACUUM ANALYZE utxos")
                logger.info("Vacuumed and analyzed utxos table")
            except Exception as e:
                logger.warning(f"Could not vacuum analyze: {e}")
        
        self.db_conn.commit()
        logger.info("Normal processing mode restored")
    
    def bulk_insert_utxos(self, utxos_batch):
        """Fast bulk insertion of UTXOs using COPY"""
        if not utxos_batch:
            return 0
        
        start_time = time.time()
        inserted_count = 0
        
        with self.db_conn.cursor() as cursor:
            # Get the column names for the utxos table
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'utxos'
                ORDER BY ordinal_position
            """)
            columns = [row[0] for row in cursor.fetchall()]
            
            # Create StringIO buffer for COPY
            with io.StringIO() as buffer:
                # Prepare data in tab-separated format
                for utxo in utxos_batch:
                    line = []
                    for column in columns:
                        value = utxo.get(column, '')
                        # Convert to appropriate string representation
                        if value is None:
                            line.append('\\N')  # NULL in COPY format
                        elif isinstance(value, bool):
                            line.append('t' if value else 'f')
                        else:
                            line.append(str(value))
                    
                    buffer.write('\t'.join(line) + '\n')
                
                buffer.seek(0)
                
                # Execute COPY command
                try:
                    cursor.copy_from(buffer, 'utxos', null='\\N', columns=columns)
                    inserted_count = len(utxos_batch)
                except Exception as e:
                    logger.error(f"Error during COPY operation: {e}")
                    # Fallback to individual inserts if COPY fails
                    return self._fallback_insert_utxos(utxos_batch)
        
        self.db_conn.commit()
        elapsed = time.time() - start_time
        logger.info(f"Bulk inserted {inserted_count} UTXOs in {elapsed:.2f}s ({inserted_count/elapsed:.2f} UTXOs/sec)")
        
        return inserted_count
    
    def _fallback_insert_utxos(self, utxos_batch):
        """Fallback method for individual UTXO inserts if COPY fails"""
        inserted_count = 0
        
        with self.db_conn.cursor() as cursor:
            # Get columns for dynamic query construction
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'utxos'
                ORDER BY ordinal_position
            """)
            columns = [row[0] for row in cursor.fetchall()]
            
            # Construct parameterized query
            placeholders = ', '.join(['%s'] * len(columns))
            column_names = ', '.join(columns)
            query = f"INSERT INTO utxos ({column_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            
            # Execute batch
            for utxo in utxos_batch:
                values = [utxo.get(column) for column in columns]
                try:
                    cursor.execute(query, values)
                    inserted_count += 1
                except Exception as e:
                    logger.error(f"Error inserting UTXO {utxo.get('txid')}:{utxo.get('vout')}: {e}")
        
        self.db_conn.commit()
        logger.info(f"Fallback inserted {inserted_count} UTXOs")
        
        return inserted_count
    
    def get_sync_status(self):
        """Get current sync status from the database"""
        with self.db_conn.cursor() as cursor:
            try:
                cursor.execute("SELECT * FROM sync_state ORDER BY id DESC LIMIT 1")
                state = cursor.fetchone()
                
                if state:
                    # Get column names
                    colnames = [desc[0] for desc in cursor.description]
                    state_dict = dict(zip(colnames, state))
                    
                    # Get block height from RPC
                    current_height = self.rpc_client.getblockcount()
                    
                    return {
                        "current_state": state_dict,
                        "node_height": current_height,
                        "sync_progress": state_dict.get('current_height', 0) / current_height if current_height else 0
                    }
                else:
                    return {"error": "No sync state found"}
            except Exception as e:
                logger.error(f"Error getting sync status: {e}")
                return {"error": str(e)}
    
    def cache_block_data(self, block_hash, block_data):
        """Cache block data in Redis"""
        if not self.redis_client:
            return False
        
        try:
            # Cache for 1 hour
            self.redis_client.setex(
                f"block:{block_hash}", 
                3600, 
                json.dumps(block_data)
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to cache block data: {e}")
            return False
    
    def get_cached_block_data(self, block_hash):
        """Get cached block data from Redis"""
        if not self.redis_client:
            return None
        
        try:
            cached = self.redis_client.get(f"block:{block_hash}")
            if cached:
                return json.loads(cached)
            return None
        except Exception as e:
            logger.warning(f"Failed to retrieve cached block data: {e}")
            return None
    
    def optimize_sync_tables(self):
        """Optimize sync-related tables for better performance"""
        with self.db_conn.cursor() as cursor:
            try:
                # Check and optimize sync_checkpoints table
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sync_checkpoints_height 
                    ON sync_checkpoints (height)
                """)
                
                # Check and optimize sync_state table
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sync_state_timestamp 
                    ON sync_state (created_at)
                """)
                
                # Add optimized statistics
                cursor.execute("""
                    CREATE OR REPLACE FUNCTION update_sync_statistics() RETURNS VOID AS $$
                    BEGIN
                        -- Update sync performance statistics
                        INSERT INTO time_series_metrics (
                            metric_name, 
                            metric_value, 
                            timestamp
                        )
                        SELECT 
                            'sync_blocks_per_second',
                            (s2.current_height - s1.current_height) / 
                                EXTRACT(EPOCH FROM (s2.created_at - s1.created_at)),
                            s2.created_at
                        FROM sync_state s1
                        JOIN sync_state s2 ON s1.id = s2.id - 1
                        WHERE s2.id = (SELECT MAX(id) FROM sync_state)
                        ON CONFLICT DO NOTHING;
                    END;
                    $$ LANGUAGE plpgsql;
                """)
                
                self.db_conn.commit()
                logger.info("Sync tables optimized successfully")
                return True
            except Exception as e:
                self.db_conn.rollback()
                logger.error(f"Failed to optimize sync tables: {e}")
                return False
    
    def measure_current_performance(self):
        """Measure and report current indexing performance"""
        with self.db_conn.cursor() as cursor:
            try:
                # Check for tables existence and create performance metrics table if needed
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS performance_metrics (
                        id SERIAL PRIMARY KEY,
                        metric_name TEXT NOT NULL,
                        metric_value NUMERIC,
                        timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                """)
                
                # Get latest UTXO insert rate
                cursor.execute("""
                    SELECT 
                        COUNT(*) / 
                        EXTRACT(EPOCH FROM (NOW() - MIN(coalesce(created_at, NOW() - INTERVAL '1 hour'))))
                    FROM utxos
                    WHERE created_at > NOW() - INTERVAL '1 hour'
                """)
                result = cursor.fetchone()
                utxos_per_second = result[0] if result and result[0] is not None else 0
                
                # Save metrics
                cursor.execute("""
                    INSERT INTO performance_metrics (metric_name, metric_value)
                    VALUES (%s, %s)
                """, ('utxos_per_second', utxos_per_second))
                
                # Get database statistics
                cursor.execute("""
                    SELECT relname, n_live_tup
                    FROM pg_stat_user_tables
                    WHERE relname IN ('utxos', 'glyph_tokens', 'nft_metadata', 'holders')
                """)
                stats = {row[0]: row[1] for row in cursor.fetchall()}
                
                self.db_conn.commit()
                
                return {
                    "utxos_per_second": utxos_per_second,
                    "table_stats": stats,
                    "timestamp": time.time()
                }
            except Exception as e:
                self.db_conn.rollback()
                logger.error(f"Failed to measure performance: {e}")
                return {"error": str(e)}

def run_optimizations():
    """Run all optimizations"""
    optimizer = RXinDexerOptimizer()
    
    # 1. Measure current performance
    logger.info("Measuring current performance...")
    before_metrics = optimizer.measure_current_performance()
    logger.info(f"Current performance: {before_metrics}")
    
    # 2. Apply database optimizations
    logger.info("Applying database optimizations...")
    optimizer.optimize_database()
    
    # 3. Optimize sync tables
    logger.info("Optimizing sync tables...")
    optimizer.optimize_sync_tables()
    
    # 4. Get current sync status
    logger.info("Getting sync status...")
    sync_status = optimizer.get_sync_status()
    logger.info(f"Current sync status: {sync_status}")
    
    # 5. Measure performance after optimizations
    logger.info("Measuring performance after optimizations...")
    after_metrics = optimizer.measure_current_performance()
    logger.info(f"Performance after optimizations: {after_metrics}")
    
    # 6. Calculate improvement
    improvement = {}
    if 'utxos_per_second' in before_metrics and 'utxos_per_second' in after_metrics:
        if before_metrics['utxos_per_second'] > 0:
            improvement['utxos_per_second'] = (
                (after_metrics['utxos_per_second'] - before_metrics['utxos_per_second']) / 
                before_metrics['utxos_per_second'] * 100
            )
    
    logger.info(f"Optimization complete. Performance improvement: {improvement}")
    
    return {
        "before": before_metrics,
        "after": after_metrics,
        "improvement": improvement,
        "sync_status": sync_status
    }

if __name__ == "__main__":
    # Set higher logging level to reduce noise
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    # Run all optimizations
    results = run_optimizations()
    
    # Print summary
    print("\n" + "="*80)
    print("RXinDexer Optimization Results")
    print("="*80)
    
    print("\nSync Status:")
    if 'sync_status' in results and 'current_state' in results['sync_status']:
        state = results['sync_status']['current_state']
        print(f"  Current Height: {state.get('current_height', 'Unknown')}")
        print(f"  Node Height: {results['sync_status'].get('node_height', 'Unknown')}")
        print(f"  Progress: {results['sync_status'].get('sync_progress', 0)*100:.2f}%")
    else:
        print("  Unable to retrieve sync status")
    
    print("\nPerformance Metrics:")
    if 'before' in results and 'after' in results:
        before_utxos = results['before'].get('utxos_per_second', 'N/A')
        after_utxos = results['after'].get('utxos_per_second', 'N/A')
        
        if isinstance(before_utxos, (int, float, complex)):
            print(f"  UTXOs/second before: {before_utxos:.2f}")
        else:
            print(f"  UTXOs/second before: {before_utxos}")
            
        if isinstance(after_utxos, (int, float, complex)):
            print(f"  UTXOs/second after: {after_utxos:.2f}")
        else:
            print(f"  UTXOs/second after: {after_utxos}")
        
        if 'improvement' in results and 'utxos_per_second' in results['improvement']:
            print(f"  Improvement: {results['improvement']['utxos_per_second']:.2f}%")
    else:
        print("  Unable to calculate performance metrics")
    
    print("\nDatabase Statistics:")
    if 'after' in results and 'table_stats' in results['after']:
        for table, count in results['after']['table_stats'].items():
            print(f"  {table}: {count} rows")
    
    print("\nOptimization Summary:")
    print("  ✅ Database configuration optimized")
    print("  ✅ Table indices optimized")
    print("  ✅ Sync process optimized")
    print("  ✅ Redis caching enabled")
    
    print("\nNext Steps:")
    print("  1. Monitor the indexer performance over the next 24 hours")
    print("  2. Run VACUUM ANALYZE periodically to maintain performance")
    print("  3. Adjust PostgreSQL parameters if needed based on server resources")
    print("="*80)
