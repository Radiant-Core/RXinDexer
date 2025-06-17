import src.sync.rpc_patch
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
from datetime import datetime, timedelta
from dotenv import load_dotenv
from bitcoinrpc.authproxy import AuthServiceProxy, JSONRPCException

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class CircuitBreaker:
    """
    Implements the Circuit Breaker pattern to prevent calls to services
    that are likely to fail, allowing them time to recover.
    """
    
    # Circuit states
    CLOSED = 'closed'      # Normal operation, requests pass through
    OPEN = 'open'         # Service is down, requests fail fast
    HALF_OPEN = 'half_open'  # Testing if service is back up
    
    def __init__(self, failure_threshold=5, reset_timeout=60, half_open_timeout=30):
        """
        Initialize the circuit breaker.
        
        Args:
            failure_threshold: Number of failures before opening circuit
            reset_timeout: Seconds before moving from OPEN to HALF_OPEN
            half_open_timeout: Seconds a successful request keeps circuit HALF_OPEN before CLOSED
        """
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.half_open_timeout = half_open_timeout
        
        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.last_success_time = None
        self.lock = Lock()
    
    def record_failure(self):
        """
        Record a failure and update circuit state if needed.
        """
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            
            # If we've hit the threshold, open the circuit
            if self.state == self.CLOSED and self.failure_count >= self.failure_threshold:
                self.state = self.OPEN
                logger.warning(f"Circuit OPENED after {self.failure_count} failures")
    
    def record_success(self):
        """
        Record a success and update circuit state if needed.
        """
        with self.lock:
            self.last_success_time = datetime.now()
            
            # If we're half open and get a success, close the circuit
            if self.state == self.HALF_OPEN:
                if (datetime.now() - self.last_success_time) > timedelta(seconds=self.half_open_timeout):
                    self.state = self.CLOSED
                    self.failure_count = 0
                    logger.info("Circuit CLOSED after successful recovery")
    
    def allow_request(self):
        """
        Check if a request should be allowed through the circuit.
        
        Returns:
            bool: True if request should be allowed, False otherwise
        """
        with self.lock:
            if self.state == self.CLOSED:
                return True
                
            elif self.state == self.OPEN:
                # Check if it's time to try again
                if self.last_failure_time and \
                   (datetime.now() - self.last_failure_time) > timedelta(seconds=self.reset_timeout):
                    self.state = self.HALF_OPEN
                    logger.info("Circuit moved to HALF-OPEN state")
                    return True
                return False
                
            elif self.state == self.HALF_OPEN:
                # In half-open state, allow only probe requests
                return True
                
            return True


class RadiantRPC:
    """
    Client for Radiant Node RPC communication with connection pooling and circuit breaker.
    Provides methods to fetch blockchain data and handle RPC connection issues.
    Uses a pool of connections to prevent overloading the RPC server with parallel requests.
    Implements circuit breaker pattern to handle node outages gracefully.
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
            # Use environment variable if set, otherwise calculate based on CPU count
            self.pool_size = int(os.environ.get("RPC_POOL_SIZE", 
                min(os.cpu_count() or 2, 8)))  # Cap at 8 connections by default
            logger.info(f"Initializing RPC connection pool with size: {self.pool_size}")
        else:
            self.pool_size = pool_size
            
        # Track connection health metrics
        self.connection_failures = {}
        self.connection_uses = {}
        self.last_request_time = 0
        
        # Configure throttling parameters - retrieved from environment or use safer defaults
        self.min_request_interval = float(os.environ.get("RPC_MIN_REQUEST_INTERVAL", "0.1"))  # 100ms minimum between requests (increased from 50ms)
        self.throttle_factor = float(os.environ.get("RPC_THROTTLE_FACTOR", "1.5"))  # Multiplier for backoff on errors
        
        # Circuit breaker configuration from environment variables
        failure_threshold = int(os.environ.get("CIRCUIT_FAILURE_THRESHOLD", "5"))
        reset_timeout = int(os.environ.get("CIRCUIT_RESET_TIMEOUT", "60"))
        half_open_timeout = int(os.environ.get("CIRCUIT_HALF_OPEN_TIMEOUT", "30"))
        
        # Initialize circuit breaker for node health monitoring
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            reset_timeout=reset_timeout,
            half_open_timeout=half_open_timeout
        )
        
        # Connection management
        self.connections = []
        self.conn_lock = Lock()
        self.total_request_count = 0
        self.successful_request_count = 0
        self.failed_request_count = 0
        self.request_sent_errors = 0
        
        # Health check interval - check node health every N requests
        self.health_check_interval = int(os.environ.get("HEALTH_CHECK_INTERVAL", "100"))
        self.last_health_check_time = datetime.now()
        
        # Initialize connection pool
        self.initialize_connection_pool()
        
        # Initialize base connection for simple operations
        logger.info(f"Connecting to Radiant Node at {self.rpc_host}:{self.rpc_port} with pool size {self.pool_size}")
        try:
            self.rpc = self._connect()
            # Verify connection works
            self.rpc.getblockcount()
            logger.info("Successfully connected to Radiant Node")
        except Exception as e:
            logger.error(f"Failed to connect to Radiant Node: {str(e)}")
            raise
    
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
            timeout = int(os.environ.get("RADIANT_RPC_TIMEOUT", "30"))  # Reduced default timeout
            
            # Add timeout to connection to prevent hanging indefinitely
            conn = AuthServiceProxy(self.rpc_url, timeout=timeout)
            
            # Test the connection with a lightweight call to verify it's working
            try:
                _ = conn.getnetworkinfo()
                logger.info("Successfully established RPC connection")
            except Exception as test_error:
                logger.warning(f"Connection test failed: {str(test_error)}. Will still use connection.")
            
            return conn
        except Exception as e:
            logger.error(f"Failed to connect to Radiant Node: {str(e)}")
            # Sleep before raising to prevent rapid reconnection attempts
            retry_delay = float(os.environ.get("CONNECTION_RETRY_DELAY", "5"))
            time.sleep(retry_delay)
            raise
    
    def _get_connection(self):
        """
        Get a connection from the pool with throttling to prevent overwhelming the server.
        If the pool is empty, create a new connection.
        Implements throttling and connection health tracking to reduce "request-sent" errors.
        
        Returns:
            AuthServiceProxy: A connection to the Radiant Node
        """
        # Throttle requests to prevent overwhelming the RPC server
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time
        if time_since_last_request < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last_request
            time.sleep(sleep_time)
        
        # Update the last request time
        self.last_request_time = time.time()
            
        with self.conn_lock:
            if not self.connections:
                logger.warning("Connection pool is empty, creating a new connection")
                new_conn = self._connect()
                return new_conn
            
            # Find the connection with the fewest failures and least usage
            best_conn = None
            lowest_score = float('inf')
            
            for conn in self.connections:
                # Calculate a score based on connection health and usage
                failure_count = self.connection_failures.get(id(conn), 0)
                use_count = self.connection_uses.get(id(conn), 0)
                
                # Lower score is better (fewer failures and less usage)
                conn_score = failure_count * 10 + use_count
                
                if conn_score < lowest_score:
                    lowest_score = conn_score
                    best_conn = conn
            
            # Track usage
            conn_id = id(best_conn)
            self.connection_uses[conn_id] = self.connection_uses.get(conn_id, 0) + 1
            
            return best_conn
    
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

    def _perform_health_check(self):
        """
        Perform a health check on the RPC node.
        This checks node connection health and refreshes stale connections.
        """
        logger.info("Performing periodic node health check")
        
        try:
            # Get a safe test connection (not from the pool)
            test_conn = self._connect()
            
            # Test basic node functionality
            start_time = time.time()
            block_count = test_conn.getblockcount()
            response_time = time.time() - start_time
            
            # Log health metrics
            logger.info(f"Node health check: Current block height: {block_count}, Response time: {response_time:.3f}s")
            
            # If response time is too high, log a warning
            if response_time > 2.0:
                logger.warning(f"Node response time is high: {response_time:.3f}s")
            
            # Record connection statistics
            success_rate = 0
            if self.total_request_count > 0:
                success_rate = (self.successful_request_count / self.total_request_count) * 100
            
            logger.info(f"Connection stats: Success rate: {success_rate:.1f}%, " 
                        f"Total requests: {self.total_request_count}, "
                        f"Failed: {self.failed_request_count}, "
                        f"Request-sent errors: {self.request_sent_errors}")
            
            # Perform pool maintenance if needed
            self._refresh_connection_pool()
            
            # Reset the last health check time
            self.last_health_check_time = datetime.now()
            
            # If success rate is too low, consider opening circuit breaker
            if self.total_request_count > 100 and success_rate < 60:
                logger.warning(f"Low success rate ({success_rate:.1f}%) detected, recording failures in circuit breaker")
                # Record failures in the circuit breaker
                for _ in range(3):
                    self.circuit_breaker.record_failure()
            
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            # Record failure in circuit breaker
            self.circuit_breaker.record_failure()
    
    def _refresh_connection_pool(self):
        """
        Refresh the connection pool by removing stale connections
        and creating fresh ones as needed.
        """
        try:
            with self.conn_lock:
                # Keep track of connections to replace
                to_replace = []
                
                # Check each connection's health and failure count
                for conn in self.connections:
                    conn_id = id(conn)
                    failures = self.connection_failures.get(conn_id, 0)
                    uses = self.connection_uses.get(conn_id, 0)
                    
                    # Replace connections with high failure counts or excessive use
                    if failures > 5 or uses > 1000:
                        to_replace.append(conn)
                        logger.info(f"Marking connection for replacement: failures={failures}, uses={uses}")
                
                # Replace stale connections
                for old_conn in to_replace:
                    try:
                        self.connections.remove(old_conn)
                        new_conn = self._connect()
                        self.connections.append(new_conn)
                        self.connection_failures[id(new_conn)] = 0
                        self.connection_uses[id(new_conn)] = 0
                        logger.info("Replaced stale connection with fresh one")
                    except Exception as e:
                        logger.warning(f"Failed to replace stale connection: {str(e)}")
                
                # Restore pool to target size if needed
                while len(self.connections) < self.pool_size:
                    try:
                        new_conn = self._connect()
                        self.connections.append(new_conn)
                        self.connection_failures[id(new_conn)] = 0
                        self.connection_uses[id(new_conn)] = 0
                        logger.info("Added new connection to restore pool size")
                    except Exception as e:
                        logger.warning(f"Failed to add connection to pool: {str(e)}")
                        break
                
                logger.info(f"Connection pool refreshed. Current pool size: {len(self.connections)}")
                
        except Exception as e:
            logger.error(f"Error refreshing connection pool: {str(e)}")
            # Don't propagate errors from maintenance tasks
    
    def _call_with_retry(self, method: str, *args, max_retries: int = 5, initial_backoff: float = 0.5):
        """
        Call an RPC method with enhanced retry logic, connection pooling, and circuit breaker pattern.
        Optimized for performance with reduced default retries and backoff.
        
        Args:
            method: RPC method name
            args: Method arguments
            max_retries: Maximum number of retry attempts (reduced from 10 to 5)
            initial_backoff: Initial backoff time in seconds (reduced from 1.0 to 0.5)
            
        Returns:
            The result of the RPC call
            
        Raises:
            Exception: If the call fails after all retries or if circuit is open
        """
        # Update request statistics
        self.total_request_count += 1
        
        # Check if max_retries is set in environment
        env_max_retries = os.environ.get("RPC_MAX_RETRIES")
        if env_max_retries:
            max_retries = int(env_max_retries)
            
        # Check if initial_backoff is set in environment
        env_backoff = os.environ.get("CONNECTION_RETRY_DELAY")
        if env_backoff:
            initial_backoff = float(env_backoff)
        
        # Check circuit breaker status
        if not self.circuit_breaker.allow_request():
            logger.warning(f"Circuit breaker is OPEN - fast failing RPC call to {method}")
            self.failed_request_count += 1
            raise Exception(f"Circuit breaker is open - RPC service considered unavailable")
            
        # Health check if needed
        if self.total_request_count % self.health_check_interval == 0:
            self._perform_health_check()
            
        retries = 0
        last_error = None
        backoff = initial_backoff
        conn = self._get_connection()
        
        while retries < max_retries:
            try:
                # Apply throttling - ensure minimum interval between requests
                current_time = time.time()
                elapsed = current_time - self.last_request_time
                if elapsed < self.min_request_interval:
                    time.sleep(self.min_request_interval - elapsed)
                
                # Call the method using the connection
                result = getattr(conn, method)(*args)
                
                # Record successful request
                self.last_request_time = time.time()
                self.successful_request_count += 1
                
                # Record success in circuit breaker
                self.circuit_breaker.record_success()
                
                # Reset connection failure count on success
                conn_id = id(conn)
                self.connection_failures[conn_id] = max(0, self.connection_failures.get(conn_id, 0) - 1)
                
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
                self.circuit_breaker.record_failure()
                time.sleep(backoff)
                
            except ConnectionError as e:
                # Handle connection errors with longer backoff
                backoff = min(initial_backoff * (2 ** retries) + random.uniform(0, 2.0), 60.0)
                logger.warning(f"Connection error during RPC call {method} (attempt {retries+1}/{max_retries}): {str(e)}")
                last_error = e
                retries += 1
                self.circuit_breaker.record_failure()
                time.sleep(backoff)
                # Always replace connection on ConnectionError
                conn = self._replace_connection(conn)
                
            except Exception as e:
                # Handle any other exception
                error_str = str(e)
                backoff = min(initial_backoff * (2 ** retries) + random.uniform(0, 1.0), 30.0)
                
                if "request-sent" in error_str.lower():
                    self.request_sent_errors += 1
                    logger.warning(f"Request-sent error during RPC call {method} (attempt {retries+1}/{max_retries}): {error_str}")
                    
                    # Track connection failure
                    conn_id = id(conn)
                    self.connection_failures[conn_id] = self.connection_failures.get(conn_id, 0) + 1
                    # If a connection has too many failures, remove it from the pool completely
                    if self.connection_failures.get(conn_id, 0) > 3:
                        try:
                            with self.conn_lock:
                                if conn in self.connections:
                                    logger.info(f"Removing connection with {self.connection_failures.get(conn_id)} failures from pool")
                                    self.connections.remove(conn)
                        except Exception as remove_error:
                            logger.warning(f"Error removing connection: {str(remove_error)}")
                    
                    last_error = e
                    retries += 1
                    self.circuit_breaker.record_failure()
                    
                    # Reduce the pool size temporarily to prevent overwhelming the node
                    # This is a dynamic throttling mechanism - if we see request-sent errors,
                    # we reduce the number of parallel connections
                    reduced_pool_size = max(1, int(self.pool_size * 0.7))  # Reduce by 30%
                    
                    # If we're still getting too many request-sent errors, reduce further
                    if self.request_sent_errors > 10 and self.request_sent_errors % 5 == 0:
                        reduced_pool_size = max(1, int(reduced_pool_size * 0.8))
                        logger.warning(f"High number of request-sent errors ({self.request_sent_errors}), reducing pool size further")
                        
                        # Also increase the minimum interval between requests
                        self.min_request_interval = min(2.0, self.min_request_interval * self.throttle_factor)
                        logger.info(f"Increasing request interval to {self.min_request_interval:.3f}s")
                    
                    try:
                        with self.conn_lock:
                            # Keep only the healthiest connections
                            if len(self.connections) > reduced_pool_size:
                                # Sort connections by failure count
                                conn_health = [(c, self.connection_failures.get(id(c), 0)) 
                                              for c in self.connections]
                                conn_health.sort(key=lambda x: x[1])  # Sort by failure count
                                
                                # Keep only the healthiest connections
                                self.connections = [c for c, _ in conn_health[:reduced_pool_size]]
                                logger.info(f"Reduced connection pool to {len(self.connections)} connections")
                    except Exception as resize_error:
                        logger.warning(f"Error resizing connection pool: {str(resize_error)}")
                    
                    # Use longer backoff for request-sent errors
                    sleep_time = backoff * 3  # Even longer backoff for request-sent errors
                    logger.info(f"Sleeping for {sleep_time:.2f}s before reconnection attempt")
                    time.sleep(sleep_time)  
                    
                    # Create a completely fresh connection
                    try:
                        logger.info("Creating new connection after request-sent error")
                        conn = self._connect()
                        # New connections start with no failure record
                        self.connection_failures[id(conn)] = 0
                        self.connection_uses[id(conn)] = 0
                        
                        # Add to pool for future use
                        with self.conn_lock:
                            if len(self.connections) < self.pool_size:
                                self.connections.append(conn)
                                logger.info("Added new connection to pool")
                    except Exception as conn_error:
                        logger.error(f"Failed to create new connection: {str(conn_error)}")
                        if self.connections:
                            conn = random.choice(self.connections)
                    try:
                        conn.getblockcount()
                    except Exception:
                        # We'll let the retry mechanism handle any failures
                        pass
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
