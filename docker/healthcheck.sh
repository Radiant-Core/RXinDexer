#!/bin/sh
# /Users/radiant/Desktop/RXinDexer/docker/healthcheck.sh
# Health check script for Radiant Node

set -e

# Try to get blockchain info from the node
radiant-cli -rpcuser=rxin -rpcpassword=securepassword getblockchaininfo > /dev/null

# Exit with the status of the previous command
exit $?
