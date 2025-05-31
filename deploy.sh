#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/deploy.sh
# This is a wrapper script for the deployment process of RXinDexer.
# It provides a simple CLI for deploying the application to different environments.

set -e

# Set working directory to the project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required but not installed."
    exit 1
fi

# Display help if no arguments are provided
if [ $# -eq 0 ]; then
    echo "RXinDexer Deployment Script"
    echo "============================"
    echo "Usage: ./deploy.sh [environment] [options]"
    echo ""
    echo "Environments:"
    echo "  development   - Deploy to development environment"
    echo "  staging       - Deploy to staging environment"
    echo "  production    - Deploy to production environment"
    echo ""
    echo "Options:"
    echo "  --build       - Force rebuild of Docker images"
    echo "  --no-cache    - Build without using cache"
    echo "  --config-only - Generate configuration files only without deploying"
    echo "  --stop        - Stop the deployment instead of starting it"
    echo "  --backup-db   - Backup database before deployment"
    echo ""
    echo "Examples:"
    echo "  ./deploy.sh development              - Deploy to development environment"
    echo "  ./deploy.sh production --build       - Deploy to production with fresh build"
    echo "  ./deploy.sh staging --stop           - Stop the staging deployment"
    echo "  ./deploy.sh production --backup-db   - Deploy to production with DB backup"
    echo ""
    exit 0
fi

# Execute the deployment Python script
echo "Starting RXinDexer deployment process..."
python3 deploy/deploy.py "$@"

# Check the result of the deployment
if [ $? -eq 0 ]; then
    echo "Deployment completed successfully!"
else
    echo "Deployment failed. Please check the logs for details."
    exit 1
fi
