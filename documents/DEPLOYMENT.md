# RXinDexer Deployment & Operations Guide

**Last Updated:** December 2025

This guide covers production deployment, monitoring, alerting, and day-to-day operations for RXinDexer.

## Quick Start

```bash
cd /path/to/RXinDexer_1/docker
docker compose up -d
```

This starts all services:
- **radiant-node** - Radiant blockchain node
- **rxindexer-db** - PostgreSQL database
- **rxindexer-api** - FastAPI REST API (port 8000)
- **rxindexer-indexer** - Blockchain indexer
- **rxindexer-balance-refresh** - Wallet balance cache updater

## System Requirements

### Hardware
| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 8 GB | 16 GB |
| Storage | 250 GB SSD | 500 GB NVMe |
| CPU | 4 cores | 8 cores |

### macOS Users (CRITICAL)
**Recommended Tool:** [OrbStack](https://orbstack.dev/) (Instead of Docker Desktop)
- **Why?** Docker Desktop for Mac often suffers from file system (VirtioFS) instability under heavy database I/O. OrbStack is significantly more stable and faster.
- **Storage:** MUST use **Docker Named Volumes** backed by a fast External SSD (Thunderbolt 3/4 recommended).
- **PostgreSQL Tuning:** We have enabled `synchronous_commit=off` and increased `shared_buffers` to 4GB. This maximizes write throughput for initial sync but requires a stable power supply (UPS recommended) to prevent data corruption on power loss.

### Windows Users
**Recommended Environment:** **WSL 2** (Windows Subsystem for Linux)
- **Storage:** Use **Docker Named Volumes**. Do not bind-mount the database to a Windows path.
- **Hardware:** External NVMe/SSD via USB 3.2+ or Thunderbolt is highly recommended.

### Linux Users
- **Performance:** Linux offers the best native performance.
- **Tuning:** `docker-compose.yml` is already tuned for high-memory environments (8GB+ RAM).

## Initial Sync Optimization
The indexer is configured for **"Performance Sync"** mode:
1.  **Aggressive Batching:** Syncs 500 blocks at a time during catch-up (Lag > 50k), 100 blocks (Lag > 10k), scaling down to 10 near the tip.
2.  **Spent Checks Skipped:** During initial catch-up, `spent` status updates are skipped to prevent I/O bottlenecks.
3.  **Automated Backfill:** Once synced (Lag < 5 blocks), the `backfill_spent.py` script automatically runs to mark spent UTXOs.
4.  **Schema Alignment:** The database now includes `container`, `author`, `ticker`, and `latest_height` fields to fully support Photonic Wallet features.

## Wallet Balance Cache
For production performance, wallet balances are pre-computed in the `wallet_balances` table:

### Initial Population (Required after first sync)
```bash
# Run once after initial sync completes
docker exec -it rxindexer-indexer python /app/indexer/refresh_balances.py
```

### Automatic Refresh
The `balance-refresh` service automatically updates balances every 5 minutes:
```bash
# Start the balance refresh service
docker compose up -d balance-refresh
```

### Manual Refresh
```bash
docker exec -it rxindexer-balance-refresh python /app/indexer/refresh_balances.py
```

### Cache Status
Check the wallet_balances table status:
```sql
SELECT COUNT(*) as wallets, MAX(last_updated) as last_refresh FROM wallet_balances;
```


## Deployment

### Prerequisites
- Docker & Docker Compose v2+
- OrbStack (macOS) or Docker Desktop
- 250+ GB free disk space

### Fresh Install
```bash
cd /path/to/RXinDexer_1/docker
docker compose up -d
```

The system will:
1. Start PostgreSQL and wait for it to be healthy
2. Run Alembic migrations to create the database schema
3. Start the Radiant node and begin syncing the blockchain
4. Start the indexer to process blocks into the database
5. Start the API server on port 8000

### Upgrades
```bash
git pull origin main
cd docker
docker compose build --no-cache
docker compose up -d
```

### Full Reset (Caution: Deletes all data)
```bash
cd docker
docker compose down -v
docker compose up -d
```

## Operations & Monitoring

### Health Checks
- **Database Health**: `GET /health/db`
- **System Status**: `GET /status`
- **Logs**:
  ```bash
  docker compose -f docker/docker-compose.yml logs -f --tail 100
  ```

### Automated Monitoring
The indexer includes a built-in background monitoring thread (see `indexer/monitor.py` and `indexer/daemon.py`):
- **Check Interval**: Every 5 minutes.
- **Metrics**: Sync lag, CPU usage, Memory usage, DB/API connectivity.
- **Periodic Status**: Every 30 minutes, a summary is logged.
- **Alert Logic**:
  - **Sync Lag**: Alert if > 10,000 blocks (customizable).
  - **Resources**: Alert if CPU or RAM > 90%.
  - **Stall Detection**: Alerts if sync lag increases for 3 consecutive checks.

### Manual Operations
- **Trigger Spent Backfill**: If the automated backfill was interrupted or you need to re-run it:
  ```bash
  docker exec -it rxindexer-indexer python3 -m indexer.backfill_spent
  ```

### Alerting Setup
To enable email or webhook notifications:
1. Edit `indexer/monitor.py`.
2. Locate the `monitor_all()` function.
3. Configure the `alerts` thresholds.
4. Uncomment and configure the **Email Alert Snippet** with your SMTP settings.

### Backups
- Schedule regular PostgreSQL backups:
  ```bash
  docker exec -t rxindexer-db pg_dumpall -c -U rxindexer > dump_$(date +%Y-%m-%d).sql
  ```

## Glyph Token Indexing

RXinDexer automatically indexes Glyph tokens (protocol `676c79`).

### Performance
- **Indexes**: GIN index on metadata, composite indexes for owner/type.
- **Monitoring Queries**:
  ```sql
  -- Total tokens
  SELECT COUNT(*) FROM glyph_tokens;
  -- Tokens by type
  SELECT type, COUNT(*) FROM glyph_tokens GROUP BY type;
  ```

## Maintenance & Cleanup

### Manual Cleanup
Run on-demand to free disk space:
```bash
cd /Users/rxindexer/Desktop/RXinDexer_1/docker
docker compose --profile maintenance run --rm cleanup
```

### Automated Weekly Cleanup (macOS)
Install the launchd job to run cleanup every Sunday at 3 AM:
```bash
cp docker/com.rxindexer.cleanup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rxindexer.cleanup.plist
```

To uninstall:
```bash
launchctl unload ~/Library/LaunchAgents/com.rxindexer.cleanup.plist
rm ~/Library/LaunchAgents/com.rxindexer.cleanup.plist
```

### What Gets Cleaned
- **Dangling images**: Untagged images from old builds
- **Build cache**: Docker build cache older than 24 hours
- **Orphaned volumes**: Volumes not attached to any container (excludes `pgdata` and `radiant-data`)

### Check Disk Usage
```bash
docker system df -v
```

## Security
- **Secrets**: Never commit `.env` files. Use Docker secrets in production.
- **Access**: Restrict access to `/admin` endpoints via reverse proxy configuration (Nginx/Traefik).
- **Firewall**: Ensure only the API port (8000) is exposed publicly if needed.
