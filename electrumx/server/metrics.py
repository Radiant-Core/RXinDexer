"""
Prometheus Metrics Endpoint for RXinDexer

Provides real-time monitoring metrics for the ElectrumX server including:
- Session statistics
- Database performance
- Glyph/WAVE/Swap indexing stats
- Mempool metrics
- Block processing performance
"""

import time
from typing import Dict, Any, Optional
from collections import defaultdict

from electrumx.lib import util


class MetricsCollector:
    """
    Collects and exposes Prometheus-compatible metrics.
    
    Metrics are exposed in Prometheus text format at /metrics endpoint.
    """
    
    def __init__(self, env=None):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.env = env
        self.enabled = getattr(env, 'prometheus_enabled', True) if env else True
        self.port = getattr(env, 'prometheus_port', 9100) if env else 9100
        
        # Counters (monotonically increasing)
        self.counters: Dict[str, int] = defaultdict(int)
        
        # Gauges (can go up or down)
        self.gauges: Dict[str, float] = defaultdict(float)
        
        # Histograms (for latency measurements)
        self.histograms: Dict[str, list] = defaultdict(list)
        
        # Labels for metrics
        self.labels: Dict[str, Dict[str, str]] = {}
        
        # Start time for uptime calculation
        self.start_time = time.time()
        
        if self.enabled:
            self.logger.info(f'Prometheus metrics enabled on port {self.port}')
    
    # ========================================================================
    # Counter Methods
    # ========================================================================
    
    def inc_counter(self, name: str, value: int = 1, labels: Dict[str, str] = None):
        """Increment a counter metric."""
        key = self._make_key(name, labels)
        self.counters[key] += value
        if labels:
            self.labels[key] = labels
    
    def get_counter(self, name: str, labels: Dict[str, str] = None) -> int:
        """Get current counter value."""
        key = self._make_key(name, labels)
        return self.counters.get(key, 0)
    
    # ========================================================================
    # Gauge Methods
    # ========================================================================
    
    def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Set a gauge metric value."""
        key = self._make_key(name, labels)
        self.gauges[key] = value
        if labels:
            self.labels[key] = labels
    
    def inc_gauge(self, name: str, value: float = 1, labels: Dict[str, str] = None):
        """Increment a gauge metric."""
        key = self._make_key(name, labels)
        self.gauges[key] += value
        if labels:
            self.labels[key] = labels
    
    def dec_gauge(self, name: str, value: float = 1, labels: Dict[str, str] = None):
        """Decrement a gauge metric."""
        key = self._make_key(name, labels)
        self.gauges[key] -= value
        if labels:
            self.labels[key] = labels
    
    def get_gauge(self, name: str, labels: Dict[str, str] = None) -> float:
        """Get current gauge value."""
        key = self._make_key(name, labels)
        return self.gauges.get(key, 0.0)
    
    # ========================================================================
    # Histogram Methods
    # ========================================================================
    
    def observe_histogram(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record a histogram observation."""
        key = self._make_key(name, labels)
        self.histograms[key].append(value)
        # Keep only last 1000 observations
        if len(self.histograms[key]) > 1000:
            self.histograms[key] = self.histograms[key][-1000:]
        if labels:
            self.labels[key] = labels
    
    # ========================================================================
    # Metric Export
    # ========================================================================
    
    def generate_metrics(self) -> str:
        """Generate Prometheus text format metrics."""
        lines = []
        
        # Add uptime gauge
        uptime = time.time() - self.start_time
        lines.append('# HELP rxindexer_uptime_seconds Server uptime in seconds')
        lines.append('# TYPE rxindexer_uptime_seconds gauge')
        lines.append(f'rxindexer_uptime_seconds {uptime:.2f}')
        lines.append('')
        
        # Export counters
        for key, value in sorted(self.counters.items()):
            name, label_str = self._parse_key(key)
            if not any(l.startswith(f'# HELP {name}') for l in lines):
                lines.append(f'# HELP {name} Counter metric')
                lines.append(f'# TYPE {name} counter')
            lines.append(f'{name}{label_str} {value}')
        
        if self.counters:
            lines.append('')
        
        # Export gauges
        for key, value in sorted(self.gauges.items()):
            name, label_str = self._parse_key(key)
            if not any(l.startswith(f'# HELP {name}') for l in lines):
                lines.append(f'# HELP {name} Gauge metric')
                lines.append(f'# TYPE {name} gauge')
            lines.append(f'{name}{label_str} {value:.6f}')
        
        if self.gauges:
            lines.append('')
        
        # Export histogram summaries
        for key, values in sorted(self.histograms.items()):
            if not values:
                continue
            name, label_str = self._parse_key(key)
            if not any(l.startswith(f'# HELP {name}') for l in lines):
                lines.append(f'# HELP {name} Histogram metric')
                lines.append(f'# TYPE {name} summary')
            
            count = len(values)
            total = sum(values)
            avg = total / count if count > 0 else 0
            sorted_vals = sorted(values)
            p50 = sorted_vals[int(count * 0.5)] if count > 0 else 0
            p90 = sorted_vals[int(count * 0.9)] if count > 0 else 0
            p99 = sorted_vals[int(count * 0.99)] if count > 0 else 0
            
            lines.append(f'{name}_count{label_str} {count}')
            lines.append(f'{name}_sum{label_str} {total:.6f}')
            if label_str:
                lines.append(f'{name}{{quantile="0.5",{label_str[1:]} {p50:.6f}')
                lines.append(f'{name}{{quantile="0.9",{label_str[1:]} {p90:.6f}')
                lines.append(f'{name}{{quantile="0.99",{label_str[1:]} {p99:.6f}')
            else:
                lines.append(f'{name}{{quantile="0.5"}} {p50:.6f}')
                lines.append(f'{name}{{quantile="0.9"}} {p90:.6f}')
                lines.append(f'{name}{{quantile="0.99"}} {p99:.6f}')
        
        return '\n'.join(lines)
    
    def _make_key(self, name: str, labels: Dict[str, str] = None) -> str:
        """Create a unique key for a metric with labels."""
        if not labels:
            return name
        label_parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return f"{name}{{{','.join(label_parts)}}}"
    
    def _parse_key(self, key: str) -> tuple:
        """Parse a key back into name and label string."""
        if '{' in key:
            name = key[:key.index('{')]
            label_str = key[key.index('{'):]
            return name, label_str
        return key, ''


# Predefined metric names for consistency
class MetricNames:
    """Standard metric names for RXinDexer."""
    
    # Session metrics
    SESSIONS_TOTAL = 'rxindexer_sessions_total'
    SESSIONS_ACTIVE = 'rxindexer_sessions_active'
    REQUESTS_TOTAL = 'rxindexer_requests_total'
    REQUESTS_ERRORS = 'rxindexer_requests_errors_total'
    REQUEST_DURATION = 'rxindexer_request_duration_seconds'
    
    # Block processing metrics
    BLOCKS_PROCESSED = 'rxindexer_blocks_processed_total'
    BLOCK_PROCESSING_TIME = 'rxindexer_block_processing_seconds'
    BLOCK_HEIGHT = 'rxindexer_block_height'
    
    # Database metrics
    DB_SIZE_BYTES = 'rxindexer_db_size_bytes'
    DB_READ_OPS = 'rxindexer_db_read_ops_total'
    DB_WRITE_OPS = 'rxindexer_db_write_ops_total'
    
    # Glyph indexing metrics
    GLYPH_TOKENS_INDEXED = 'rxindexer_glyph_tokens_indexed_total'
    GLYPH_TRANSFERS_INDEXED = 'rxindexer_glyph_transfers_indexed_total'
    GLYPH_CACHE_SIZE = 'rxindexer_glyph_cache_size'
    GLYPH_CACHE_HITS = 'rxindexer_glyph_cache_hits_total'
    GLYPH_CACHE_MISSES = 'rxindexer_glyph_cache_misses_total'
    
    # WAVE indexing metrics
    WAVE_NAMES_INDEXED = 'rxindexer_wave_names_indexed_total'
    WAVE_RESOLUTIONS = 'rxindexer_wave_resolutions_total'
    WAVE_RESOLUTION_TIME = 'rxindexer_wave_resolution_seconds'
    
    # Swap indexing metrics
    SWAP_ORDERS_INDEXED = 'rxindexer_swap_orders_indexed_total'
    SWAP_ORDERS_ACTIVE = 'rxindexer_swap_orders_active'
    SWAP_FILLS_INDEXED = 'rxindexer_swap_fills_indexed_total'
    
    # Mempool metrics
    MEMPOOL_TXS = 'rxindexer_mempool_txs'
    MEMPOOL_SIZE_BYTES = 'rxindexer_mempool_size_bytes'
    
    # Subscription metrics
    SUBSCRIPTIONS_ACTIVE = 'rxindexer_subscriptions_active'
    SUBSCRIPTION_NOTIFICATIONS = 'rxindexer_subscription_notifications_total'


# Global metrics instance
_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


def init_metrics(env) -> MetricsCollector:
    """Initialize the global metrics collector with environment."""
    global _metrics
    _metrics = MetricsCollector(env)
    return _metrics


async def metrics_handler(request):
    """
    HTTP handler for /metrics endpoint.
    
    Use with aiohttp:
        app.router.add_get('/metrics', metrics_handler)
    """
    from aiohttp import web
    metrics = get_metrics()
    content = metrics.generate_metrics()
    return web.Response(text=content, content_type='text/plain; charset=utf-8')
