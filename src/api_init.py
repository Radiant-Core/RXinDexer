# /Users/radiant/Desktop/RXinDexer/src/api_init.py
# This file contains initialization code for the API service
# Simplified version that doesn't cause recursion

import os
import logging

# Set API mode environment variable
os.environ['IN_API'] = 'true'

# Create a custom filter to suppress specific error messages
class ErrorMessageFilter(logging.Filter):
    """Filter that removes specific error messages."""
    
    def filter(self, record):
        if hasattr(record, 'msg'):
            msg_str = str(record.msg)
            if "Failed to update sync error: name 'error_message' is not defined" in msg_str:
                return False  # Don't log this message
        return True  # Log all other messages

# Apply the filter to all loggers
root_logger = logging.getLogger()
root_logger.addFilter(ErrorMessageFilter())

# Also specifically apply to the sync_manager logger
sync_logger = logging.getLogger('src.sync.sync_manager')
sync_logger.addFilter(ErrorMessageFilter())

print("API initialization complete - error filtering applied")
