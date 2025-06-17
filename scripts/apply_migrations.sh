#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/scripts/apply_migrations.sh
# This script applies database migrations to ensure the database schema matches the SQLAlchemy models.

set -e

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR/.."

# Activate Python virtual environment if it exists
if [ -d "venv" ]; then
    echo "Activating Python virtual environment..."
    source venv/bin/activate
fi

# Install required Python packages if not already installed
echo "Checking for required Python packages..."
pip install --user -q sqlalchemy alembic psycopg2-binary python-dotenv

# Create the migrations directory if it doesn't exist
mkdir -p migrations/versions

# Run the migrations
echo "Running database migrations..."
python -m scripts.run_migrations

# Check if the migration was successful
if [ $? -eq 0 ]; then
    echo "✅ Database migrations applied successfully!"
else
    echo "❌ Failed to apply database migrations."
    exit 1
fi
