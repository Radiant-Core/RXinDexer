import logging
from typing import Optional, List
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class PartitionManager:
    """Manages database partitions for blocks, transactions, and utxos."""

    _constraints_setup_done = False

    def ensure_partitions_covering_range(self, min_height: int, max_height: int):
        """
        Ensure all partitions covering the range [min_height, max_height] exist for all tables.
        Should be called before inserting any blocks in that range.
        """
        # Skip partition creation if using unbounded partitioning
        if self.using_unbounded_partitioning:
            logger.info(f"PARTITION MANAGER: Using unbounded partitioning, skipping ensure_partitions_covering_range({min_height}, {max_height})")
            return
            
        try:
            start = (min_height // self.partition_size) * self.partition_size
            partitions_created = 0
            while start <= max_height:
                end = start + self.partition_size - 1
                for table in self.tables:
                    try:
                        self._create_partition(table, start, end)
                        partitions_created += 1
                    except SQLAlchemyError as e:
                        logger.warning(f"PARTITION MANAGER: Error creating partition for {table}_{start}_{end}: {str(e)}")
                start += self.partition_size
                
            if partitions_created > 0:
                logger.info(f"PARTITION MANAGER: Created {partitions_created} partitions for range {min_height}-{max_height}")
            self.session.commit()
        except SQLAlchemyError as e:
            logger.error(f"PARTITION MANAGER: Error in ensure_partitions_covering_range: {str(e)}")
            self.session.rollback()

    def __init__(self, session: Session, partition_size: int = 50000, max_block_height: int = 500000):
        """
        Initialize the partition manager.
        
        Args:
            session: SQLAlchemy session
            partition_size: Size of each partition in blocks
            max_block_height: Maximum block height to create partitions for (increased for future-proofing)
        """
        self.session = session
        self.partition_size = partition_size
        self.max_block_height = max_block_height  # Increased from 300k to 500k for future growth
        self.tables = ['blocks', 'transactions', 'utxos']
        self.using_unbounded_partitioning = self._check_unbounded_partitioning()
        self._ensure_constraints_setup()
        
    def _check_unbounded_partitioning(self) -> bool:
        """
        Check if the database is using unbounded partitioning (initial partitions).
        
        Returns:
            True if using unbounded partitioning, False otherwise
        """
        try:
            # Check if any of the initial partitions exist
            logger.info("PARTITION MANAGER: Checking for unbounded partitioning...")
            for table in self.tables:
                sql_query = f"""SELECT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = '{table}_initial'
                    AND n.nspname = 'public'
                )"""
                logger.debug(f"PARTITION MANAGER: Executing query: {sql_query}")
                
                result = self.session.execute(text(sql_query)).scalar()
                logger.info(f"PARTITION MANAGER: {table}_initial partition exists? {result}")
                
                if result:
                    logger.info(f"PARTITION MANAGER: Unbounded partitioning detected via {table}_initial")
                    self.session.commit()  # Ensure the query doesn't hang in a transaction
                    return True
            
            logger.warning("PARTITION MANAGER: No initial partitions found, using traditional fixed-range partitioning")
            self.session.commit()  # Ensure the query doesn't hang in a transaction
            return False
        except SQLAlchemyError as e:
            logger.error(f"PARTITION MANAGER: Error checking for unbounded partitioning: {str(e)}")
            self.session.rollback()  # Rollback failed transaction
            return False

    def setup_constraints(self) -> None:
        """Set up common constraints for all tables."""
        try:
            # Create function to check block height
            self.session.execute(text("""
                CREATE OR REPLACE FUNCTION check_block_height()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.height < 0 THEN
                        RAISE EXCEPTION 'Block height cannot be negative';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """))

            # Create trigger for blocks table only (not transactions, which lacks 'height' column)
            self.session.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_trigger t
                        JOIN pg_class c ON c.oid = t.tgrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE t.tgname = 'check_blocks_height'
                          AND c.relname = 'blocks'
                          AND n.nspname = 'public'
                    ) THEN
                        CREATE TRIGGER check_blocks_height
                        BEFORE INSERT ON blocks
                        FOR EACH ROW
                        EXECUTE FUNCTION check_block_height();
                    END IF;
                END
                $$;
            """))

            # Create function to check UTXO state
            self.session.execute(text("""
                CREATE OR REPLACE FUNCTION check_utxo_state()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.spent = TRUE AND NEW.spent_in_txid IS NULL THEN
                        RAISE EXCEPTION 'Spent UTXOs must have spent_in_txid set';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """))

            self.session.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_trigger t
                        JOIN pg_class c ON c.oid = t.tgrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE t.tgname = 'check_utxo_state'
                          AND c.relname = 'utxos'
                          AND n.nspname = 'public'
                    ) THEN
                        CREATE TRIGGER check_utxo_state
                        BEFORE INSERT ON utxos
                        FOR EACH ROW
                        EXECUTE FUNCTION check_utxo_state();
                    END IF;
                END
                $$;
            """))

            self.session.commit()
            logger.info("Successfully created constraints and triggers")
        except SQLAlchemyError as e:
            logger.error(f"Error setting up constraints: {str(e)}")
            self.session.rollback()

    def _ensure_constraints_setup(self) -> None:
        if PartitionManager._constraints_setup_done:
            return

        self.setup_constraints()
        PartitionManager._constraints_setup_done = True

    def create_new_partitions(self, current_height: int) -> None:
        """
        Create new partitions for all tables if needed.
        
        Args:
            current_height: Current block height
        """
        # Skip partition creation if using unbounded partitioning
        if self.using_unbounded_partitioning:
            logger.debug("Using unbounded partitioning, skipping fixed-range partition creation")
            return
            
        try:
            # Calculate the next partition range
            next_partition_start = ((current_height // self.partition_size) * self.partition_size)
            next_partition_end = next_partition_start + self.partition_size - 1

            if next_partition_end > self.max_block_height:
                logger.info(f"No new partitions needed beyond max block height {self.max_block_height}")
                return

            for table in self.tables:
                self._create_partition(table, next_partition_start, next_partition_end)

            self.session.commit()
            logger.info(f"Created partitions from {next_partition_start} to {next_partition_end}")
        except SQLAlchemyError as e:
            logger.error(f"Error creating partitions: {str(e)}")
            self.session.rollback()
    
    def create_partitions_ahead(self, current_height: int, look_ahead_partitions: int = 3) -> None:
        """
        Proactively create partitions ahead of the current sync position.
        
        Args:
            current_height: Current block height being synced
            look_ahead_partitions: Number of partition ranges to create ahead (default: 3)
        """
        # Skip partition creation if using unbounded partitioning
        if self.using_unbounded_partitioning:
            logger.debug("Using unbounded partitioning, skipping proactive partition creation")
            return
            
        try:
            # Calculate current partition range
            current_partition_start = (current_height // self.partition_size) * self.partition_size
            partitions_created = 0
            
            # Create partitions for the next N partition ranges
            for i in range(1, look_ahead_partitions + 1):
                partition_start = current_partition_start + (i * self.partition_size)
                partition_end = partition_start + self.partition_size - 1
                
                # Don't create partitions beyond max height
                if partition_start > self.max_block_height:
                    break
                    
                # Check if partitions already exist
                if self._partitions_exist_for_range(partition_start, partition_end):
                    continue
                    
                # Create partitions for all tables
                for table in self.tables:
                    self._create_partition(table, partition_start, partition_end)
                    
                partitions_created += len(self.tables)
                logger.info(f"Proactively created partitions for range {partition_start}-{partition_end}")
            
            if partitions_created > 0:
                self.session.commit()
                logger.info(f"Proactive partition creation completed: {partitions_created} partitions created ahead of sync")
            else:
                logger.debug(f"No new ahead partitions needed (current height: {current_height})")
                
        except SQLAlchemyError as e:
            logger.error(f"Error creating ahead partitions: {str(e)}")
            self.session.rollback()

    def _create_partition(self, table: str, start: int, end: int) -> None:
        """
        Create a new partition for a specific table.
        
        Args:
            table: Table name (blocks, transactions, or utxos)
            start: Start height
            end: End height
        """
        # Additional safety check - if using unbounded partitioning, skip creating new partitions
        if self.using_unbounded_partitioning:
            logger.debug(f"Skipping creation of {table}_{start}_{end} (using unbounded partitioning)")
            return
            
        partition_name = f'{table}_{start}_{end}'
        column = 'height' if table == 'blocks' else 'block_id' if table == 'transactions' else 'transaction_block_height'

        # Check if an initial partition exists to avoid conflicts
        initial_exists = False
        try:
            initial_result = self.session.execute(text(f"""SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = '{table}_initial'
                AND n.nspname = 'public'
            )""")).scalar()
            
            if initial_result:
                logger.warning(f"Initial partition {table}_initial exists, skipping creation of {partition_name}")
                initial_exists = True
        except SQLAlchemyError as e:
            logger.error(f"Error checking for initial partition: {str(e)}")
            # Continue with attempt to create partition (will fail if it conflicts)
            
        if not initial_exists:
            try:
                sql = text(f"""
                    CREATE TABLE IF NOT EXISTS {partition_name} PARTITION OF {table}
                    FOR VALUES FROM ({start}) TO ({end});
                """)

                self.session.execute(sql)
                logger.info(f"Created {table} partition {partition_name}")
            except SQLAlchemyError as e:
                # Log but don't propagate the error - this allows migration to continue
                # even if some partitions can't be created due to conflicts
                logger.warning(f"Could not create partition {partition_name}: {str(e)}")
                # Don't rollback here - let the caller decide if a rollback is needed
    
    def _partitions_exist_for_range(self, start: int, end: int) -> bool:
        """
        Check if partitions already exist for the given range across all tables.
        
        Args:
            start: Start height
            end: End height
            
        Returns:
            True if partitions exist for all tables in the range
        """
        try:
            for table in self.tables:
                partition_name = f'{table}_{start}_{end}'
                result = self.session.execute(text(f"""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_name = '{partition_name}'
                    )
                """))
                if not result.scalar():
                    return False
            return True
        except SQLAlchemyError:
            return False

    def vacuum_partitions(self, partition_start: int, partition_end: int) -> None:
        """
        Vacuum and reindex specific partitions.
        
        Args:
            partition_start: Start height of partitions to maintain
            partition_end: End height of partitions to maintain
        """
        try:
            for table in self.tables:
                partition = f'{table}_{partition_start}_{partition_end}'
                self._vacuum_and_reindex(partition)
            self.session.commit()
        except SQLAlchemyError as e:
            logger.error(f"Error maintaining partitions: {str(e)}")
            self.session.rollback()

    def _vacuum_and_reindex(self, partition: str) -> None:
        """
        Vacuum and reindex a specific partition.
        
        Args:
            partition: Partition name
        """
        try:
            sql = text(f"VACUUM ANALYZE {partition}")
            self.session.execute(sql)
            logger.info(f"Vacuumed partition {partition}")

            sql = text(f"REINDEX TABLE {partition}")
            self.session.execute(sql)
            logger.info(f"Reindexed partition {partition}")
        except SQLAlchemyError as e:
            logger.error(f"Error maintaining partition {partition}: {str(e)}")

    def drop_old_partitions(self, cutoff_height: int) -> None:
        """
        Drop partitions older than the cutoff height.
        
        Args:
            cutoff_height: Height below which partitions will be dropped
        """
        try:
            for i in range(0, cutoff_height, self.partition_size):
                for table in self.tables:
                    partition = f'{table}_{i}_{i + self.partition_size - 1}'
                    self._drop_partition(partition)
            self.session.commit()
        except SQLAlchemyError as e:
            logger.error(f"Error dropping old partitions: {str(e)}")
            self.session.rollback()

    def _drop_partition(self, partition: str) -> None:
        """
        Drop a specific partition.
        
        Args:
            partition: Partition name
        """
        try:
            sql = text(f"DROP TABLE IF EXISTS {partition} CASCADE")
            self.session.execute(sql)
            logger.info(f"Dropped old partition {partition}")
        except SQLAlchemyError as e:
            logger.error(f"Error dropping partition {partition}: {str(e)}")

    def optimize_partitions(self, current_height: int, maintenance_window: int = 100000, enable_proactive: bool = True) -> None:
        """
        Optimize partitions based on current height with optional proactive creation.
        
        Args:
            current_height: Current block height
            maintenance_window: Range of blocks to maintain (vacuum/reindex)
            enable_proactive: Whether to proactively create partitions ahead
        """
        try:
            # Create new partitions if needed (reactive)
            self.create_new_partitions(current_height)
            
            # Proactively create partitions ahead of sync (future-proofing)
            if enable_proactive:
                self.create_partitions_ahead(current_height, look_ahead_partitions=3)

            # Calculate maintenance range
            maintenance_start = current_height - maintenance_window
            if maintenance_start < 0:
                maintenance_start = 0

            # Get all partitions in maintenance range
            partitions = self._get_partitions_in_range(maintenance_start, current_height)

            # Maintain partitions
            for partition in partitions:
                self._vacuum_and_reindex(partition)

            # Drop old partitions
            self.drop_old_partitions(maintenance_start)

            self.session.commit()
            logger.info("Partition optimization completed successfully")
        except SQLAlchemyError as e:
            logger.error(f"Error during partition optimization: {str(e)}")
            self.session.rollback()

    def _get_partitions_in_range(self, start: int, end: int) -> List[str]:
        """
        Get all partition names in a specific range.
        
        Args:
            start: Start height
            end: End height
        
        Returns:
            List of partition names
        """
        partitions = []
        current_start = start
        while current_start < end:
            current_end = min(current_start + self.partition_size - 1, end)
            for table in self.tables:
                partitions.append(f'{table}_{current_start}_{current_end}')
            current_start += self.partition_size
        return partitions
