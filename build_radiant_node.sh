#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/build_radiant_node.sh
# Script to build Radiant Node with memory limits to prevent OOM errors

echo "=== Building Radiant Node with resource limits ==="
echo "This script compiles Radiant Node on the host to avoid Docker build memory issues."

# Navigate to the Radiant Node directory
cd "$(dirname "$0")/Radiant-Node-master" || {
  echo "Error: Could not find Radiant-Node-master directory"
  exit 1
}

# Create build directory if it doesn't exist
mkdir -p build

# Configure with limited job count to reduce memory pressure
echo "Configuring build with memory-optimized settings..."
cmake -DBUILD_BITCOIN_WALLET=OFF -DBUILD_BITCOIN_ZMQ=OFF -DBUILD_BITCOIN_SEEDER=OFF -B build .

# Build with limited parallel jobs to reduce memory usage
echo "Building Radiant Node (this may take a while)..."
cd build
make -j2  # Use only 2 parallel jobs to limit memory consumption

echo "=== Build complete ==="
echo "Verify binary exists:"
ls -la ./bin/radiantd
ls -la ./bin/radiant-cli

echo "You can now run the RXinDexer stack with: ./run_optimized.sh start"
