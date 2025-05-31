# /Users/radiant/Desktop/RXinDexer/src/sync/rpc_selector.py
# This file selects the appropriate RPC client based on the environment.
# It allows for seamless switching between production and development RPC clients.

import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Get environment setting
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
DEV_MODE = os.getenv("DEV_MODE", "true").lower() == "true"

# Select the appropriate RPC client based on environment
if ENVIRONMENT == "production" or not DEV_MODE:
    try:
        from .rpc_client import RadiantRPC
        logger.info("Using production RPC client with live Radiant node")
    except ImportError as e:
        logger.warning(f"Failed to import production RPC client: {str(e)}")
        logger.warning("Falling back to development RPC client with mock data")
        from .rpc_client_dev import RadiantRPC
else:
    # In development, use the mock RPC client
    try:
        from .rpc_client_dev import RadiantRPC
        logger.info("Using development RPC client with mock data")
    except ImportError as e:
        logger.warning(f"Failed to import development RPC client: {str(e)}")
        logger.warning("Attempting to use production RPC client")
        try:
            from .rpc_client import RadiantRPC
        except ImportError:
            logger.error("No RPC client available. API functionality will be limited.")
            
            # Define a minimal fallback class to prevent crashes
            class RadiantRPC:
                def __init__(self, *args, **kwargs):
                    self.connected = False
                    # Use a more graceful error message
                    try:
                        logger.error("Using dummy RPC client - most API calls will fail")
                    except Exception as e:
                        # Suppress any errors during initialization to prevent API startup issues
                        pass
