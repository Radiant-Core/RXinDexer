# RXinDexer Monitoring Setup

This directory contains the complete monitoring and alerting stack for RXinDexer using Prometheus, Grafana, and Alertmanager.

## Components

- **Prometheus**: Metrics collection and storage
- **Grafana**: Visualization and dashboards
- **Alertmanager**: Alert routing and notification
- **Node Exporter**: System metrics
- **PostgreSQL Exporter**: Database metrics

## Quick Start

```bash
# Start the monitoring stack
docker-compose -f docker-compose.monitoring.yml up -d

# Access services
# Grafana: http://localhost:3001 (admin/admin123)
# Prometheus: http://localhost:9090
# Alertmanager: http://localhost:9093
```

## Configuration

### Prometheus
- Configuration file: `prometheus/prometheus.yml`
- Alert rules: `prometheus/alert_rules.yml`
- Data retention: 30 days

### Grafana
- Dashboards: `grafana/dashboards/`
- Datasources: `grafana/datasources/`
- Default login: admin/admin123 (change in production)

### Alertmanager
- Configuration: `alertmanager/alertmanager.yml`
- Configure email/Slack/webhook notifications

## Metrics Collected

### RXinDexer Specific
- Sync lag and block height
- API request rate and latency
- RPC call metrics
- Database connection pool
- Backfill progress
- Token and UTXO counts

### System
- CPU, memory, disk usage
- Network I/O
- PostgreSQL performance

## Alerting

The following alerts are configured:

### Critical
- Service down
- Sync lag > 50,000 blocks
- Database connection pool exhausted

### Warning
- High API error rate (>10%)
- High API latency (>5s)
- Sync lag > 1,000 blocks
- High memory/disk/CPU usage (>90%)
- Slow database queries
- Backfill stalled

## Dashboard

The included Grafana dashboard provides:
- Real-time sync status
- API performance metrics
- Database health
- System resource usage
- Token statistics

## Customization

1. Add new metrics in `config/metrics.py`
2. Update alert rules in `prometheus/alert_rules.yml`
3. Modify dashboards in Grafana UI or JSON files
4. Configure notification channels in Alertmanager

## Production Considerations

1. Change default passwords
2. Configure persistent storage
3. Set up backup for Prometheus data
4. Configure external notification services
5. Set up log aggregation
6. Consider high availability for critical components
