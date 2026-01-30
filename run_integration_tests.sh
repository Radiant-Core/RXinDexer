#!/bin/bash
# RXinDexer Integration Test Runner
# Usage: ./run_integration_tests.sh [--build] [--cleanup]
#
# Prerequisites:
#   - Docker and docker-compose installed
#   - Radiant node running on host at port 7332
#
# Options:
#   --build    Force rebuild of Docker images
#   --cleanup  Remove test containers and volumes after tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse arguments
BUILD_FLAG=""
CLEANUP=false

for arg in "$@"; do
    case $arg in
        --build)
            BUILD_FLAG="--build"
            ;;
        --cleanup)
            CLEANUP=true
            ;;
    esac
done

echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  RXinDexer Integration Test Suite${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed${NC}"
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "${RED}Error: docker-compose is not installed${NC}"
    exit 1
fi

# Check if Radiant node is running
if ! nc -z localhost 7332 2>/dev/null; then
    echo -e "${YELLOW}Warning: Radiant node not detected on port 7332${NC}"
    echo -e "${YELLOW}Tests may fail without a running node${NC}"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Create test results directory
mkdir -p test_results

# Get RPC credentials from radiant.conf if available
RADIANT_CONF="$HOME/Library/Application Support/Radiant/radiant.conf"
if [[ -f "$RADIANT_CONF" ]]; then
    RPC_USER=$(grep -E "^rpcuser=" "$RADIANT_CONF" | cut -d'=' -f2)
    RPC_PASS=$(grep -E "^rpcpassword=" "$RADIANT_CONF" | cut -d'=' -f2)
    if [[ -n "$RPC_USER" && -n "$RPC_PASS" ]]; then
        export DAEMON_URL="http://${RPC_USER}:${RPC_PASS}@host.docker.internal:7332/"
        echo -e "${GREEN}Found Radiant RPC credentials${NC}"
    fi
fi

# Default DAEMON_URL if not set
export DAEMON_URL="${DAEMON_URL:-http://radiantrpc:testpass@host.docker.internal:7332/}"

echo -e "${YELLOW}Starting test environment...${NC}"
echo ""

# Use docker compose (v2) or docker-compose (v1)
if docker compose version &> /dev/null; then
    COMPOSE_CMD="docker compose"
else
    COMPOSE_CMD="docker-compose"
fi

# Start the test environment
$COMPOSE_CMD -f docker-compose.test.yaml up $BUILD_FLAG --abort-on-container-exit --exit-code-from test_runner

EXIT_CODE=$?

# Show test results
if [[ -f "test_results/integration_results.json" ]]; then
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  Test Results${NC}"
    echo -e "${GREEN}============================================${NC}"
    cat test_results/integration_results.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
s = data['summary']
print(f\"Total:  {s['total']}\")
print(f\"Passed: {s['passed']}\")
print(f\"Failed: {s['failed']}\")
" 2>/dev/null || cat test_results/integration_results.json
fi

# Cleanup if requested
if [[ "$CLEANUP" == true ]]; then
    echo ""
    echo -e "${YELLOW}Cleaning up test environment...${NC}"
    $COMPOSE_CMD -f docker-compose.test.yaml down -v
fi

echo ""
if [[ $EXIT_CODE -eq 0 ]]; then
    echo -e "${GREEN}✅ All tests passed!${NC}"
else
    echo -e "${RED}❌ Some tests failed${NC}"
fi

exit $EXIT_CODE
