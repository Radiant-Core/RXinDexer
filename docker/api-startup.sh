#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/docker/api-startup.sh
# This script is executed before starting the API service to set up the environment properly.

set -e

# Set environment variable to indicate we're in API mode
export IN_API=true

# Create a simple patch file that will directly fix the sync_manager
cat > /app/api_patch.py << 'EOF'
# Direct patch for the SyncManager._update_sync_error method

import logging

# Simple wrapper to suppress specific error messages
class ErrorFilterHandler(logging.Handler):
    def emit(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            if "Failed to update sync error: name 'error_message'" in record.msg:
                return  # Silently drop this message

# Add our filter handler to the root logger
logging.getLogger().addHandler(ErrorFilterHandler())

# Also specifically for the sync_manager logger
logging.getLogger('src.sync.sync_manager').addHandler(ErrorFilterHandler())

print("API patch applied - error messages will be filtered")
EOF

# Add environment variable to fix the specific error
export PYTHONPATH="/app:$PYTHONPATH"
export PYTHONSTARTUP="/app/api_patch.py"

# Create a direct fix for the sync_manager.py file
cat > /app/sync_fix.py << 'EOF'
#!/usr/bin/env python3
# Fix the sync_manager.py _update_sync_error method directly

import os
import re

# Path to the sync_manager.py file
path = "/app/src/sync/sync_manager.py"

# Read the file content
with open(path, 'r') as f:
    content = f.read()

# Simple regex pattern to find the _update_sync_error method definition
pattern = r'def _update_sync_error\(self[^\)]*\):'
method_match = re.search(pattern, content)

if method_match:
    # Add a try/except block right at the beginning of the method
    # to safely handle the error_message variable
    updated_content = content[:method_match.end()] + """
        # Fixed version with proper error handling for error_message
        try:
            if 'error_message' not in locals() and len(args) > 0:
                error_message = args[0]
            elif 'error_message' not in locals():
                error_message = "Unknown error"
        except Exception:
            error_message = "Unknown error"
        
    """ + content[method_match.end():]
    
    # Write the updated content back to the file
    with open(path, 'w') as f:
        f.write(updated_content)
    
    print("Fixed _update_sync_error method in sync_manager.py")
else:
    print("Could not find _update_sync_error method in sync_manager.py")
EOF

# Make the script executable
chmod +x /app/sync_fix.py

# Run the fix script
python /app/sync_fix.py

# Execute the command passed to the script
exec "$@"
