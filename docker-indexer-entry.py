#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/docker-indexer-entry.py
# This file serves as a direct entry point for the RXinDexer Indexer in Docker.
# It ensures that the indexer process starts correctly regardless of environment.

import os
import sys
import subprocess
import time
from pathlib import Path

# Print banner for easy identification in logs
print("=" * 50)
print("RXinDexer Indexer Starting")
print("=" * 50)

# Set up Python path properly
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
    print(f"Added {project_root} to Python path")

# Ensure environment variables are set
os.environ["IN_DOCKER"] = "true"
os.environ["IN_INDEXER"] = "true"

# Define the indexer module path
indexer_module = "src.indexer"
sync_script = "sync/rxindex_sync.py"

# Try to run the indexer
try:
    print(f"Starting indexer process using {sync_script}")
    # Use the sync script directly since that's what was specified in docker-compose
    result = subprocess.run([sys.executable, sync_script], 
                          check=True)
    sys.exit(result.returncode)
except Exception as e:
    print(f"Error starting indexer: {e}")
    print("Attempting fallback to module import...")
    
    try:
        print(f"Trying to import and run {indexer_module}")
        # Try the module import approach as fallback
        module_name = indexer_module.replace("/", ".")
        subprocess.run([sys.executable, "-m", module_name], 
                      check=True)
    except Exception as fallback_error:
        print(f"Fatal error starting indexer via fallback: {fallback_error}")
        sys.exit(1)
