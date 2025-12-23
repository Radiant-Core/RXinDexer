import os
import requests
from fastapi import HTTPException

RADIANT_NODE_HOST = os.getenv("RADIANT_NODE_HOST", "radiant-node")
RADIANT_NODE_RPCPORT = int(os.getenv("RADIANT_NODE_RPCPORT", 7332))
RADIANT_NODE_RPCUSER = os.getenv("RADIANT_NODE_RPCUSER", "dockeruser")
RADIANT_NODE_RPCPASSWORD = os.getenv("RADIANT_NODE_RPCPASSWORD", "dockerpass")

API_RPC_CONNECT_TIMEOUT = float(os.getenv("API_RPC_CONNECT_TIMEOUT", "3"))
API_RPC_READ_TIMEOUT = float(os.getenv("API_RPC_READ_TIMEOUT", "10"))

def rpc_call(method, params=None, timeout=None):
    url = f"http://{RADIANT_NODE_HOST}:{RADIANT_NODE_RPCPORT}"
    headers = {"content-type": "application/json"}
    payload = {
        "method": method,
        "params": params or [],
        "id": 1,
        "jsonrpc": "2.0"
    }
    try:
        read_timeout = float(timeout) if timeout is not None else float(API_RPC_READ_TIMEOUT)
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            auth=(RADIANT_NODE_RPCUSER, RADIANT_NODE_RPCPASSWORD),
            timeout=(float(API_RPC_CONNECT_TIMEOUT), read_timeout),
        )
        response.raise_for_status()
        result = response.json()
        if 'error' in result and result['error']:
            raise HTTPException(status_code=500, detail=result['error'])
        return result['result']
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
