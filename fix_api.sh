#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/fix_api.sh
# This script fixes the API container issues by adding the missing Session import

# Stop the container
echo "Stopping the API container..."
docker stop rxindexer-api

# Make a backup of the current main.py
echo "Creating backup of main.py..."
docker cp rxindexer-api:/app/src/api/main.py /tmp/main.py.bak

# Copy our updated files to the container
echo "Copying enhanced security_patch.py to container..."
docker cp /Users/radiant/Desktop/RXinDexer/src/api/security_patch.py rxindexer-api:/app/src/api/

# Copy the common.py file with PaginationParams
echo "Copying common.py with PaginationParams to container..."
docker cp /Users/radiant/Desktop/RXinDexer/src/api/common.py rxindexer-api:/app/src/api/

# Copy the health.py file with health_router
echo "Copying health.py with health_router to container..."
docker cp /Users/radiant/Desktop/RXinDexer/src/api/health.py rxindexer-api:/app/src/api/

# Prepend the missing imports to main.py
echo "Creating fixed main.py with missing imports..."
echo '# Added missing imports
from sqlalchemy.orm import Session
from src.api.common import PaginationParams
from src.api.health import health_router' > /tmp/fixed_main.py
cat /tmp/main.py.bak >> /tmp/fixed_main.py

# Replace the container's main.py with our fixed version
echo "Replacing main.py in the container..."
docker cp /tmp/fixed_main.py rxindexer-api:/app/src/api/main.py

# Start the container
echo "Starting the API container..."
docker start rxindexer-api

# Wait for the container to start
echo "Waiting for container to start..."
sleep 5

# Check if the container is running
echo "Checking container status..."
docker ps | grep rxindexer-api

echo "Fix completed. Checking logs..."
docker logs rxindexer-api --tail 20
