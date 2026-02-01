#!/bin/bash

# Setup script for RXinDexer with rocksdb GIL fixes
# This script ensures all dependencies are installed correctly

set -e

echo "Setting up RXinDexer environment..."

# Install system dependencies if on Linux
if command -v apt-get >/dev/null 2>&1; then
    echo "Installing system dependencies..."
    sudo apt-get update
    sudo apt-get install -y libleveldb-dev librocksdb-dev
fi

# Install Python dependencies
echo "Installing Python dependencies..."
python -m pip install --upgrade pip

# Install rocksdb with GIL workaround
echo "Installing python-rocksdb with GIL workaround..."
CFLAGS="-DCYTHON_WITHOUT_GIL=0" pip install python-rocksdb || pip install "python-rocksdb<=0.7.0"

# Install remaining requirements
echo "Installing remaining requirements..."
pip install -r requirements.txt

echo "✅ Setup complete!"
