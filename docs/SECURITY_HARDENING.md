# ElectrumX Security Hardening Guide

This guide covers security best practices for deploying ElectrumX in production environments.

---

## Table of Contents

1. [Network Security](#1-network-security)
2. [Authentication & Access Control](#2-authentication--access-control)
3. [Rate Limiting](#3-rate-limiting)
4. [SSL/TLS Configuration](#4-ssltls-configuration)
5. [Docker Security](#5-docker-security)
6. [Monitoring & Logging](#6-monitoring--logging)
7. [Backup & Recovery](#7-backup--recovery)

---

## 1. Network Security

### Firewall Configuration

```bash
# Allow only necessary ports
# Public Electrum ports
sudo ufw allow 50001/tcp  # TCP
sudo ufw allow 50002/tcp  # SSL
sudo ufw allow 50004/tcp  # WSS (if needed)

# Block RPC from external access
sudo ufw deny 8000/tcp

# Enable firewall
sudo ufw enable
```

### Port Recommendations

| Port | Protocol | Access | Description |
|------|----------|--------|-------------|
| 50001 | TCP | Public | Electrum TCP (unencrypted) |
| 50002 | SSL | Public | Electrum SSL (recommended) |
| 50004 | WSS | Public | WebSocket Secure |
| 8000 | HTTP | **Internal only** | RPC interface |

### Reverse Proxy (Recommended)

Use nginx as a reverse proxy for additional security:

```nginx
# /etc/nginx/sites-available/electrumx
server {
    listen 50002 ssl;
    server_name electrumx.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=electrum:10m rate=10r/s;
    limit_req zone=electrum burst=20 nodelay;

    location / {
        proxy_pass http://127.0.0.1:50001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 2. Authentication & Access Control

### RPC Credentials

**CRITICAL**: Always change default RPC credentials.

```bash
# .env or environment variables
RPC_USER=your_secure_username
RPC_PASSWORD=$(openssl rand -base64 32)
```

### Environment Variables

```bash
# Required security settings
COIN=Radiant
NET=mainnet
DB_DIRECTORY=/data/electrumx

# Authentication
RPC_USER=electrumx_admin
RPC_PASSWORD=<strong_random_password>

# Restrict RPC to localhost
RPC_HOST=127.0.0.1

# Daemon credentials (for Radiant node)
DAEMON_URL=http://user:password@127.0.0.1:7332
```

### File Permissions

```bash
# Restrict config file access
chmod 600 .env
chmod 600 config.env

# Restrict data directory
chmod 700 /data/electrumx
chown electrumx:electrumx /data/electrumx
```

---

## 3. Rate Limiting

### Built-in Rate Limiting

Configure cost limits to prevent abuse:

```bash
# .env configuration
COST_SOFT_LIMIT=1000    # Soft limit per session
COST_HARD_LIMIT=10000   # Hard limit (disconnect if exceeded)
BANDWIDTH_UNIT_COST=5000
REQUEST_TIMEOUT=30
SESSION_TIMEOUT=600
```

### Cost Limit Explanation

| Setting | Recommended | Description |
|---------|-------------|-------------|
| COST_SOFT_LIMIT | 1000 | Warning threshold |
| COST_HARD_LIMIT | 10000 | Disconnect threshold |
| BANDWIDTH_UNIT_COST | 5000 | Cost per bandwidth unit |
| REQUEST_TIMEOUT | 30 | Seconds before request timeout |
| SESSION_TIMEOUT | 600 | Seconds of inactivity before disconnect |

### Connection Limits

```bash
# Maximum concurrent sessions
MAX_SESSIONS=1000

# Maximum pending requests per session
MAX_SEND=10

# Maximum subscriptions per session
MAX_SUBS=10000
```

---

## 4. SSL/TLS Configuration

### Generate SSL Certificate

**Option A: Let's Encrypt (Recommended for production)**

```bash
# Install certbot
sudo apt install certbot

# Generate certificate
sudo certbot certonly --standalone -d electrumx.yourdomain.com

# Set environment variables
SSL_CERTFILE=/etc/letsencrypt/live/yourdomain.com/fullchain.pem
SSL_KEYFILE=/etc/letsencrypt/live/yourdomain.com/privkey.pem
```

**Option B: Self-signed (Development only)**

```bash
# Generate self-signed certificate
openssl req -x509 -nodes -days 365 -newkey rsa:4096 \
  -keyout electrumx.key \
  -out electrumx.crt \
  -subj "/CN=electrumx.local"

# Set environment variables
SSL_CERTFILE=/path/to/electrumx.crt
SSL_KEYFILE=/path/to/electrumx.key
```

### Environment Configuration

```bash
# Enable SSL
SSL_CERTFILE=/etc/letsencrypt/live/yourdomain.com/fullchain.pem
SSL_KEYFILE=/etc/letsencrypt/live/yourdomain.com/privkey.pem

# SSL port
SSL_PORT=50002

# Disable unencrypted TCP in production
TCP_PORT=
```

---

## 5. Docker Security

### Docker Compose Security

```yaml
# docker-compose.yml
version: '3.8'
services:
  electrumx:
    image: electrumx:latest
    container_name: electrumx
    restart: unless-stopped
    user: "1000:1000"  # Non-root user
    read_only: true    # Read-only filesystem
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    tmpfs:
      - /tmp:noexec,nosuid,nodev
    volumes:
      - electrumx-data:/data:rw
      - ./certs:/certs:ro
    environment:
      - COIN=Radiant
      - NET=mainnet
      - DB_DIRECTORY=/data
    ports:
      - "50002:50002"  # SSL only
    networks:
      - electrumx-net

networks:
  electrumx-net:
    driver: bridge

volumes:
  electrumx-data:
```

### Container Hardening

```bash
# Run as non-root
docker run --user 1000:1000 ...

# Drop all capabilities
docker run --cap-drop ALL ...

# Read-only root filesystem
docker run --read-only ...

# No privilege escalation
docker run --security-opt no-new-privileges ...
```

---

## 6. Monitoring & Logging

### Enable Logging

```bash
# Log level (debug, info, warning, error)
LOG_LEVEL=info

# Log format
LOG_FORMAT=%(asctime)s %(levelname)s:%(name)s: %(message)s
```

### Monitor Key Metrics

```bash
# Check session count
echo '{"id": 1, "method": "server.peers.subscribe"}' | nc localhost 50001

# Check server info
electrumx_rpc getinfo
```

### Log Monitoring

```bash
# Watch for suspicious activity
tail -f /var/log/electrumx.log | grep -E "(error|warning|reject|ban)"

# Monitor connection attempts
journalctl -u electrumx -f | grep "connection"
```

### Prometheus Metrics (Optional)

```bash
# Enable Prometheus metrics
PROMETHEUS_PORT=9090

# Scrape config for Prometheus
scrape_configs:
  - job_name: 'electrumx'
    static_configs:
      - targets: ['localhost:9090']
```

---

## 7. Backup & Recovery

### Database Backup

```bash
# Stop ElectrumX before backup
docker stop electrumx

# Backup database
tar -czvf electrumx-backup-$(date +%Y%m%d).tar.gz /data/electrumx

# Restart
docker start electrumx
```

### Recovery Procedure

```bash
# Stop service
docker stop electrumx

# Restore from backup
rm -rf /data/electrumx/*
tar -xzvf electrumx-backup-YYYYMMDD.tar.gz -C /

# Restart
docker start electrumx
```

---

## Security Checklist

### Pre-Deployment

- [ ] Changed default RPC credentials
- [ ] Generated SSL certificates
- [ ] Configured firewall rules
- [ ] Set rate limiting values
- [ ] Running as non-root user
- [ ] Database directory has correct permissions

### Production

- [ ] Using Let's Encrypt or trusted CA certificate
- [ ] Disabled TCP port (SSL only)
- [ ] RPC restricted to localhost
- [ ] Log monitoring configured
- [ ] Backup schedule established
- [ ] Docker security options applied

### Ongoing

- [ ] Regular security updates
- [ ] Certificate renewal automated
- [ ] Log review schedule
- [ ] Performance monitoring
- [ ] Backup verification

---

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do NOT** open a public GitHub issue
2. Email: radiantblockchain@protonmail.com
3. Subject: `[SECURITY] ElectrumX - Brief Description`

---

*Last updated: January 27, 2026*
