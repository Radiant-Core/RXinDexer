# /Users/radiant/Desktop/RXinDexer/docs/deployment-guide.md
# This file provides detailed instructions for deploying RXinDexer in production environments.
# It covers infrastructure requirements, security considerations, and monitoring strategies.

# RXinDexer Production Deployment Guide

This guide provides comprehensive instructions for deploying RXinDexer in a production environment, ensuring reliability, security, and optimal performance.

## Infrastructure Requirements

### Recommended Hardware

For production deployments supporting moderate traffic (up to ~1000 API requests/minute):

| Component | Minimum Specifications | Recommended Specifications |
|-----------|------------------------|----------------------------|
| CPU | 4 cores | 8+ cores |
| RAM | 16 GB | 32+ GB |
| Storage | 500 GB SSD | 1+ TB NVMe SSD |
| Network | 100 Mbps | 1 Gbps |

For high-traffic deployments (1000+ API requests/minute), consider horizontal scaling with multiple API instances.

### Operating System

- Ubuntu 22.04 LTS or later (recommended)
- Debian 11 or later
- Red Hat Enterprise Linux 8 or later

## Production Deployment Options

### Docker Deployment (Recommended)

The simplest and most reliable deployment method is using Docker and docker-compose.

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Radiant-Core/RXinDexer.git
   cd RXinDexer
   ```

2. **Configure production environment**:
   ```bash
   cp .env.example .env.production
   # Edit .env.production with your production settings
   ```

3. **Key production environment variables**:
   ```
   # Set environment to production
   ENVIRONMENT=production
   
   # Database configuration
   DATABASE_URL=postgresql://user:password@db:5432/rxindexer
   
   # RPC configuration
   RADIANT_RPC_URL=http://radiant:7332
   RADIANT_RPC_USER=secure_rpc_user
   RADIANT_RPC_PASSWORD=strong_password_here
   
   # Redis configuration
   REDIS_HOST=redis
   REDIS_PORT=6379
   REDIS_PASSWORD=strong_redis_password
   
   # Logging and performance
   LOG_LEVEL=INFO
   SYNC_BATCH_SIZE=1000
   SYNC_MAX_WORKERS=16
   
   # API settings
   API_WORKERS=4
   MAX_REQUESTS_PER_MINUTE=600
   ```

4. **Update docker-compose.yml with production settings**:
   ```bash
   cp docker-compose.yml docker-compose.production.yml
   # Edit docker-compose.production.yml
   ```

5. **Start the production stack**:
   ```bash
   docker-compose -f docker-compose.production.yml up -d
   ```

### Native Deployment

For deployments without Docker:

1. **Install dependencies**:
   ```bash
   # Install PostgreSQL 16
   sudo apt install -y postgresql-16
   
   # Install Redis
   sudo apt install -y redis-server
   
   # Install Python 3.11
   sudo apt install -y python3.11 python3.11-dev python3.11-venv
   
   # Install system dependencies
   sudo apt install -y build-essential libpq-dev
   ```

2. **Set up the application**:
   ```bash
   # Create a dedicated user
   sudo useradd -m rxindexer
   sudo su - rxindexer
   
   # Clone the repository
   git clone https://github.com/Radiant-Core/RXinDexer.git
   cd RXinDexer
   
   # Set up virtual environment
   python3.11 -m venv venv
   source venv/bin/activate
   
   # Install dependencies
   pip install -r requirements.txt
   ```

3. **Configure the application**:
   ```bash
   cp .env.example .env.production
   # Edit .env.production with your settings
   ```

4. **Set up a systemd service for the API**:
   Create `/etc/systemd/system/rxindexer-api.service`:
   ```
   [Unit]
   Description=RXinDexer API Service
   After=network.target postgresql.service redis-server.service
   
   [Service]
   User=rxindexer
   Group=rxindexer
   WorkingDirectory=/home/rxindexer/RXinDexer
   Environment="PATH=/home/rxindexer/RXinDexer/venv/bin"
   EnvironmentFile=/home/rxindexer/RXinDexer/.env.production
   ExecStart=/home/rxindexer/RXinDexer/venv/bin/gunicorn src.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
   Restart=always
   
   [Install]
   WantedBy=multi-user.target
   ```

5. **Set up a systemd service for the indexer**:
   Create `/etc/systemd/system/rxindexer-sync.service`:
   ```
   [Unit]
   Description=RXinDexer Sync Service
   After=network.target postgresql.service
   
   [Service]
   User=rxindexer
   Group=rxindexer
   WorkingDirectory=/home/rxindexer/RXinDexer
   Environment="PATH=/home/rxindexer/RXinDexer/venv/bin"
   EnvironmentFile=/home/rxindexer/RXinDexer/.env.production
   ExecStart=/home/rxindexer/RXinDexer/venv/bin/python -m sync.rxindex_sync --initialize --continuous --interval 60
   Restart=always
   
   [Install]
   WantedBy=multi-user.target
   ```

6. **Enable and start the services**:
   ```bash
   sudo systemctl enable rxindexer-api
   sudo systemctl start rxindexer-api
   sudo systemctl enable rxindexer-sync
   sudo systemctl start rxindexer-sync
   ```

## Security Considerations

### Database Security

1. **Use strong passwords**:
   - Generate a strong, unique password for the database user
   - Store passwords securely (not in plain text in configuration files)

2. **Network security**:
   - Configure PostgreSQL to only allow connections from specified IPs
   - Use SSL for database connections

3. **Least privilege**:
   - Create a dedicated database user for RXinDexer
   - Grant only the necessary permissions

Example PostgreSQL secure configuration:
```
# In postgresql.conf
listen_addresses = 'localhost,internal_ip'
ssl = on
ssl_cert_file = '/path/to/cert.pem'
ssl_key_file = '/path/to/key.pem'

# In pg_hba.conf
hostssl rxindexer rxindexer_user 10.0.0.0/24 md5
```

### RPC Security

1. **Secure RPC configuration**:
   ```
   # In radiant.conf
   rpcuser=secure_rpc_user
   rpcpassword=strong_password_here
   rpcallowip=10.0.0.0/24
   ```

2. **Consider using an SSL proxy** for RPC connections outside of a secured network.

### API Security

1. **Use a reverse proxy** (Nginx or Traefik) with HTTPS:
   ```nginx
   server {
       listen 443 ssl;
       server_name api.rxindexer.example.com;
       
       ssl_certificate /path/to/cert.pem;
       ssl_certificate_key /path/to/key.pem;
       
       location / {
           proxy_pass http://localhost:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

2. **Implement rate limiting** to prevent abuse:
   - Configure in Nginx/Traefik or use the built-in rate limiting

3. **Consider adding API keys** for sensitive endpoints (implemented in the `.env` file)

## Monitoring and Maintenance

### Monitoring

1. **Health checks**:
   - Use the `/health` and `/api/v1/health` endpoints
   - Configure monitoring tools to alert on service unavailability

2. **Set up Prometheus and Grafana** for metrics:
   - CPU, memory, and disk usage
   - API request rates and response times
   - Sync progress and lag behind chain tip
   - Database query performance

3. **Log monitoring**:
   - Centralize logs using Elasticsearch, Logstash, and Kibana (ELK)
   - Set up alerts for ERROR level logs

### Backup Strategy

1. **Database backups**:
   ```bash
   # Create a daily PostgreSQL backup
   pg_dump -U postgres rxindexer > /backup/rxindexer_$(date +%Y-%m-%d).sql
   ```

2. **Configure backup rotation**:
   - Keep daily backups for 7 days
   - Weekly backups for 1 month
   - Monthly backups for 1 year

3. **Test restoration procedures regularly**

### Scaling Strategies

#### Vertical Scaling

Increase resources on the existing server:
- Upgrade CPU, RAM, and disk capacity
- Increase PostgreSQL shared_buffers and work_mem
- Adjust Redis maxmemory

#### Horizontal Scaling

For high-load environments:
1. **API layer**:
   - Deploy multiple API instances behind a load balancer
   - Configure session affinity if needed

2. **Database layer**:
   - Use PostgreSQL read replicas for query distribution
   - Consider database sharding for very large datasets

3. **Caching layer**:
   - Deploy Redis in cluster mode
   - Implement a distributed caching strategy

## Upgrading Procedures

### Zero-Downtime Upgrades

1. **For API services**:
   - Deploy new instances
   - Gradually shift traffic using rolling updates
   - Monitor for errors
   - Complete the transition

2. **For database migrations**:
   - Review migration scripts for backward compatibility
   - Apply migrations with minimal locks
   - Consider blue-green deployment for major schema changes

### Rollback Procedures

1. **Prepare rollback scripts** for each deployment
2. **Maintain versioned Docker images** for quick rollback
3. **Document specific rollback steps** for each major release

## Performance Tuning

### PostgreSQL Tuning

```
# Memory settings
shared_buffers = 8GB  # 25% of system RAM
work_mem = 64MB
maintenance_work_mem = 512MB

# Checkpointing
checkpoint_timeout = 15min
max_wal_size = 2GB
checkpoint_completion_target = 0.9

# Query planning
effective_cache_size = 24GB  # 75% of system RAM
random_page_cost = 1.1  # For SSDs

# Parallel query execution
max_worker_processes = 16
max_parallel_workers_per_gather = 8
max_parallel_workers = 16
```

### Application Tuning

Adjust these parameters in `.env.production`:

```
# Sync performance
SYNC_BATCH_SIZE=1000  # Adjust based on server capacity
SYNC_MAX_WORKERS=16   # Match to CPU core count
UTXO_MAX_WORKERS=8    # For UTXO processing

# Caching strategy
CACHE_TTL_SECONDS=300  # TTL for cached data
ENABLE_CACHE_WARMUP=true  # Pre-warm cache for common queries
```

## Troubleshooting

### Common Issues

1. **Database connection errors**:
   - Check network connectivity
   - Verify PostgreSQL is running and accessible
   - Confirm database user credentials
   - Check connection limits

2. **RPC connection issues**:
   - Verify Radiant node is running and fully synced
   - Check RPC credentials
   - Confirm network connectivity to RPC
   - Verify node has appropriate bandwidth

3. **API performance degradation**:
   - Check database query performance
   - Review Redis cache hit rates
   - Monitor system resources for bottlenecks
   - Check for slow client connections

### Diagnostic Commands

```bash
# Check API service status
systemctl status rxindexer-api

# Check sync service status
systemctl status rxindexer-sync

# View API logs
journalctl -u rxindexer-api -f

# View sync logs
journalctl -u rxindexer-sync -f

# Check database connection
psql -U rxindexer_user -h localhost -d rxindexer -c "SELECT version();"

# Test RPC connection
curl -X POST -H "Content-Type: application/json" -d '{"jsonrpc":"1.0","id":"test","method":"getblockcount","params":[]}' http://user:password@localhost:7332/
```

## Support and Resources

- **GitHub Repository**: [https://github.com/Radiant-Core/RXinDexer](https://github.com/Radiant-Core/RXinDexer)
- **Documentation**: [https://docs.rxindexer.io](https://docs.rxindexer.io)
- **Issue Tracker**: [https://github.com/Radiant-Core/RXinDexer/issues](https://github.com/Radiant-Core/RXinDexer/issues)

For urgent production support, contact the Radiant Core team at support@radiantblockchain.org.
