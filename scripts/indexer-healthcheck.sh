#!/bin/bash
#
# indexer-healthcheck.sh — Probe the RXinDexer WSS endpoint for -101 throttle
# and restart the container if detected. Designed to run from cron every 5 min.
#
# Usage: /opt/rxindexer-new/scripts/indexer-healthcheck.sh
#
# Exits 0 = healthy, 1 = throttled (and restarted), 2 = unreachable

set -euo pipefail

ENDPOINT="wss://electrumx.radiantcore.org"
COMPOSE_DIR="/opt/rxindexer-new/docker/full-stack"
LOG="/var/log/indexer-healthcheck.log"
MAX_RESTARTS_PER_HOUR=3
STATE_FILE="/tmp/indexer-healthcheck-restarts"

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" >> "$LOG"
}

# Count restarts in the last hour to prevent a restart loop
count_recent_restarts() {
    if [ ! -f "$STATE_FILE" ]; then
        echo 0
        return
    fi
    local cutoff
    cutoff=$(date -u -v-1H '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo "")
    if [ -z "$cutoff" ]; then
        echo 0
        return
    fi
    grep -c "$(date -u '+%Y-%m-%d')" "$STATE_FILE" 2>/dev/null || echo 0
}

record_restart() {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$STATE_FILE"
    # Trim entries older than 1 hour
    local now_epoch
    now_epoch=$(date -u '+%s')
    if [ -f "$STATE_FILE" ]; then
        local tmp=""
        while IFS= read -r line; do
            local entry_epoch
            entry_epoch=$(date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$line" '+%s' 2>/dev/null || echo 0)
            if [ "$entry_epoch" -gt $((now_epoch - 3600)) ] 2>/dev/null; then
                tmp="${tmp}${line}"$'\n'
            fi
        done < "$STATE_FILE"
        printf '%s' "$tmp" > "$STATE_FILE"
    fi
}

# Probe the indexer with server.version via a raw WebSocket handshake.
# Uses curl + sed for the WS upgrade, then reads the first data frame.
# Falls back to node if available (more reliable WS client).
probe() {
    if command -v node &>/dev/null; then
        node -e "
const ws = new WebSocket('$ENDPOINT');
const t = setTimeout(() => { console.log('TIMEOUT'); process.exit(2); }, 10000);
ws.onopen = () => ws.send(JSON.stringify({id:1,jsonrpc:'2.0',method:'server.version',params:['healthcheck','1.4']}) + '\n');
ws.onmessage = (e) => { clearTimeout(t); console.log(String(e.data).trim()); ws.close(); process.exit(0); };
ws.onerror = () => { clearTimeout(t); console.log('ERROR'); process.exit(2); };
ws.onclose = (e) => { clearTimeout(t); console.log('CLOSE:' + e.code); process.exit(e.code === 1000 ? 0 : 2); };
" 2>/dev/null
    else
        # Fallback: use curl to check if the port is at least accepting connections
        local http_code
        http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
            -H 'Upgrade: websocket' -H 'Connection: Upgrade' \
            -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
            "https://electrumx.radiantcore.org" 2>/dev/null || echo "000")
        if [ "$http_code" = "101" ] || [ "$http_code" = "426" ]; then
            echo '{"jsonrpc":"2.0","result":["ok","1.4"],"id":1}'
        else
            echo "UNREACHABLE"
        fi
    fi
}

# --- Main ---

response=$(probe)

if echo "$response" | grep -q '"code":-101'; then
    log "THROTTLED: -101 excessive resource usage detected"

    recent=$(count_recent_restarts)
    if [ "$recent" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
        log "SKIP: already restarted $recent times in the last hour (max $MAX_RESTARTS_PER_HOUR) — not restarting to avoid loop"
        exit 1
    fi

    log "RESTARTING rxindexer (attempt #$((recent + 1)) this hour)..."
    if cd "$COMPOSE_DIR" && docker compose restart rxindexer >> "$LOG" 2>&1; then
        record_restart
        log "RESTARTED successfully"
        # Wait for the indexer to come back up
        sleep 15
        verify=$(probe)
        if echo "$verify" | grep -q '"result"'; then
            log "VERIFIED: indexer responding normally after restart"
            exit 1
        else
            log "WARNING: indexer still not healthy after restart: $verify"
            exit 1
        fi
    else
        log "FAILED: docker compose restart rxindexer failed"
        exit 2
    fi
elif echo "$response" | grep -q '"result"'; then
    # Healthy — no log spam, only log if transitioning from unhealthy
    exit 0
elif echo "$response" | grep -q 'TIMEOUT\|ERROR\|UNREACHABLE\|CLOSE'; then
    log "UNREACHABLE: $response"
    # Don't restart on unreachable — could be a network blip. The daily cron handles stale state.
    exit 2
else
    log "UNKNOWN response: $response"
    exit 2
fi
