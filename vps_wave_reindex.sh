#!/bin/bash
# VPS WAVE Reindexing Script
# Run this on your VPS to reindex WAVE names

set -e

# Genesis transaction (first WAVE name minted)
GENESIS_TXID="115e62d96f44402c448bf76d4ca403188733b902ab0b7703d9f36333178afda4"
GENESIS_BLOCK=425046
GENESIS_REF="${GENESIS_TXID}_0"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  VPS WAVE Reindexing${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Genesis TX: $GENESIS_TXID"
echo "Genesis Ref: $GENESIS_REF"
echo "Block: $GENESIS_BLOCK"
echo ""

# Detect how RXinDexer is running
echo -e "${BLUE}Step 1: Detecting RXinDexer installation...${NC}"

# Check for various possible locations
if [ -f "/etc/systemd/system/electrumx.service" ] || [ -f "/etc/systemd/system/rxindexer.service" ]; then
    SERVICE_MODE="systemd"
    echo -e "${GREEN}✓ Found systemd service${NC}"
elif [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
    SERVICE_MODE="docker"
    echo -e "${GREEN}✓ Found Docker Compose${NC}"
elif pgrep -f "electrumx_server" > /dev/null; then
    SERVICE_MODE="direct"
    echo -e "${GREEN}✓ Found running electrumx_server process${NC}"
else
    echo -e "${YELLOW}⚠ Could not detect RXinDexer installation${NC}"
    echo "Please ensure you're in the RXinDexer directory"
    exit 1
fi

# Update environment configuration
echo ""
echo -e "${BLUE}Step 2: Setting WAVE_GENESIS_REF...${NC}"

# Add to shell profile if not present
if ! grep -q "WAVE_GENESIS_REF=" ~/.bashrc 2>/dev/null; then
    echo "export WAVE_GENESIS_REF=$GENESIS_REF" >> ~/.bashrc
    echo -e "${GREEN}✓ Added to ~/.bashrc${NC}"
fi

# Export for current session
export WAVE_GENESIS_REF=$GENESIS_REF
echo -e "${GREEN}✓ Set for current session: $WAVE_GENESIS_REF${NC}"

# Check for .env file and update
for envfile in .env docker/full-stack/.env; do
    if [ -f "$envfile" ]; then
        if grep -q "WAVE_GENESIS_REF=" "$envfile"; then
            # Update existing
            sed -i "s|^WAVE_GENESIS_REF=.*|WAVE_GENESIS_REF=$GENESIS_REF|" "$envfile"
            echo -e "${GREEN}✓ Updated $envfile${NC}"
        else
            # Add new
            echo "WAVE_GENESIS_REF=$GENESIS_REF" >> "$envfile"
            echo -e "${GREEN}✓ Added to $envfile${NC}"
        fi
    fi
done

# Restart and reindex
echo ""
echo -e "${BLUE}Step 3: Restarting RXinDexer...${NC}"

case $SERVICE_MODE in
    systemd)
        SERVICE_NAME=$(systemctl list-unit-files | grep -E "electrumx|rxindexer" | head -1 | awk '{print $1}')
        if [ -n "$SERVICE_NAME" ]; then
            echo "Restarting $SERVICE_NAME..."
            
            # Update service environment
            if [ -f "/etc/default/electrumx" ]; then
                if grep -q "WAVE_GENESIS_REF=" /etc/default/electrumx; then
                    sudo sed -i "s|^WAVE_GENESIS_REF=.*|WAVE_GENESIS_REF=$GENESIS_REF|" /etc/default/electrumx
                else
                    echo "WAVE_GENESIS_REF=$GENESIS_REF" | sudo tee -a /etc/default/electrumx > /dev/null
                fi
                echo -e "${GREEN}✓ Updated /etc/default/electrumx${NC}"
            fi
            
            sudo systemctl daemon-reload
            sudo systemctl restart $SERVICE_NAME
            echo -e "${GREEN}✓ Service restarted${NC}"
            
            # Wait for startup
            echo ""
            echo "Waiting 10 seconds for startup..."
            sleep 10
        fi
        ;;
        
    docker)
        echo "Using Docker Compose..."
        if command -v docker-compose &> /dev/null; then
            COMPOSE_CMD="docker-compose"
        else
            COMPOSE_CMD="docker compose"
        fi
        
        $COMPOSE_CMD down
        echo -e "${GREEN}✓ Containers stopped${NC}"
        
        WAVE_GENESIS_REF=$GENESIS_REF $COMPOSE_CMD up -d
        echo -e "${GREEN}✓ Containers started${NC}"
        
        # Wait for startup
        echo ""
        echo "Waiting 15 seconds for startup..."
        sleep 15
        ;;
        
    direct)
        echo "Direct process mode - manual restart required"
        echo "Please restart your electrumx_server process with:"
        echo "  export WAVE_GENESIS_REF=$GENESIS_REF"
        echo "  # Then restart electrumx_server"
        read -p "Press Enter after you've restarted the process..."
        ;;
esac

# Check logs
echo ""
echo -e "${BLUE}Step 4: Checking logs for WAVE initialization...${NC}"

case $SERVICE_MODE in
    systemd)
        sudo journalctl -u $SERVICE_NAME --since "1 minute ago" | grep -i wave || echo -e "${YELLOW}No WAVE log entries yet (may need more time)${NC}"
        ;;
    docker)
        $COMPOSE_CMD logs --since 1m | grep -i wave || echo -e "${YELLOW}No WAVE log entries yet (may need more time)${NC}"
        ;;
    direct)
        echo "Check your electrumx logs manually for 'WAVE' entries"
        ;;
esac

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Reindexing Initiated${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}Important Notes:${NC}"
echo "  • WAVE indexing starts at block $GENESIS_BLOCK"
echo "  • Reindexing may take 30-60 minutes depending on VPS specs"
echo "  • Check progress with:"
echo ""

# Output monitoring commands
case $SERVICE_MODE in
    systemd)
        echo "    sudo journalctl -u $SERVICE_NAME -f | grep -i wave"
        echo "    sudo journalctl -u $SERVICE_NAME | grep -c 'Indexed WAVE'"
        ;;
    docker)
        echo "    $COMPOSE_CMD logs -f | grep -i wave"
        echo "    $COMPOSE_CMD logs | grep -c 'Indexed WAVE'"
        ;;
    direct)
        echo "    # Check your electrumx logs for 'WAVE' and 'Indexed'"
        ;;
esac

echo ""
echo "  • Test API: curl http://localhost:8000/wave/stats"
echo ""
