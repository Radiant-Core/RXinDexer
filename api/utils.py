import os
import time
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from fastapi import HTTPException
from typing import Optional, Any, Dict

RADIANT_NODE_HOST = os.getenv("RADIANT_NODE_HOST", "radiant-node")
RADIANT_NODE_RPCPORT = int(os.getenv("RADIANT_NODE_RPCPORT", 7332))
RADIANT_NODE_RPCUSER = os.getenv("RADIANT_NODE_RPCUSER", "dockeruser")
RADIANT_NODE_RPCPASSWORD = os.getenv("RADIANT_NODE_RPCPASSWORD", "dockerpass")

API_RPC_CONNECT_TIMEOUT = float(os.getenv("API_RPC_CONNECT_TIMEOUT", "3"))
API_RPC_READ_TIMEOUT = float(os.getenv("API_RPC_READ_TIMEOUT", "10"))
API_RPC_MAX_RETRIES = int(os.getenv("API_RPC_MAX_RETRIES", "3"))
API_RPC_RETRY_BACKOFF = float(os.getenv("API_RPC_RETRY_BACKOFF", "0.5"))

logger = logging.getLogger("rxindexer.api.rpc")

# Create a session with connection pooling for better performance
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """Get or create a shared requests session with connection pooling."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.auth = (RADIANT_NODE_RPCUSER, RADIANT_NODE_RPCPASSWORD)
        _session.headers.update({"content-type": "application/json"})
        
        # Configure connection pooling
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=0  # We handle retries manually for better control
        )
        _session.mount("http://", adapter)
        _session.mount("https://", adapter)
    
    return _session


def rpc_call(method: str, params: Optional[list] = None, timeout: Optional[float] = None) -> Any:
    """
    Make RPC call to radiant-node with retry logic and improved error handling.
    
    Args:
        method: RPC method name
        params: Optional list of parameters
        timeout: Optional read timeout override
    
    Returns:
        RPC result
    
    Raises:
        HTTPException: On RPC errors or connection failures
    """
    url = f"http://{RADIANT_NODE_HOST}:{RADIANT_NODE_RPCPORT}"
    payload = {
        "method": method,
        "params": params or [],
        "id": 1,
        "jsonrpc": "2.0"
    }
    
    read_timeout = float(timeout) if timeout is not None else float(API_RPC_READ_TIMEOUT)
    effective_timeout = (float(API_RPC_CONNECT_TIMEOUT), read_timeout)
    
    last_error = None
    session = _get_session()
    
    for attempt in range(API_RPC_MAX_RETRIES):
        try:
            response = session.post(url, json=payload, timeout=effective_timeout)
            response.raise_for_status()
            result = response.json()
            
            if 'error' in result and result['error']:
                error_msg = result['error']
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get('message', str(error_msg))
                raise HTTPException(status_code=500, detail=f"RPC error: {error_msg}")
            
            return result['result']
            
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            wait_time = API_RPC_RETRY_BACKOFF * (2 ** attempt)
            
            if attempt < API_RPC_MAX_RETRIES - 1:
                logger.warning(
                    f"RPC call {method} failed (attempt {attempt + 1}/{API_RPC_MAX_RETRIES}): {e}. "
                    f"Retrying in {wait_time:.1f}s..."
                )
                time.sleep(wait_time)
            continue
            
        except HTTPException:
            raise
            
        except Exception as e:
            logger.error(f"RPC call {method} failed with unexpected error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    # All retries exhausted
    error_msg = f"RPC call {method} failed after {API_RPC_MAX_RETRIES} attempts: {last_error}"
    logger.error(error_msg)
    raise HTTPException(status_code=503, detail=error_msg)


def check_node_connection() -> Dict[str, Any]:
    """
    Check if radiant-node is reachable and responding.
    
    Returns:
        Dict with connection status and details
    """
    try:
        start_time = time.time()
        block_count = rpc_call("getblockcount", timeout=5)
        latency_ms = (time.time() - start_time) * 1000
        
        return {
            "connected": True,
            "block_height": block_count,
            "latency_ms": round(latency_ms, 2),
            "host": RADIANT_NODE_HOST,
            "port": RADIANT_NODE_RPCPORT
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
            "host": RADIANT_NODE_HOST,
            "port": RADIANT_NODE_RPCPORT
        }
