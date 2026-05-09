"""
Prometheus Metrics for RXinDexer (R18/R19/R20)

Exposes a prometheus_client registry with all required counters, gauges,
and histograms. Importable by rest_api.py (/metrics endpoint) and
block_processor.py (timing + parse-error instrumentation).
"""

try:
    from prometheus_client import (
        Counter, Gauge, Histogram, CollectorRegistry, generate_latest,
        CONTENT_TYPE_LATEST,
    )
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False

# ── Registry ──────────────────────────────────────────────────────────────────
# Use a dedicated registry so we don't accidentally include default
# process/platform metrics that may conflict with the host's collector.
registry = CollectorRegistry() if HAS_PROMETHEUS else None

# ── Helpers ───────────────────────────────────────────────────────────────────
def _noop(*a, **kw):
    """Stub used when prometheus_client is not installed."""
    class _Stub:
        def labels(self, **kw): return self
        def inc(self, v=1): pass
        def set(self, v): pass
        def observe(self, v): pass
        def time(self): return _NullCtx()
    return _Stub()

class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *a): pass

def _counter(name, doc, labels=()):
    if not HAS_PROMETHEUS:
        return _noop()
    return Counter(name, doc, list(labels), registry=registry)

def _gauge(name, doc, labels=()):
    if not HAS_PROMETHEUS:
        return _noop()
    return Gauge(name, doc, list(labels), registry=registry)

def _histogram(name, doc, labels=(), buckets=None):
    if not HAS_PROMETHEUS:
        return _noop()
    kw = {'registry': registry, 'labelnames': list(labels)}
    if buckets:
        kw['buckets'] = buckets
    return Histogram(name, doc, **kw)

# ── Metrics ───────────────────────────────────────────────────────────────────

# R18: block processing
blocks_processed = _counter(
    'rxindexer_blocks_processed_total',
    'Total number of blocks processed by the indexer',
)
block_processing_seconds = _histogram(
    'rxindexer_block_processing_seconds',
    'Time spent processing a single block (advance_txs + flush)',
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
flush_seconds = _histogram(
    'rxindexer_flush_seconds',
    'Time spent writing a flush batch to the DB',
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)
flush_total = _counter('rxindexer_flush_total', 'Total number of DB flushes')
block_height = _gauge('rxindexer_block_height', 'Current indexed block height')
reorg_total = _counter('rxindexer_reorg_total', 'Total number of chain reorgs handled')

# R18: token counts
tokens_total = _gauge(
    'rxindexer_tokens_total',
    'Number of indexed Glyph tokens by type',
    labels=['type'],
)

# R18: REST API
rest_requests_total = _counter(
    'rxindexer_rest_requests_total',
    'Total REST API requests',
    labels=['method', 'endpoint', 'status'],
)
rest_request_seconds = _histogram(
    'rxindexer_rest_request_seconds',
    'REST API request latency',
    labels=['endpoint'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)

# R18: cache sizes
cache_size = _gauge(
    'rxindexer_cache_size',
    'Current size of internal caches',
    labels=['cache'],
)

# R18: swap
swap_orders_total = _gauge(
    'rxindexer_swap_orders_total',
    'Number of swap orders by status',
    labels=['status'],
)

# R20: parse errors
glyph_parse_errors_total = _counter(
    'rxindexer_glyph_parse_errors_total',
    'Total Glyph envelope / CBOR parse errors',
)
swap_parse_errors_total = _counter(
    'rxindexer_swap_parse_errors_total',
    'Total RSWP payload parse errors',
)
wave_parse_errors_total = _counter(
    'rxindexer_wave_parse_errors_total',
    'Total WAVE record parse errors',
)


def generate_metrics_text() -> bytes:
    """Return current metrics in Prometheus text format."""
    if not HAS_PROMETHEUS:
        return b'# prometheus_client not installed\n'
    return generate_latest(registry)


METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST if HAS_PROMETHEUS else 'text/plain; charset=utf-8'
