# /Users/radiant/Desktop/RXinDexer/src/sync/rpc_client_fixed.py
# This file implements the RPC client for communicating with the Radiant Node.
# It provides methods to fetch blocks, transactions, and other blockchain data.

import os
import logging
import time
import random
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from dotenv import load_dotenv
from bitcoinrpc.authproxy import AuthServiceProxy, JSONRPCException

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class RadiantRPC:
    """
    Client for Radiant Node RPC communication with connection pooling.
    Provides methods to fetch blockchain data and handle RPC connection issues.
    Uses a pool of connections to prevent overloading the RPC server with parallel requests.
    """
    
    def __init__(self, pool_size=None):
        """Initialize the RPC connection pool using environment variables.
        
        Args:
            pool_size: Size of the connection pool. If None, will use max_workers from env
        """
        self.rpc_user = os.getenv("RADIANT_RPC_USER", "rxin")
        self.rpc_password = os.getenv("RADIANT_RPC_PASSWORD", "securepassword")
        
        # Check if RADIANT_RPC_URL is a complete URL or just a hostname
        rpc_url = os.getenv("RADIANT_RPC_URL", "localhost")
        
        # Use radiant container name if in Docker
        if os.environ.get("IN_DOCKER", "false").lower() == "true":
            rpc_url = os.getenv("RADIANT_RPC_URL", "radiant")
        
        # If it's a complete URL, use it directly with auth credentials
        if rpc_url.startswith("http://") or rpc_url.startswith("https://"):
            # Parse the URL to get host and port
            from urllib.parse import urlparse
            parsed_url = urlparse(rpc_url)
            self.rpc_host = parsed_url.netloc.split(':')[0]
            self.rpc_port = parsed_url.port or "7332"
            # Construct the full URL with auth credentials
            self.rpc_url = f"{parsed_url.scheme}://{self.rpc_user}:{self.rpc_password}@{self.rpc_host}:{self.rpc_port}"
        else:
            # Handle the case where it's just a hostname
            self.rpc_host = rpc_url
            self.rpc_port = os.getenv("RADIANT_RPC_PORT", "7332")
            self.rpc_url = f"http://{self.rpc_user}:{self.rpc_password}@{self.rpc_host}:{self.rpc_port}"
        
        # Determine pool size based on environment or default value
        if pool_size is None:
            # Use the number of workers for parallel processing
            self.pool_size = int(os.environ.get("SYNC_MAX_WORKERS", "8"))
        else:
            self.pool_size = pool_size
            
        # Initialize connection pool
        self.connections = []
        self.conn_lock = Lock()
        self.initialize_connection_pool()
        
        # Initialize base connection for simple operations
        logger.info(f"Connecting to Radiant Node at {self.rpc_host}:{self.rpc_port} with pool size {self.pool_size}")
        self.rpc = self._connect()
    
    def initialize_connection_pool(self):
        """Initialize the connection pool with multiple RPC connections."""
        try:
            with self.conn_lock:
                # Create connections for the pool
                for _ in range(self.pool_size):
                    conn = self._connect()
                    self.connections.append(conn)
                    # Small delay to prevent overwhelming the node
                    time.sleep(0.1)
                logger.info(f"Initialized RPC connection pool with {self.pool_size} connections")
        except Exception as e:
            logger.error(f"Failed to initialize connection pool: {str(e)}")
            # Continue with at least one connection
            if not self.connections and hasattr(self, 'rpc'):
                self.connections.append(self.rpc)
        
    def _connect(self) -> AuthServiceProxy:
        """Establish connection to the Radiant Node."""
        try:
            logger.info(f"Connecting to Radiant Node at {self.rpc_host}:{self.rpc_port}")
            # Set timeout from environment or use a reasonable default
            timeout = int(os.environ.get("RADIANT_RPC_TIMEOUT", "60"))
            # Add timeout to connection to prevent hanging indefinitely
            return AuthServiceProxy(self.rpc_url, timeout=timeout)
        except Exception as e:
            logger.error(f"Failed to connect to Radiant Node: {str(e)}")
            # Sleep before raising to prevent rapid reconnection attempts
            retry_delay = float(os.environ.get("CONNECTION_RETRY_DELAY", "5"))
            time.sleep(retry_delay)
            raise
    
    def _get_connection(self):
        """
        Get a connection from the pool.
        If the pool is empty, create a new connection.
        
        Returns:
            AuthServiceProxy: An RPC connection
        """
        with self.conn_lock:
            if not self.connections:
                # Pool is empty, create a new connection
                logger.warning("Connection pool is empty, creating a new connection")
                return self._connect()
            else:
                # Get a random connection from the pool to distribute load
                return random.choice(self.connections)
    
    def _return_connection(self, conn):
        """
        Return a connection to the pool.
        If the connection is broken, replace it with a new one.
        
        Args:
            conn: The connection to return
        """
        with self.conn_lock:
            # Check if connection is already in the pool
            if conn in self.connections:
                # Already in the pool, nothing to do
                return
            
            # Add to pool if not full
            if len(self.connections) < self.pool_size:
                self.connections.append(conn)
    
    def _replace_connection(self, old_conn):
        """
        Replace a broken connection with a new one in the pool.
        
        Args:
            old_conn: The broken connection to replace
        
        Returns:
            AuthServiceProxy: The new connection
        """
        with self.conn_lock:
            # Remove old connection from the pool if it exists
            if old_conn in self.connections:
                self.connections.remove(old_conn)
            
            # Create a new connection
            try:
                new_conn = self._connect()
                # Add to pool if not full
                if len(self.connections) < self.pool_size:
                    self.connections.append(new_conn)
                return new_conn
            except Exception as e:
                logger.error(f"Failed to create replacement connection: {str(e)}")
                # Return any connection from the pool or create a basic one
                if self.connections:
                    return random.choice(self.connections)
                else:
                    return self._connect()
    
    def _call_with_retry(self, method: str, *args, max_retries: int = 10, initial_backoff: float = 1.0):
        """
        Call an RPC method with enhanced retry logic and connection pooling.
        
        Args:
            method: RPC method name
            args: Method arguments
            max_retries: Maximum number of retry attempts
            initial_backoff: Initial backoff time in seconds
            
        Returns:
            Result of the RPC call
            
        Raises:
            Exception: If all retry attempts fail
        """
        # Check if max_retries is set in environment
        env_max_retries = os.environ.get("RADIANT_MAX_RETRIES")
        if env_max_retries:
            max_retries = int(env_max_retries)
            
        # Check if initial_backoff is set in environment
        env_backoff = os.environ.get("CONNECTION_RETRY_DELAY")
        if env_backoff:
            initial_backoff = float(env_backoff)
            
        retries = 0
        last_error = None
        backoff = initial_backoff
        conn = self._get_connection()
        
        while retries < max_retries:
            try:
                # Call the method using the connection
                result = getattr(conn, method)(*args)
                # Return connection to the pool on success
                self._return_connection(conn)
                return result
            except JSONRPCException as e:
                # Exponential backoff with jitter for JSONRPCException
                backoff = min(initial_backoff * (2 ** retries) + random.uniform(0, 1.0), 30.0)
                logger.warning(f"RPC call {method} failed (attempt {retries+1}/{max_retries}): {str(e)}")
                
                # Check for 500 errors
                if "500" in str(e) or "Internal Server Error" in str(e):
                    logger.info(f"Detected 500 error, replacing connection")
                    conn = self._replace_connection(conn)
                    
                last_error = e
                retries += 1
                time.sleep(backoff)
                
            except ConnectionError as e:
                # Handle connection errors with longer backoff
                backoff = min(initial_backoff * (2 ** retries) + random.uniform(0, 2.0), 60.0)
                logger.warning(f"Connection error during RPC call {method} (attempt {retries+1}/{max_retries}): {str(e)}")
                last_error = e
                retries += 1
                time.sleep(backoff)
                # Always replace connection on ConnectionError
                conn = self._replace_connection(conn)
                
            except Exception as e:
                # Handle any other exception
                error_str = str(e)
                backoff = min(initial_backoff * (2 ** retries) + random.uniform(0, 1.0), 30.0)
                
                if "request-sent" in error_str.lower():
                    logger.warning(f"Request-sent error during RPC call {method} (attempt {retries+1}/{max_retries}): {error_str}")
                    last_error = e
                    retries += 1
                    time.sleep(backoff * 2)  # Use longer backoff for request-sent errors
                    conn = self._replace_connection(conn)
                else:
                    logger.warning(f"Unexpected error during RPC call {method} (attempt {retries+1}/{max_retries}): {error_str}")
                    last_error = e
                    retries += 1
                    time.sleep(backoff)
                    conn = self._replace_connection(conn)
        
        # If we've exhausted all retries, try to reinitialize the connection pool
        try:
            logger.error(f"Reinitializing the connection pool after persistent failures")
            with self.conn_lock:
                self.connections.clear()
                self.initialize_connection_pool()
                
            # Try one more time with a fresh connection
            conn = self._get_connection()
            result = getattr(conn, method)(*args)
            self._return_connection(conn)
            return result
        except Exception as fresh_error:
            # If it still fails, raise the error
            raise Exception(f"Failed to execute {method} after {max_retries} attempts and connection pool reset: {str(last_error)}")

    def get_block_count(self) -> int:
        """
        Get the current block height.
        
        Returns:
            Current block height
        """
        return self._call_with_retry("getblockcount")
    
    def get_block_hash(self, height: int) -> str:
        """
        Get the block hash for a given height.
        
        Args:
            height: Block height
            
        Returns:
            Block hash
        """
        return self._call_with_retry("getblockhash", height)
    
    def get_block(self, block_hash: str, verbosity: int = 2) -> Dict[str, Any]:
        """
        Get block data with specified verbosity.
        
        Args:
            block_hash: Hash of the block to retrieve
            verbosity: Detail level (0=hex, 1=block data, 2=block with tx data)
            
        Returns:
            Block data
        """
        return self._call_with_retry("getblock", block_hash, verbosity)
    
    def get_raw_transaction(self, txid: str, verbose: bool = True) -> Dict[str, Any]:
        """
        Get transaction data.
        
        Args:
            txid: Transaction ID
            verbose: Whether to return detailed transaction data
            
        Returns:
            Transaction data
        """
        return self._call_with_retry("getrawtransaction", txid, verbose)
    
    def get_glyph_token(self, ref: str) -> Optional[Dict[str, Any]]:
        """
        Get Glyph token data using ref.get RPC method.
        
        Args:
            ref: Token reference
            
        Returns:
            Token data or None if not found
        """
        try:
            return self._call_with_retry("ref.get", ref)
        except JSONRPCException as e:
            if "token not found" in str(e).lower():
                logger.warning(f"Glyph token with ref {ref} not found")
                return None
            raise
    
    def is_node_synced(self) -> bool:
        """
        Check if the Radiant Node is fully synced.
        
        Returns:
            True if synced, False otherwise
        """
        try:
            info = self._call_with_retry("getblockchaininfo")
            return info.get("blocks") == info.get("headers")
        except Exception as e:
            logger.error(f"Failed to check node sync status: {str(e)}")
            return False
