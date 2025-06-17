import os

# Set environment variables
os.environ["RPC_MIN_REQUEST_INTERVAL"] = "2.0"
os.environ["RPC_THROTTLE_FACTOR"] = "1.8"
os.environ["CIRCUIT_RESET_TIMEOUT"] = "30"
os.environ["CIRCUIT_FAILURE_THRESHOLD"] = "10"
os.environ["HEALTH_CHECK_INTERVAL"] = "25"
os.environ["CONNECTION_RETRY_DELAY"] = "15"
os.environ["RADIANT_RPC_TIMEOUT"] = "90"
os.environ["SYNC_MAX_WORKERS"] = "2"
os.environ["RPC_MAX_RETRIES"] = "20"
os.environ["ENABLE_PARALLEL_PROCESSING"] = "false"
