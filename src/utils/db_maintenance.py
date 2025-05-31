# /Users/radiant/Desktop/RXinDexer/src/utils/db_maintenance.py
# This module provides scheduled database maintenance to keep performance optimal
# It runs periodic tasks like materialized view refreshes and statistics updates

import logging
import time
import os
import schedule
import threading
import argparse
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get database connection from environment or use default
DB_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/rxindexer')

class DatabaseMaintainer:
    """
    Handles scheduled database maintenance tasks to ensure optimal performance.
    Runs periodic tasks like materialized view refreshes, statistics updates,
    and monitors for potential issues.
    """
    
    def __init__(self, db_url=None):
        """Initialize the database maintainer with connection parameters"""
        self.db_url = db_url or DB_URL
        self.engine = create_engine(self.db_url)
        self.Session = sessionmaker(bind=self.engine)
        self.running = False
        self.thread = None
        self.last_run = {
            'refresh_views': datetime.now() - timedelta(hours=1),
            'update_stats': datetime.now() - timedelta(hours=1),
            'vacuum': datetime.now() - timedelta(days=1),
            'monitor': datetime.now() - timedelta(minutes=30)
        }
    
    def refresh_materialized_views(self):
        """Refresh all materialized views to ensure fresh query results"""
        logger.info("Running scheduled materialized view refresh")
        start_time = time.time()
        
        try:
            with self.Session() as session:
                # Refresh the address balances materialized view
                session.execute(text("SELECT refresh_balances_now()"))
                session.commit()
                
            duration = time.time() - start_time
            logger.info(f"Successfully refreshed materialized views in {duration:.2f}s")
            self.last_run['refresh_views'] = datetime.now()
            return True
        except Exception as e:
            logger.error(f"Error refreshing materialized views: {str(e)}")
            return False
    
    def update_statistics(self):
        """Update database statistics for better query planning"""
        logger.info("Running scheduled statistics update")
        start_time = time.time()
        
        try:
            with self.Session() as session:
                # Update statistics for key tables
                tables = ['utxos', 'holders', 'glyph_tokens']
                for table in tables:
                    session.execute(text(f"ANALYZE {table}"))
                    logger.info(f"Updated statistics for {table}")
                session.commit()
                
            duration = time.time() - start_time
            logger.info(f"Successfully updated database statistics in {duration:.2f}s")
            self.last_run['update_stats'] = datetime.now()
            return True
        except Exception as e:
            logger.error(f"Error updating statistics: {str(e)}")
            return False
    
    def run_vacuum(self):
        """Run VACUUM to reclaim space and update statistics"""
        logger.info("Running scheduled VACUUM operation")
        start_time = time.time()
        
        try:
            # Connect with autocommit because VACUUM can't run in a transaction
            connection = self.engine.raw_connection()
            cursor = connection.cursor()
            
            # Run VACUUM ANALYZE on key tables
            tables = ['utxos', 'holders', 'glyph_tokens']
            for table in tables:
                logger.info(f"Running VACUUM ANALYZE on {table}")
                cursor.execute(f"VACUUM ANALYZE {table}")
            
            cursor.close()
            connection.close()
            
            duration = time.time() - start_time
            logger.info(f"Successfully completed VACUUM operations in {duration:.2f}s")
            self.last_run['vacuum'] = datetime.now()
            return True
        except Exception as e:
            logger.error(f"Error during VACUUM operation: {str(e)}")
            return False
    
    def monitor_database_health(self):
        """Check database health and performance metrics"""
        logger.info("Running database health check")
        
        try:
            with self.Session() as session:
                # Check for long-running queries
                long_queries = session.execute(text("""
                    SELECT pid, query, EXTRACT(EPOCH FROM (NOW() - query_start)) AS duration_sec
                    FROM pg_stat_activity
                    WHERE state = 'active'
                      AND query NOT LIKE '%pg_stat_activity%'
                      AND query_start < NOW() - INTERVAL '30 seconds'
                """)).fetchall()
                
                if long_queries:
                    logger.warning(f"Found {len(long_queries)} long-running queries")
                    for query in long_queries:
                        logger.warning(f"Query running for {query.duration_sec:.1f}s: {query.query[:100]}...")
                
                # Check cache hit ratio
                cache_stats = session.execute(text("""
                    SELECT 
                        sum(heap_blks_read) as heap_read,
                        sum(heap_blks_hit) as heap_hit,
                        sum(heap_blks_hit) / (sum(heap_blks_hit) + sum(heap_blks_read)) as ratio
                    FROM pg_statio_user_tables;
                """)).fetchone()
                
                if cache_stats and cache_stats.ratio:
                    logger.info(f"Cache hit ratio: {cache_stats.ratio:.2%}")
                    if cache_stats.ratio < 0.90:
                        logger.warning("Cache hit ratio is below 90% - consider increasing shared_buffers")
                
                # Check for bloat in tables
                table_stats = session.execute(text("""
                    SELECT 
                        schemaname, 
                        relname, 
                        n_dead_tup, 
                        n_live_tup,
                        CASE WHEN n_live_tup > 0 THEN n_dead_tup::float / n_live_tup ELSE 0 END as dead_ratio
                    FROM pg_stat_user_tables
                    WHERE n_live_tup > 1000
                    ORDER BY dead_ratio DESC
                    LIMIT 5
                """)).fetchall()
                
                for stat in table_stats:
                    if stat.dead_ratio > 0.2:  # More than 20% dead tuples
                        logger.warning(f"Table {stat.relname} has {stat.dead_ratio:.1%} dead tuples - consider VACUUM")
                
            self.last_run['monitor'] = datetime.now()
            return True
        except Exception as e:
            logger.error(f"Error monitoring database health: {str(e)}")
            return False
    
    def run_all_maintenance(self):
        """Run all maintenance tasks in sequence"""
        logger.info("Running all database maintenance tasks")
        
        self.refresh_materialized_views()
        self.update_statistics()
        self.run_vacuum()
        self.monitor_database_health()
        
        logger.info("Completed all database maintenance tasks")
    
    def start_scheduler(self):
        """Start the maintenance scheduler in a background thread"""
        if self.running:
            logger.warning("Scheduler is already running")
            return False
        
        # Schedule regular maintenance tasks
        schedule.every(10).minutes.do(self.refresh_materialized_views)
        schedule.every(30).minutes.do(self.update_statistics)
        schedule.every(1).days.do(self.run_vacuum)
        schedule.every(5).minutes.do(self.monitor_database_health)
        
        def run_scheduler():
            self.running = True
            logger.info("Database maintenance scheduler started")
            
            while self.running:
                schedule.run_pending()
                time.sleep(1)
            
            logger.info("Database maintenance scheduler stopped")
        
        # Start the scheduler in a background thread
        self.thread = threading.Thread(target=run_scheduler, daemon=True)
        self.thread.start()
        return True
    
    def stop_scheduler(self):
        """Stop the maintenance scheduler"""
        if not self.running:
            logger.warning("Scheduler is not running")
            return False
        
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None
        
        logger.info("Database maintenance scheduler stopped")
        return True


def main():
    """Main function for running the database maintainer from command line"""
    parser = argparse.ArgumentParser(description='Database maintenance utility')
    parser.add_argument('--run-once', action='store_true', help='Run maintenance once and exit')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon with scheduled maintenance')
    parser.add_argument('--refresh-views', action='store_true', help='Only refresh materialized views')
    parser.add_argument('--update-stats', action='store_true', help='Only update statistics')
    parser.add_argument('--vacuum', action='store_true', help='Only run VACUUM')
    parser.add_argument('--monitor', action='store_true', help='Only run health monitoring')
    
    args = parser.parse_args()
    maintainer = DatabaseMaintainer()
    
    if args.run_once:
        maintainer.run_all_maintenance()
    elif args.refresh_views:
        maintainer.refresh_materialized_views()
    elif args.update_stats:
        maintainer.update_statistics()
    elif args.vacuum:
        maintainer.run_vacuum()
    elif args.monitor:
        maintainer.monitor_database_health()
    elif args.daemon:
        maintainer.start_scheduler()
        try:
            # Keep the main thread alive
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            maintainer.stop_scheduler()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
