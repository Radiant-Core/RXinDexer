
# RPC client patch to improve stability - fixed version
import time
import os

# Reduce connection pool size
os.environ['RPC_POOL_SIZE'] = '1'
os.environ['RPC_MIN_REQUEST_INTERVAL'] = '3.0'
os.environ['RPC_THROTTLE_FACTOR'] = '2.0'
os.environ['CIRCUIT_RESET_TIMEOUT'] = '60'
os.environ['RADIANT_RPC_TIMEOUT'] = '120'
os.environ['RPC_MAX_RETRIES'] = '30'

