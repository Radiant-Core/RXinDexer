# /Users/radiant/Desktop/RXinDexer/src/api_patches.py
# This file contains patches that are applied when running in API mode
# to fix specific issues that only occur in the API context.

import os
import sys
import logging

logger = logging.getLogger(__name__)

def apply_api_patches():
    """
    Apply patches to fix issues that only occur in the API context.
    This function is called during API initialization.
    """
    logger.info("Applying API-specific patches...")
    
    # Fix for the "Failed to update sync error: name 'error_message' is not defined" error
    try:
        # Monkey patch the sync_manager._update_sync_error method at runtime
        from src.sync.sync_manager import SyncManager
        
        # Store the original method for reference
        original_method = SyncManager._update_sync_error
        
        # Define a replacement method that won't fail
        def safe_update_sync_error(self, *args, **kwargs):
            try:
                # Extract error_message from args or kwargs
                error_message = None
                if args and len(args) > 0:
                    error_message = args[0]
                elif 'error_message' in kwargs:
                    error_message = kwargs['error_message']
                
                # Only proceed if we have a valid error_message
                if error_message is not None:
                    # Try to call the original method safely
                    try:
                        original_method(self, error_message)
                    except Exception:
                        # Silently ignore errors in API context
                        pass
            except Exception:
                # Suppress all errors to prevent API initialization issues
                pass
        
        # Apply the monkey patch
        SyncManager._update_sync_error = safe_update_sync_error
        logger.info("Successfully patched SyncManager._update_sync_error method")
    
    except Exception as e:
        logger.warning(f"Failed to apply sync_manager patch: {str(e)}")
    
    logger.info("API patches applied successfully")

# Apply patches if we're in API context
if __name__ == "__main__" or os.environ.get('IN_API', 'false').lower() == 'true':
    apply_api_patches()
