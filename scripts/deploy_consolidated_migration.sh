#!/bin/bash
# Deployment script for consolidated Alembic migration
# This script prepares the migration environment and handles the transition

set -e  # Exit on any error

echo "===== RXinDexer Migration Consolidation Deployment ====="
echo "This script prepares the environment for the consolidated migration"
echo "and handles the transition from multiple migrations to a single one."

# Step 1: Create backups of original migrations
BACKUP_DIR="/Users/main/Desktop/RXinDexer_1/alembic/versions/backup_$(date +%Y%m%d_%H%M%S)"
echo "Creating backup of original migrations in $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
cp /Users/main/Desktop/RXinDexer_1/alembic/versions/*.py "$BACKUP_DIR/"

# Step 2: Keep only the consolidated migration
echo "Setting up consolidated migration..."
cd /Users/main/Desktop/RXinDexer_1/alembic/versions/
# Keep our new consolidated migration and remove old ones
CONSOLIDATED_FILE="combined_migration_prod_v1.py"

# Move all other migrations to backup (safer than deleting)
for file in *.py; do
  if [ "$file" != "$CONSOLIDATED_FILE" ]; then
    echo "Moving $file to backup"
    mv "$file" "$BACKUP_DIR/"
  fi
done

echo "Consolidated migration setup complete!"
echo "The following migration is now active:"
ls -l /Users/main/Desktop/RXinDexer_1/alembic/versions/

# Step 3: Clean and rebuild instructions
echo ""
echo "===== Next Steps ====="
echo "To test the consolidated migration:"
echo "1. Run 'docker-compose down -v' to stop and remove containers, networks, and volumes"
echo "2. Run 'docker-compose build' to rebuild the containers"
echo "3. Run 'docker-compose up -d' to start containers with the new migration"
echo ""
echo "Note: To restore previous migrations if needed, copy files back from $BACKUP_DIR"
