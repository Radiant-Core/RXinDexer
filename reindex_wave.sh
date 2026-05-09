#!/bin/bash
# WAVE Name Reindexing Helper Script
# This script helps reindex WAVE names from the genesis block

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Genesis transaction (first WAVE name)
GENESIS_TXID="115e62d96f44402c448bf76d4ca403188733b902ab0b7703d9f36333178afda4"
GENESIS_BLOCK=425046
GENESIS_REF="${GENESIS_TXID}_0"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  WAVE Name Reindexing Helper${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if running in Docker or native
if [ -f "docker-compose.yaml" ] || [ -f "docker-compose.yml" ]; then
    echo -e "${YELLOW}Docker environment detected${NC}"
    DOCKER_MODE=1
else
    echo -e "${YELLOW}Native environment detected${NC}"
    DOCKER_MODE=0
fi

# Verify genesis ref is set
echo "Step 1: Verifying WAVE_GENESIS_REF configuration..."
if [ -z "$WAVE_GENESIS_REF" ]; then
    echo -e "${YELLOW}WARNING: WAVE_GENESIS_REF not set in environment${NC}"
    echo "Setting for this session:"
    export WAVE_GENESIS_REF=$GENESIS_REF
    echo "export WAVE_GENESIS_REF=$GENESIS_REF"
else
    if [ "$WAVE_GENESIS_REF" = "$GENESIS_REF" ]; then
        echo -e "${GREEN}✓ WAVE_GENESIS_REF is correctly set${NC}"
    else
        echo -e "${YELLOW}WARNING: WAVE_GENESIS_REF differs from expected${NC}"
        echo "  Current: $WAVE_GENESIS_REF"
        echo "  Expected: $GENESIS_REF"
        echo "Updating for this session..."
        export WAVE_GENESIS_REF=$GENESIS_REF
    fi
fi
echo ""

# Option selection
echo "Step 2: Choose reindexing method:"
echo ""
echo "  1) Trigger reorg via RPC (Fastest - if caught up)"
echo "  2) Restart with reindex (Recommended)"
echo "  3) Full database reset (⚠️  Destructive - all data lost)"
echo ""
read -p "Enter choice (1-3): " choice

case $choice in
    1)
        echo ""
        echo "Step 3: Triggering reorg via RPC..."
        echo "  This will reprocess the last 1000+ blocks"
        
        # Check if electrumx_rpc is available
        if command -v electrumx_rpc &> /dev/null; then
            echo -e "${YELLOW}Running: electrumx_rpc reorg 1000${NC}"
            electrumx_rpc reorg 1000 || echo -e "${RED}RPC call failed. Is RXinDexer running?${NC}"
        else
            echo -e "${YELLOW}electrumx_rpc not found, trying Python directly...${NC}"
            python3 -c "
import asyncio
import sys
sys.path.insert(0, '.')
from electrumx_rpc import main
import argparse

# Mock args
sys.argv = ['electrumx_rpc', 'reorg', '1000']
asyncio.run(main())
" || echo -e "${RED}RPC call failed. Is RXinDexer running?${NC}"
        fi
        ;;
        
    2)
        echo ""
        echo "Step 3: Restarting RXinDexer with WAVE genesis ref..."
        
        if [ $DOCKER_MODE -eq 1 ]; then
            echo -e "${YELLOW}Stopping containers...${NC}"
            docker-compose down
            
            echo -e "${YELLOW}Starting with WAVE_GENESIS_REF...${NC}"
            export WAVE_GENESIS_REF=$GENESIS_REF
            docker-compose up -d
            
            echo ""
            echo "Watching logs (Ctrl+C to stop watching)..."
            sleep 2
            docker-compose logs -f | grep -i wave || true
        else
            echo -e "${YELLOW}Please manually restart your ElectrumX server with:${NC}"
            echo "  export WAVE_GENESIS_REF=$GENESIS_REF"
            echo "  # Then restart your electrumx process"
        fi
        ;;
        
    3)
        echo ""
        echo -e "${RED}WARNING: This will DELETE ALL INDEXED DATA${NC}"
        echo "The database will be cleared and indexing will start from scratch."
        echo ""
        read -p "Are you sure? Type 'DELETE' to confirm: " confirm
        
        if [ "$confirm" = "DELETE" ]; then
            echo ""
            echo "Step 3: Clearing database and restarting..."
            
            if [ $DOCKER_MODE -eq 1 ]; then
                echo -e "${YELLOW}Stopping containers...${NC}"
                docker-compose down
                
                # Find and clear database
                DB_DIR=${DB_DIRECTORY:-/data/electrumdb}
                echo -e "${YELLOW}Clearing database at $DB_DIR...${NC}"
                echo "  rm -rf $DB_DIR/*"
                
                echo -e "${YELLOW}Starting fresh...${NC}"
                export WAVE_GENESIS_REF=$GENESIS_REF
                docker-compose up -d
                
                echo ""
                echo "Watching logs (Ctrl+C to stop watching)..."
                sleep 2
                docker-compose logs -f | grep -i wave || true
            else
                echo -e "${YELLOW}Please manually clear your database and restart:${NC}"
                echo "  export WAVE_GENESIS_REF=$GENESIS_REF"
                echo "  rm -rf \$DB_DIRECTORY/*"
                echo "  # Then restart your electrumx process"
            fi
        else
            echo "Cancelled."
            exit 1
        fi
        ;;
        
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Reindexing Initiated${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Next steps:"
echo "  1. Watch logs for 'WAVE genesis ref' message"
echo "  2. Look for 'Indexed WAVE name' entries starting at block $GENESIS_BLOCK"
echo "  3. Test: curl http://localhost:8000/wave/resolve/testname.rxd"
echo "  4. Verify Photonic Wallet can resolve names after sync"
echo ""
echo "Genesis Reference: $GENESIS_REF"
echo "Block Height: $GENESIS_BLOCK"
echo ""
