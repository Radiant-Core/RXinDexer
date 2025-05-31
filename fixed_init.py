    def __init__(self, db: Session = None):
        """
        Initialize the sync manager with robust error handling.
        
        Args:
            db: Database session (optional, will create one if not provided)
        """
        # Get database session if not provided
        if db is None:
            self.db = next(get_db())
        else:
            self.db = db
            
        # Create RPC client for blockchain communication
        self.rpc = RadiantRPC()
        
        # Ensure database tables exist
        logger.info("Ensuring database tables exist")
        self._ensure_tables_exist()
        logger.info("Database tables created or verified")
        
        # Initialize checkpoint manager
        self.checkpoint_manager = CheckpointManager(self.db)
        
        # CRITICAL FIX: Create a default sync_state first to ensure it's never None
        self.sync_state = SyncState(id=1, current_height=0, is_syncing=0, last_updated_at=time.time())
        
        # Try to get or create a sync state from the database
        try:
            # Get existing sync state or create a new one
            db_state = self.db.query(SyncState).filter(SyncState.id == 1).first()
            
            if db_state is not None:
                # Use the database state if it exists
                self.sync_state = db_state
                logger.info(f"Using database sync state with height {self.sync_state.current_height}")
                
                # Make sure syncing is set to 0 for safety
                if self.sync_state.is_syncing == 1:
                    logger.info("Found sync state with is_syncing=1, resetting")
                    try:
                        # Use a dedicated connection with autocommit to avoid transaction issues
                        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                            conn.execute(text("UPDATE sync_state SET is_syncing = 0 WHERE id = 1"))
                        # Refresh our in-memory copy
                        self.db.refresh(self.sync_state)
                    except Exception as e:
                        logger.error(f"Failed to reset sync state in database: {str(e)}")
                        # Set it in memory if DB update fails
                        self.sync_state.is_syncing = 0
            else:
                # Create new sync state if it doesn't exist
                logger.info("Creating initial sync state")
                try:
                    # Create with the default we've already set up
                    self.db.add(self.sync_state)
                    self.db.commit()
                    self.db.refresh(self.sync_state)
                except Exception as e:
                    logger.error(f"Failed to create sync state in database: {str(e)}")
                    # Continue with our in-memory state
        except Exception as e:
            logger.error(f"Error initializing sync state: {str(e)}")
            logger.warning("Using in-memory sync state as fallback")
                
        # Create block parser
        self.parser = BlockParser(self.rpc, self.db)
        
        # Create parallel processor
        self.parallel_processor = ParallelBlockProcessor(
            self.rpc,
            process_func=self._process_block,
            max_workers=int(os.environ.get("SYNC_MAX_WORKERS", "8"))
        )
