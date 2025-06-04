#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/docker/healthchecks/db_maintenance.sh
# Health check script for the database maintenance container

# Check if the main maintenance process is running
if pgrep -f "python -m src.utils.db_maintenance" > /dev/null; then
  # Process is running - that's good enough for a basic health check
  exit 0
else
  # Process not running
  exit 1
fi
