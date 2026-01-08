# Prometheus metrics collection for RXinDexer
# Provides observability into sync progress, API response times, and database performance

import os
import time
import threading
from typing import Optional, Dict, Any
from functools import wraps

# Check if prometheus_client is available
try:
    from prometheus_client import (
        Counter, Gauge, Histogram, Summary, Info,
        CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST,
        multiprocess, REGISTRY
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

# Configuration
METRICS_ENABLED = os.getenv("METRICS_ENABLED", "1").lower() in ("1", "true", "yes")
METRICS_PREFIX = os.getenv("METRICS_PREFIX", "rxindexer")


class MetricsRegistry:
    """
    Centralized metrics registry for RXinDexer.
    All metrics are collected here for easy access and export.
    """
    
    def __init__(self):
        self._enabled = METRICS_ENABLED and PROMETHEUS_AVAILABLE
        self._registry = REGISTRY if self._enabled else None
        self._metrics: Dict[str, Any] = {}
        
        if self._enabled:
            self._init_metrics()
    
    def _init_metrics(self):
        """Initialize all application metrics."""
        prefix = METRICS_PREFIX
        
        # ============== SYNC METRICS ==============
        self._metrics['sync_lag'] = Gauge(
            f'{prefix}_sync_lag_blocks',
            'Number of blocks behind the node',
            registry=self._registry
        )
        
        self._metrics['db_height'] = Gauge(
            f'{prefix}_db_height_blocks',
            'Current indexed block height in database',
            registry=self._registry
        )
        
        self._metrics['node_height'] = Gauge(
            f'{prefix}_node_height_blocks',
            'Current block height on radiant-node',
            registry=self._registry
        )
        
        self._metrics['sync_duration'] = Histogram(
            f'{prefix}_sync_batch_duration_seconds',
            'Time taken to sync a batch of blocks',
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
            registry=self._registry
        )
        
        self._metrics['blocks_synced'] = Counter(
            f'{prefix}_blocks_synced_total',
            'Total number of blocks synced',
            registry=self._registry
        )
        
        # ============== API METRICS ==============
        self._metrics['api_requests'] = Counter(
            f'{prefix}_api_requests_total',
            'Total API requests',
            ['method', 'endpoint', 'status'],
            registry=self._registry
        )
        
        self._metrics['api_latency'] = Histogram(
            f'{prefix}_api_latency_seconds',
            'API request latency',
            ['method', 'endpoint'],
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=self._registry
        )
        
        self._metrics['api_errors'] = Counter(
            f'{prefix}_api_errors_total',
            'Total API errors',
            ['method', 'endpoint', 'error_type'],
            registry=self._registry
        )
        
        # ============== RPC METRICS ==============
        self._metrics['rpc_calls'] = Counter(
            f'{prefix}_rpc_calls_total',
            'Total RPC calls to radiant-node',
            ['method', 'status'],
            registry=self._registry
        )
        
        self._metrics['rpc_latency'] = Histogram(
            f'{prefix}_rpc_latency_seconds',
            'RPC call latency',
            ['method'],
            buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
            registry=self._registry
        )
        
        self._metrics['rpc_retries'] = Counter(
            f'{prefix}_rpc_retries_total',
            'Total RPC retry attempts',
            ['method'],
            registry=self._registry
        )
        
        # ============== DATABASE METRICS ==============
        self._metrics['db_query_duration'] = Histogram(
            f'{prefix}_db_query_duration_seconds',
            'Database query duration',
            ['query_type'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=self._registry
        )
        
        self._metrics['db_slow_queries'] = Counter(
            f'{prefix}_db_slow_queries_total',
            'Number of slow queries (>1s)',
            ['query_type'],
            registry=self._registry
        )
        
        self._metrics['db_connections_active'] = Gauge(
            f'{prefix}_db_connections_active',
            'Number of active database connections',
            registry=self._registry
        )
        
        self._metrics['db_pool_size'] = Gauge(
            f'{prefix}_db_pool_size',
            'Database connection pool size',
            registry=self._registry
        )
        
        self._metrics['db_pool_overflow'] = Gauge(
            f'{prefix}_db_pool_overflow',
            'Number of overflow connections in use',
            registry=self._registry
        )
        
        self._metrics['db_pool_checkedout'] = Gauge(
            f'{prefix}_db_pool_checkedout',
            'Number of connections currently checked out from pool',
            registry=self._registry
        )
        
        # ============== CACHE METRICS ==============
        self._metrics['cache_hits'] = Counter(
            f'{prefix}_cache_hits_total',
            'Total cache hits',
            ['cache_type'],
            registry=self._registry
        )
        
        self._metrics['cache_misses'] = Counter(
            f'{prefix}_cache_misses_total',
            'Total cache misses',
            ['cache_type'],
            registry=self._registry
        )
        
        self._metrics['cache_hit_ratio'] = Gauge(
            f'{prefix}_cache_hit_ratio',
            'Cache hit ratio (0-1)',
            ['cache_type'],
            registry=self._registry
        )
        
        self._metrics['cache_size'] = Gauge(
            f'{prefix}_cache_size_keys',
            'Number of keys in cache',
            ['cache_type'],
            registry=self._registry
        )
        
        # ============== BACKFILL METRICS ==============
        self._metrics['backfill_progress'] = Gauge(
            f'{prefix}_backfill_progress_percent',
            'Backfill progress percentage',
            ['backfill_type'],
            registry=self._registry
        )
        
        self._metrics['backfill_items_processed'] = Counter(
            f'{prefix}_backfill_items_processed_total',
            'Total items processed in backfill',
            ['backfill_type'],
            registry=self._registry
        )
        
        self._metrics['backfill_eta_seconds'] = Gauge(
            f'{prefix}_backfill_eta_seconds',
            'Estimated time to completion for backfill',
            ['backfill_type'],
            registry=self._registry
        )
        
        # ============== TOKEN METRICS ==============
        self._metrics['tokens_total'] = Gauge(
            f'{prefix}_tokens_total',
            'Total number of tokens indexed',
            registry=self._registry
        )
        
        self._metrics['utxos_total'] = Gauge(
            f'{prefix}_utxos_total',
            'Total number of UTXOs',
            registry=self._registry
        )
        
        self._metrics['utxos_unspent'] = Gauge(
            f'{prefix}_utxos_unspent',
            'Number of unspent UTXOs',
            registry=self._registry
        )
        
        # ============== SYSTEM METRICS ==============
        self._metrics['uptime'] = Gauge(
            f'{prefix}_uptime_seconds',
            'Service uptime in seconds',
            ['service'],
            registry=self._registry
        )
        
        self._metrics['alerts_total'] = Counter(
            f'{prefix}_alerts_total',
            'Total alerts raised',
            ['level'],
            registry=self._registry
        )
        
        # ============== INFO METRIC ==============
        self._metrics['info'] = Info(
            f'{prefix}_build',
            'Build information',
            registry=self._registry
        )
        self._metrics['info'].info({
            'version': os.getenv('APP_VERSION', '1.0.0'),
            'component': os.getenv('APP_COMPONENT', 'rxindexer')
        })
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    def get(self, name: str) -> Optional[Any]:
        """Get a metric by name."""
        if not self._enabled:
            return None
        return self._metrics.get(name)
    
    def export(self) -> bytes:
        """Export all metrics in Prometheus format."""
        if not self._enabled:
            return b"# Metrics disabled\n"
        return generate_latest(self._registry)
    
    def content_type(self) -> str:
        """Get the content type for metrics export."""
        if not self._enabled:
            return "text/plain"
        return CONTENT_TYPE_LATEST


# Global metrics registry instance
metrics = MetricsRegistry()


# ============== HELPER FUNCTIONS ==============

def record_sync_metrics(db_height: int, node_height: int, sync_duration: float = None):
    """Record sync-related metrics."""
    if not metrics.enabled:
        return
    
    metrics.get('db_height').set(db_height)
    metrics.get('node_height').set(node_height)
    metrics.get('sync_lag').set(max(0, node_height - db_height))
    
    if sync_duration is not None:
        metrics.get('sync_duration').observe(sync_duration)


def record_blocks_synced(count: int):
    """Record number of blocks synced."""
    if metrics.enabled:
        metrics.get('blocks_synced').inc(count)


def record_api_request(method: str, endpoint: str, status: int, duration: float):
    """Record API request metrics."""
    if not metrics.enabled:
        return
    
    metrics.get('api_requests').labels(method=method, endpoint=endpoint, status=str(status)).inc()
    metrics.get('api_latency').labels(method=method, endpoint=endpoint).observe(duration)


def record_api_error(method: str, endpoint: str, error_type: str):
    """Record API error."""
    if metrics.enabled:
        metrics.get('api_errors').labels(method=method, endpoint=endpoint, error_type=error_type).inc()


def record_rpc_call(method: str, status: str, duration: float, retries: int = 0):
    """Record RPC call metrics."""
    if not metrics.enabled:
        return
    
    metrics.get('rpc_calls').labels(method=method, status=status).inc()
    metrics.get('rpc_latency').labels(method=method).observe(duration)
    
    if retries > 0:
        metrics.get('rpc_retries').labels(method=method).inc(retries)


def record_db_query(query_type: str, duration: float):
    """Record database query metrics."""
    if not metrics.enabled:
        return
    
    metrics.get('db_query_duration').labels(query_type=query_type).observe(duration)
    
    if duration > 1.0:  # Slow query threshold
        metrics.get('db_slow_queries').labels(query_type=query_type).inc()


def record_backfill_progress(backfill_type: str, progress_pct: float, items_processed: int = 0, eta_seconds: float = None):
    """Record backfill progress metrics."""
    if not metrics.enabled:
        return
    
    metrics.get('backfill_progress').labels(backfill_type=backfill_type).set(progress_pct)
    
    if items_processed > 0:
        metrics.get('backfill_items_processed').labels(backfill_type=backfill_type).inc(items_processed)
    
    if eta_seconds is not None:
        metrics.get('backfill_eta_seconds').labels(backfill_type=backfill_type).set(eta_seconds)


def record_alert(level: str):
    """Record alert metric."""
    if metrics.enabled:
        metrics.get('alerts_total').labels(level=level).inc()


def update_db_pool_metrics(active: int, pool_size: int, overflow: int = 0, checkedout: int = 0):
    """Update database connection pool metrics."""
    if not metrics.enabled:
        return
    
    metrics.get('db_connections_active').set(active)
    metrics.get('db_pool_size').set(pool_size)
    metrics.get('db_pool_overflow').set(overflow)
    metrics.get('db_pool_checkedout').set(checkedout)


def record_cache_hit(cache_type: str = "api"):
    """Record a cache hit."""
    if metrics.enabled:
        metrics.get('cache_hits').labels(cache_type=cache_type).inc()


def record_cache_miss(cache_type: str = "api"):
    """Record a cache miss."""
    if metrics.enabled:
        metrics.get('cache_misses').labels(cache_type=cache_type).inc()


def update_cache_metrics(cache_type: str, hits: int, misses: int, size: int = 0):
    """Update cache metrics including hit ratio."""
    if not metrics.enabled:
        return
    
    total = hits + misses
    if total > 0:
        ratio = hits / total
        metrics.get('cache_hit_ratio').labels(cache_type=cache_type).set(ratio)
    
    metrics.get('cache_size').labels(cache_type=cache_type).set(size)


def update_token_metrics(total_tokens: int, total_utxos: int, unspent_utxos: int):
    """Update token-related metrics."""
    if not metrics.enabled:
        return
    
    metrics.get('tokens_total').set(total_tokens)
    metrics.get('utxos_total').set(total_utxos)
    metrics.get('utxos_unspent').set(unspent_utxos)


def track_time(metric_name: str, labels: Dict[str, str] = None):
    """
    Decorator to track execution time of a function.
    
    Usage:
        @track_time('db_query_duration', {'query_type': 'select'})
        def my_query():
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                return func(*args, **kwargs)
            finally:
                duration = time.time() - start_time
                if metrics.enabled:
                    metric = metrics.get(metric_name)
                    if metric:
                        if labels:
                            metric.labels(**labels).observe(duration)
                        else:
                            metric.observe(duration)
        return wrapper
    return decorator


class Timer:
    """
    Context manager for timing operations.
    
    Usage:
        with Timer() as t:
            do_something()
        print(f"Took {t.elapsed:.2f}s")
    """
    
    def __init__(self):
        self.start_time = None
        self.elapsed = 0.0
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, *args):
        self.elapsed = time.time() - self.start_time
