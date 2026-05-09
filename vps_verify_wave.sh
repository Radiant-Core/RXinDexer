#!/bin/bash
# VPS WAVE Verification Script
# Run this after reindexing to verify WAVE names are working

set -e

GENESIS_REF="115e62d96f44402c448bf76d4ca403188733b902ab0b7703d9f36333178afda4_0"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  WAVE Verification Checklist${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check 1: Environment variable
echo -e "${BLUE}Check 1: Environment Configuration${NC}"
if [ "$WAVE_GENESIS_REF" = "$GENESIS_REF" ]; then
    echo -e "${GREEN}✓ WAVE_GENESIS_REF is set correctly${NC}"
else
    echo -e "${YELLOW}⚠ WAVE_GENESIS_REF not set in this session${NC}"
    echo "  Expected: $GENESIS_REF"
    echo "  Current: ${WAVE_GENESIS_REF:-'(not set)'}"
fi
echo ""

# Check 2: Service status
echo -e "${BLUE}Check 2: Service Status${NC}"
if systemctl is-active --quiet electrumx 2>/dev/null || systemctl is-active --quiet rxindexer 2>/dev/null; then
    SERVICE=$(systemctl list-units --type=service --state=running | grep -E "electrumx|rxindexer" | awk '{print $1}' | head -1)
    echo -e "${GREEN}✓ Service $SERVICE is running${NC}"
elif docker ps 2>/dev/null | grep -q electrumx; then
    echo -e "${GREEN}✓ Docker container is running${NC}"
else
    echo -e "${RED}✗ No RXinDexer service detected${NC}"
fi
echo ""

# Check 3: Logs for genesis ref
echo -e "${BLUE}Check 3: Genesis Ref in Logs${NC}"
GENESIS_FOUND=0

# Try systemd
if command -v journalctl &> /dev/null; then
    for service in electrumx rxindexer; do
        if systemctl list-units --type=service | grep -q "$service"; then
            if sudo journalctl -u $service --since "10 minutes ago" 2>/dev/null | grep -q "WAVE genesis ref"; then
                echo -e "${GREEN}✓ Genesis ref found in $service logs${NC}"
                sudo journalctl -u $service --since "10 minutes ago" | grep "WAVE genesis ref" | tail -1
                GENESIS_FOUND=1
                break
            fi
        fi
    done
fi

# Try docker
if [ $GENESIS_FOUND -eq 0 ] && command -v docker &> /dev/null; then
    if docker ps | grep -q electrumx; then
        CONTAINER=$(docker ps | grep electrumx | awk '{print $1}' | head -1)
        if docker logs $CONTAINER --since 10m 2>&1 | grep -q "WAVE genesis ref"; then
            echo -e "${GREEN}✓ Genesis ref found in Docker logs${NC}"
            docker logs $CONTAINER --since 10m 2>&1 | grep "WAVE genesis ref" | tail -1
            GENESIS_FOUND=1
        fi
    fi
fi

if [ $GENESIS_FOUND -eq 0 ]; then
    echo -e "${YELLOW}⚠ Genesis ref not found in recent logs${NC}"
    echo "  This may be normal if service was restarted longer than 10 minutes ago"
fi
echo ""

# Check 4: API endpoint
echo -e "${BLUE}Check 4: REST API Endpoint${NC}"
API_URL="http://localhost:8000"

if curl -s "$API_URL/wave/stats" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ WAVE API is responding${NC}"
    
    # Get stats
    STATS=$(curl -s "$API_URL/wave/stats")
    echo "  Stats: $STATS"
    
    # Check if genesis is configured
    if echo "$STATS" | grep -q "true"; then
        echo -e "${GREEN}✓ Genesis is configured in API${NC}"
    fi
else
    echo -e "${RED}✗ WAVE API not responding${NC}"
    echo "  Check if REST API is enabled and port 8000 is accessible"
fi
echo ""

# Check 5: Test name resolution
echo -e "${BLUE}Check 5: Test Name Resolution${NC}"

# Try to resolve a few test names
TEST_NAMES=("alice.rxd" "test.rxd" "wave.rxd")
RESOLVED_COUNT=0

for name in "${TEST_NAMES[@]}"; do
    echo "  Testing: $name"
    RESPONSE=$(curl -s "$API_URL/wave/resolve/$name" 2>/dev/null || echo '{"error": "connection failed"}')
    
    if echo "$RESPONSE" | grep -q "resolved.*true"; then
        echo -e "    ${GREEN}✓ Resolved:${NC} $RESPONSE"
        ((RESOLVED_COUNT++))
    elif echo "$RESPONSE" | grep -q "available.*true"; then
        echo -e "    ${YELLOW}⚠ Available (not registered):${NC} $name"
    else
        echo -e "    ${YELLOW}⚠ No response or error${NC}"
    fi
done

if [ $RESOLVED_COUNT -gt 0 ]; then
    echo -e "${GREEN}✓ $RESOLVED_COUNT WAVE name(s) resolved successfully${NC}"
else
    echo -e "${YELLOW}⚠ No WAVE names resolved yet${NC}"
    echo "  This is normal if reindexing is still in progress"
fi
echo ""

# Check 6: Count indexed names
echo -e "${BLUE}Check 6: Indexed WAVE Names Count${NC}"
INDEXED_COUNT=0

# Try systemd logs
if command -v journalctl &> /dev/null; then
    for service in electrumx rxindexer; do
        if systemctl list-units --type=service | grep -q "$service"; then
            COUNT=$(sudo journalctl -u $service 2>/dev/null | grep -c "Indexed WAVE name" || echo "0")
            if [ "$COUNT" -gt 0 ]; then
                INDEXED_COUNT=$COUNT
                echo -e "${GREEN}✓ Found $COUNT 'Indexed WAVE name' entries in $service logs${NC}"
                break
            fi
        fi
    done
fi

# Try docker logs
if [ $INDEXED_COUNT -eq 0 ] && command -v docker &> /dev/null; then
    if docker ps | grep -q electrumx; then
        CONTAINER=$(docker ps | grep electrumx | awk '{print $1}' | head -1)
        COUNT=$(docker logs $CONTAINER 2>&1 | grep -c "Indexed WAVE name" || echo "0")
        if [ "$COUNT" -gt 0 ]; then
            INDEXED_COUNT=$COUNT
            echo -e "${GREEN}✓ Found $COUNT 'Indexed WAVE name' entries in Docker logs${NC}"
        fi
    fi
fi

if [ $INDEXED_COUNT -eq 0 ]; then
    echo -e "${YELLOW}⚠ No 'Indexed WAVE name' entries found yet${NC}"
    echo "  Reindexing may still be in progress"
fi
echo ""

# Check 7: ElectrumX RPC
echo -e "${BLUE}Check 7: ElectrumX RPC (wave.resolve)${NC}"

# Try electrumx_rpc
if command -v electrumx_rpc &> /dev/null; then
    echo "  Testing: electrumx_rpc wave.stats"
    RPC_RESULT=$(electrumx_rpc wave.stats 2>/dev/null || echo '{"error": "rpc failed"}')
    
    if echo "$RPC_RESULT" | grep -q "enabled"; then
        echo -e "${GREEN}✓ RPC wave.stats responded${NC}"
        echo "  Result: $RPC_RESULT"
    else
        echo -e "${YELLOW}⚠ RPC not responding or wave not enabled${NC}"
    fi
else
    echo -e "${YELLOW}⚠ electrumx_rpc not in PATH${NC}"
fi
echo ""

# Summary
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Verification Summary${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

if [ $INDEXED_COUNT -gt 0 ] || [ $RESOLVED_COUNT -gt 0 ]; then
    echo -e "${GREEN}✓ WAVE indexing appears to be working!${NC}"
    echo ""
    echo "Quick test commands:"
    echo "  curl http://localhost:8000/wave/resolve/alice.rxd"
    echo "  curl http://localhost:8000/wave/stats"
    echo "  electrumx_rpc wave.stats"
    echo ""
    echo -e "${GREEN}Photonic Wallet should now be able to resolve WAVE names!${NC}"
else
    echo -e "${YELLOW}⚠ WAVE indexing may still be in progress${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Wait for reindexing to complete (check logs)"
    echo "  2. Re-run this script in 10-15 minutes"
    echo "  3. Verify Photonic Wallet can resolve names after sync"
    echo ""
    echo "Monitor progress:"
    if command -v journalctl &> /dev/null; then
        for service in electrumx rxindexer; do
            if systemctl list-units --type=service | grep -q "$service"; then
                echo "  sudo journalctl -u $service -f | grep -i wave"
                break
            fi
        done
    fi
    if command -v docker &> /dev/null && docker ps | grep -q electrumx; then
        echo "  docker logs -f $(docker ps | grep electrumx | awk '{print $1}' | head -1) | grep -i wave"
    fi
fi

echo ""
echo "Genesis Ref: $GENESIS_REF"
echo ""
