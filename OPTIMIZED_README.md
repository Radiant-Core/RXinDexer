# RXinDexer Optimized Deployment

## Overview

This package contains a fully optimized deployment of RXinDexer - a lightweight, scalable indexer for the Radiant (RXD) blockchain. All database performance optimizations have been integrated into the Docker setup, making it easy to run on any test machine. The deployment uses a local copy of the Radiant Node located at `/Users/radiant/Desktop/RXinDexer/Radiant-Node-master`.

## Performance Optimizations

The following optimizations have been applied to enhance database performance:

1. **Materialized View for Balances**: Reduces CPU usage by pre-calculating address balances
2. **Smart Query Routing**: Redirects slow queries to use optimized alternatives
3. **Efficient Index Design**: Custom indexes for common query patterns
4. **Automated Maintenance**: Regular database maintenance to keep performance optimal
5. **Resource Controls**: Prevents runaway queries with timeouts and monitoring

## Quick Start

Make the deployment script executable:

```bash
chmod +x run_optimized.sh
```

Start the optimized stack:

```bash
./run_optimized.sh start
```

This will start:
- RXinDexer API at http://localhost:8000
- PostgreSQL database with all optimizations
- Redis cache
- Radiant Node
- Monitoring dashboard at http://localhost:3000

## File Structure

```
/Users/radiant/Desktop/RXinDexer/
├── optimized-docker-compose.yml  # Optimized Docker Compose configuration
├── run_optimized.sh              # Deployment script for test machines
├── Radiant-Node-master/          # Local copy of Radiant Node source code
│   ├── Dockerfile                # Dockerfile for building Radiant Node
│   └── ...                       # Other Radiant Node source files
├── docker/                       # Docker configuration files
│   ├── postgresql.conf           # Optimized PostgreSQL configuration
│   ├── postgresql-init.sql       # Database initialization with optimizations
│   └── radiant.conf              # Radiant Node configuration
├── src/                          # Application source code
│   ├── utils/
│   │   ├── db_optimizations.py   # Database optimization utilities
│   │   └── db_maintenance.py     # Automated database maintenance
│   └── ...
└── logs/                         # Application logs
```

## Using the Deployment Script

The `run_optimized.sh` script provides simple commands:

```bash
# Start the stack
./run_optimized.sh start

# View logs for a specific service
./run_optimized.sh logs db
./run_optimized.sh logs indexer

# Show monitoring metrics
./run_optimized.sh metrics

# Stop the stack
./run_optimized.sh stop

# Clean up (removes all data)
./run_optimized.sh clean
```

## Database Optimizations Details

### 1. Materialized View for Balance Calculations

We've created a materialized view `address_balances` that pre-calculates all address balances:

```sql
CREATE MATERIALIZED VIEW address_balances AS
SELECT 
    address,
    SUM(amount) as total_balance
FROM utxos
WHERE spent = FALSE
GROUP BY address;
```

This view is refreshed automatically when data changes, making balance queries extremely fast.

### 2. Query Optimization

Slow balance queries have been replaced with efficient alternatives:

```sql
-- Original slow query:
SELECT address, SUM(amount) as total_balance
FROM utxos
WHERE spent = FALSE
GROUP BY address
HAVING SUM(amount) > 1000000000

-- Optimized query using materialized view:
SELECT address, total_balance
FROM address_balances
WHERE total_balance > 1000000000
```

### 3. Index Optimization

Specialized indexes have been added:

```sql
CREATE INDEX idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE;
CREATE INDEX idx_address_balances_balance ON address_balances (total_balance DESC);
```

### 4. Temporary Table Optimization

Slow temporary table creations have been replaced with efficient alternatives that use the materialized view.

## Monitoring

The deployment includes a Grafana dashboard at http://localhost:3000 (default login: admin/admin) connected to Prometheus for monitoring system metrics.

You can also check basic metrics with:

```bash
./run_optimized.sh metrics
```

## Troubleshooting

If you encounter any issues:

1. Check container logs:
   ```bash
   ./run_optimized.sh logs db
   ./run_optimized.sh logs indexer
   ./run_optimized.sh logs radiant
   ```

2. Ensure Docker has enough resources allocated (CPU/Memory)

3. Check database status:
   ```bash
   docker exec rxindexer-db psql -U postgres -d rxindexer -c "SELECT version();"
   ```

4. Verify the Radiant Node is properly built:
   ```bash
   # Check if the Radiant Node container is running
   docker ps | grep rxindexer-radiant
   
   # Check Radiant Node logs for any build or startup issues
   docker logs rxindexer-radiant
   ```

## Requirements

- Docker Engine 20.10+
- Docker Compose v2+
- At least 8GB RAM and 4 CPU cores recommended
- Local Radiant Node directory at `/Users/radiant/Desktop/RXinDexer/Radiant-Node-master`

## Security Notes

- Default passwords are used for simplicity in testing environments
- For production, use proper secrets management and stronger passwords
