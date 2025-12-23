#!/bin/bash
# Check Radiant node sync status
CURRENT_BLOCK=$(docker compose -f docker/docker-compose.yml exec radiant-node radiant-cli getblockcount 2>/dev/null | tr -d '\r')
echo "Current block: $CURRENT_BLOCK"
if [[ "$CURRENT_BLOCK" =~ ^[0-9]+$ ]] && [ "$CURRENT_BLOCK" -ge "$REQUIRED_BLOCK" ]; then
  echo "Radiant node is synced to required block height ($REQUIRED_BLOCK)."
else
  echo "Radiant node is NOT yet synced to required block height ($REQUIRED_BLOCK)."
fi
