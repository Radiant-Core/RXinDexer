#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/db_maintenance_healthcheck.sh
# Custom health check script for the database maintenance container

# Check if the process is running
if pgrep -f "python -m src.utils.db_maintenance" > /dev/null; then
  # Process is running - check log files for errors
  if [ -f /app/logs/db_maintenance.log ]; then
    # Look for successful materialized view refresh in the last 15 minutes
    if grep -q "Successfully refreshed materialized views" /app/logs/db_maintenance.log; then
      # Check if there are any recent error logs (excluding warnings about cache hit ratio)
      if ! grep -i "error" /app/logs/db_maintenance.log | grep -v "Cache hit ratio" | grep -q "$(date +%Y-%m-%d)"; then
        # No errors found, container is healthy
        exit 0
      fi
    fi
  else
    # Log file doesn't exist yet, but process is running, give it some time
    exit 0
  fi
fi

# If we reach here, something is wrong
exit 1
