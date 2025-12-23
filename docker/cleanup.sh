#!/bin/sh
# RXinDexer Docker Cleanup Script
# Removes dangling images, build cache, and orphaned volumes
# Safe to run while containers are running

set -e

echo "=== RXinDexer Docker Cleanup ==="
echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting cleanup..."

# Show current usage
echo ""
echo "Before cleanup:"
docker system df

# Remove dangling images (untagged)
echo ""
echo "Removing dangling images..."
docker image prune -f 2>/dev/null || true

# Remove build cache older than 24 hours
echo ""
echo "Removing old build cache..."
docker builder prune -f --filter "until=24h" 2>/dev/null || true

# Remove orphaned volumes (not attached to any container)
echo ""
echo "Checking for orphaned volumes..."
ORPHANED=$(docker volume ls -qf dangling=true 2>/dev/null | grep -v "docker_pgdata\|docker_radiant-data" || true)
if [ -n "$ORPHANED" ]; then
    echo "Removing orphaned volumes: $ORPHANED"
    echo "$ORPHANED" | xargs docker volume rm 2>/dev/null || true
else
    echo "No orphaned volumes found."
fi

# Show final usage
echo ""
echo "After cleanup:"
docker system df

echo ""
echo "$(date '+%Y-%m-%d %H:%M:%S') - Cleanup complete!"
