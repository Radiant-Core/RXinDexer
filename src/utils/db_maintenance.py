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
    partition management, and monitors for potential issues.
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
            'monitor': datetime.now() - timedelta(minutes=30),
            'partition_maintenance': datetime.now() - timedelta(hours=1)
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
    
    def maintain_partitions(self):
        """Maintain database partitions and ensure they're properly managed"""
        logger.info("Running partition maintenance")
        start_time = time.time()
        
        try:
            with self.engine.connect() as conn:
                # Call the maintain_utxo_partitions function
                result = conn.execute(text("SELECT * FROM maintain_utxo_partitions()"))
                
                # Get partition info for logging
                partitions = conn.execute(
                    text("""
                    SELECT 
                        partition_name, 
                        range_start, 
                        range_end, 
                        row_count,
                        pg_size_pretty(pg_total_relation_size(partition_name::regclass)) as size
                    FROM get_utxo_partition_info()
                    ORDER BY range_start
                    """)
                ).fetchall()
                
                # Log partition info
                logger.info("Current UTXO partitions:")
                for p in partitions:
                    logger.info(f"  - {p.partition_name}: Blocks {p.range_start} to {p.range_end}, "
                               f"{p.row_count} rows, {p.size}")
                
                self.last_run['partition_maintenance'] = datetime.now()
                logger.info(f"Partition maintenance completed in {time.time() - start_time:.2f} seconds")
                return True
                
        except Exception as e:
            logger.error(f"Error during partition maintenance: {str(e)}", exc_info=True)
            return False
            
    def monitor_database_health(self):
        """Monitor database health and performance"""
        logger.info("Running database health check")
        start_time = time.time()
        
        try:
            with self.Session() as session:
                # Check for long-running queries
                long_queries = session.execute("""
                    SELECT 
                        pid, 
                        now() - query_start as duration, 
                        query 
                    FROM pg_stat_activity 
                    WHERE state = 'active' 
                    AND query != '<IDLE>' 
                    AND query NOT LIKE '%pg_stat_activity%'
                    AND now() - query_start > interval '5 minutes'
                    ORDER BY duration DESC;
                """).fetchall()
                
                if long_queries:
                    logger.warning(f"Found {len(long_queries)} long-running queries:")
                    for query in long_queries:
                        logger.warning(f"  - PID {query.pid}: {query.duration} - {query.query[:200]}...")
                
                # Check for locks
                locks = session.execute("""
                    SELECT 
                        blocked_locks.pid AS blocked_pid,
                        blocked_activity.usename AS blocked_user,
                        blocking_locks.pid AS blocking_pid,
                        blocking_activity.usename AS blocking_user,
                        blocked_activity.query AS blocked_statement,
                        blocking_activity.query AS blocking_statement
                    FROM pg_catalog.pg_locks blocked_locks
                    JOIN pg_catalog.pg_stat_activity blocked_activity 
                        ON blocked_activity.pid = blocked_locks.pid
                    JOIN pg_catalog.pg_locks blocking_locks 
                        ON blocking_locks.locktype = blocked_locks.locktype
                        AND blocking_locks.DATABASE IS NOT DISTINCT FROM blocked_locks.DATABASE
                        AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
                        AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
                        AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
                        AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
                        AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
                        AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
                        AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
                        AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
                        AND blocking_locks.pid != blocked_locks.pid
                    JOIN pg_catalog.pg_stat_activity blocking_activity 
                        ON blocking_activity.pid = blocking_locks.pid
                    WHERE NOT blocked_locks.GRANTED;
                """).fetchall()
                
                if locks:
                    logger.warning(f"Found {len(locks)} blocking locks:")
                    for lock in locks:
                        logger.warning(f"  - Blocked PID {lock.blocked_pid} by {lock.blocking_user} (PID: {lock.blocking_pid})")
                        logger.warning(f"    Blocked statement: {lock.blocked_statement[:200]}...")
                        logger.warning(f"    Blocking statement: {lock.blocking_statement[:200]}...")
                
                # Check partition status
                try:
                    partition_status = session.execute("""
                        SELECT 
                            partition_name, 
                            range_start, 
                            range_end, 
                            row_count,
                            pg_size_pretty(pg_total_relation_size(partition_name::regclass)) as size
                        FROM get_utxo_partition_info()
                        ORDER BY range_start
                    """).fetchall()
                    
                    logger.info("Current UTXO partition status:")
                    for p in partition_status:
                        logger.info(f"  - {p.partition_name}: Blocks {p.range_start} to {p.range_end}, "
                                   f"{p.row_count} rows, {p.size}")
                except Exception as e:
                    logger.warning(f"Could not check partition status: {str(e)}")
                
                self.last_run['monitor'] = datetime.now()
                logger.info(f"Database health check completed in {time.time() - start_time:.2f}s")
                return True
                
        except Exception as e:
            logger.error(f"Error during database health check: {str(e)}")
            return False
    
    def run_all_maintenance(self):
        """Run all maintenance tasks in sequence"""
        logger.info("Running all database maintenance tasks")
        
        self.refresh_materialized_views()
        self.update_statistics()
        self.run_vacuum()
        self.monitor_database_health()
        self.maintain_partitions()
        
        # Run every hour
        schedule.every().hour.do(self.refresh_materialized_views)
        
        # Run every 6 hours
        schedule.every(6).hours.do(self.update_statistics)
        
        # Run daily at 2 AM
        schedule.every().day.at("02:00").do(self.run_vacuum)
        
        # Run every 30 minutes
        schedule.every(30).minutes.do(self.monitor_database_health)
        
        # Run partition maintenance every 6 hours
        schedule.every(6).hours.do(self.maintain_partitions)
        
        logger.info("Scheduled all maintenance tasks")
    
    def schedule_tasks(self):
        """Schedule all maintenance tasks"""
        # Run every hour
        schedule.every().hour.do(self.refresh_materialized_views)
        
        # Run every 6 hours
        schedule.every(6).hours.do(self.update_statistics)
        
        # Run daily at 2 AM
        schedule.every().day.at("02:00").do(self.run_vacuum)
        
        # Run every 30 minutes
        schedule.every(30).minutes.do(self.monitor_database_health)
        
        # Run partition maintenance every 6 hours
        schedule.every(6).hours.do(self.maintain_partitions)
        
        logger.info("Scheduled all maintenance tasks")
    
    def start_scheduler(self):
        """Start the maintenance scheduler in a background thread"""
        if self.running:
            logger.warning("Scheduler is already running")
            return False
        
        self.schedule_tasks()
        
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
    parser.add_argument('--partitions', action='store_true', help='Run partition maintenance only')
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
    elif args.partitions:
        maintainer.maintain_partitions()
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
