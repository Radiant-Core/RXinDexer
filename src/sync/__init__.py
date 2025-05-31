# /Users/radiant/Desktop/RXinDexer/src/sync/__init__.py
# This file makes the sync directory a Python package.
# It exposes the main synchronization functions for external use.

import os
import logging

# Configure logging
logger = logging.getLogger(__name__)

# Import RPC client directly for immediate use
from .rpc_client import RadiantRPC

# Check if we're running in API mode
is_api_mode = os.environ.get('IN_API', 'false').lower() == 'true'

# Use the appropriate SyncManager implementation based on context
if is_api_mode:
    logger.info("Running in API mode, using APISyncManager")
    from .api_sync_manager import APISyncManager as SyncManager
else:
    logger.info("Running in normal mode, using standard SyncManager")
    from .sync_manager import SyncManager

# Export the names for use with from sync import *
__all__ = ['RadiantRPC', 'SyncManager']
