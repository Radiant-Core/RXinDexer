# /Users/radiant/Desktop/RXinDexer/src/sync/process_block.py
# This file contains the process_block implementation for the SyncManager class
# It handles individual block processing with transaction safety

def process_block(self, block_data, height, block_hash):
    """
    Process a single block with transaction safety.
    
    Args:
        block_data: Full block data
        height: Block height
        block_hash: Block hash
        
    Returns:
        bool: True if block was processed successfully
    """
    try:
        # Parse block with the block parser
        self.parser.parse_block(block_data, height, block_hash)
        
        # Update sync state with the new height
        with self.db.begin():
            self.sync_state.current_height = height
            self.sync_state.current_hash = block_hash
            self.sync_state.last_updated_at = time.time()
            self.db.commit()
            
        return True
    except Exception as e:
        # Log the error
        logger.error(f"Failed to process block {height}: {str(e)}")
        # Don't propagate the exception, just return False
        return False
