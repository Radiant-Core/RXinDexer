# Database Maintenance for RXinDexer

This directory contains the database maintenance scripts and configurations for the RXinDexer system.

## Overview

The database maintenance system is designed to keep the PostgreSQL database running efficiently by performing regular maintenance tasks such as:

- **VACUUM** and **ANALYZE** operations
- Partition maintenance
- Backup creation and management
- Performance monitoring and optimization
- Long-running query detection
- Index maintenance

## Components

### 1. `db_maintenance.sh`

The main maintenance script that coordinates all maintenance activities. It should be run on a schedule (e.g., via cron or as a systemd service).

### 2. Docker Configuration

- `docker/Dockerfile.db-maintenance`: Dockerfile for the maintenance container
- `docker-compose.yml`: Contains the `rxindexer-db-maintenance` service definition
- `db-init-scripts/`: SQL scripts for database initialization and maintenance functions

### 3. Optimization Scripts

Located in `db/optimization/`:

- `maintenance.sql`: Database maintenance functions and procedures
- `monitoring.sql`: Monitoring and logging functions
- `performance.sql`: Performance optimization functions and indexes

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_USER` | Database username | `postgres` |
| `POSTGRES_PASSWORD` | Database password | `postgres` |
| `POSTGRES_DB` | Database name | `rxindexer` |
| `POSTGRES_HOST` | Database host | `db` |
| `POSTGRES_PORT` | Database port | `5432` |
| `BACKUP_DIR` | Directory for database backups | `/backups` |
| `LOG_DIR` | Directory for log files | `/app/logs` |
| `RETENTION_DAYS` | Number of days to keep backups and logs | `30` |
| `MAINTENANCE_WINDOW_START` | Start time for maintenance window | `02:00` |
| `MAINTENANCE_WINDOW_END` | End time for maintenance window | `04:00` |
| `VACUUM_THRESHOLD` | Minimum dead tuples percentage to trigger VACUUM | `50` |
| `ANALYZE_THRESHOLD` | Minimum changed rows percentage to trigger ANALYZE | `10` |
| `LONG_RUNNING_QUERY_THRESHOLD` | Threshold in seconds for long-running queries | `300` (5 minutes) |
| `SLOW_QUERY_THRESHOLD` | Threshold in milliseconds for slow queries | `1000` (1 second) |

## Usage

### Running Manually

```bash
# Set environment variables
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=your_password
export POSTGRES_DB=rxindexer
export POSTGRES_HOST=localhost

# Run the maintenance script
./scripts/db_maintenance.sh
```

### Using Docker Compose

The maintenance service is included in the main `docker-compose.yml` file and will run automatically with the rest of the services.

```bash
# Start all services including the maintenance service
docker-compose up -d

# View logs for the maintenance service
docker-compose logs -f rxindexer-db-maintenance
```

### Scheduling with Cron

To schedule the maintenance script to run regularly, add a cron job:

```bash
# Edit the crontab
crontab -e

# Add this line to run the script daily at 2:30 AM
30 2 * * * /path/to/scripts/db_maintenance.sh >> /var/log/db_maintenance.log 2>&1
```

## Monitoring

The maintenance service includes health checks and logging:

- Logs are written to `LOG_DIR` (default: `/app/logs`)
- Health status is available via Docker health checks
- Maintenance history is stored in the `maintenance_history` table

## Troubleshooting

### Common Issues

1. **Permission denied when writing to backup directory**
   - Ensure the backup directory exists and is writable by the container user
   - Check Docker volume permissions

2. **Connection refused to database**
   - Verify the database is running and accessible
   - Check `POSTGRES_HOST` and `POSTGRES_PORT` environment variables
   - Check firewall rules if running on separate hosts

3. **Maintenance tasks taking too long**
   - Consider adjusting the maintenance window
   - Review and optimize maintenance parameters
   - Check for long-running transactions that might block maintenance

### Viewing Logs

```bash
# View container logs
docker-compose logs rxindexer-db-maintenance

# View log files in the container
docker-compose exec rxindexer-db-maintenance tail -f /app/logs/db_maintenance_*.log
```

## Maintenance Tasks

The following tasks are performed by the maintenance system:

### Scheduled Tasks

| Task | Frequency | Description |
|------|-----------|-------------|
| VACUUM ANALYZE | Daily | Reclaims storage and updates statistics |
| Partition Maintenance | Daily | Manages table partitions |
| Backup | Daily | Creates database backups |
| Log Rotation | Daily | Rotates log files |
| Index Maintenance | Weekly | Rebuilds or reindexes indexes |

### On-Demand Tasks

- Long-running query detection
- Deadlock monitoring
- Connection pool management
- Performance analysis

## Best Practices

1. **Monitor disk space** for backup directory
2. **Review logs** regularly for warnings or errors
3. **Adjust parameters** based on database size and workload
4. **Test backups** regularly
5. **Monitor maintenance performance** and adjust schedules as needed

## Security Considerations

- The maintenance user should have minimal required permissions
- Backup files should be encrypted if they contain sensitive data
- Access to maintenance scripts and logs should be restricted
- Database credentials should be managed securely (e.g., using Docker secrets)
