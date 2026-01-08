# Database query logging and slow query detection for RXinDexer
# Provides instrumentation for database queries to identify performance issues

import os
import time
import logging
from functools import wraps
from contextlib import contextmanager
from typing import Optional, Any, Callable

logger = logging.getLogger("rxindexer.db.queries")

# Configuration
SLOW_QUERY_THRESHOLD_MS = float(os.getenv("SLOW_QUERY_THRESHOLD_MS", "1000"))  # 1 second default
QUERY_LOGGING_ENABLED = os.getenv("QUERY_LOGGING_ENABLED", "1").lower() in ("1", "true", "yes")
LOG_ALL_QUERIES = os.getenv("LOG_ALL_QUERIES", "0").lower() in ("1", "true", "yes")

# Try to import metrics
try:
    from config.metrics import record_db_query
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False
    def record_db_query(*args, **kwargs): pass

# Try to import alerts
try:
    from config.logging_config import alert_manager, AlertLevel
    ALERTS_AVAILABLE = True
except ImportError:
    ALERTS_AVAILABLE = False


class QueryStats:
    """Track query statistics."""
    
    def __init__(self):
        self.total_queries = 0
        self.total_time_ms = 0.0
        self.slow_queries = 0
        self.queries_by_type = {}
    
    def record(self, query_type: str, duration_ms: float, is_slow: bool):
        self.total_queries += 1
        self.total_time_ms += duration_ms
        if is_slow:
            self.slow_queries += 1
        
        if query_type not in self.queries_by_type:
            self.queries_by_type[query_type] = {"count": 0, "total_ms": 0.0, "slow": 0}
        
        self.queries_by_type[query_type]["count"] += 1
        self.queries_by_type[query_type]["total_ms"] += duration_ms
        if is_slow:
            self.queries_by_type[query_type]["slow"] += 1
    
    def get_stats(self) -> dict:
        return {
            "total_queries": self.total_queries,
            "total_time_ms": round(self.total_time_ms, 2),
            "slow_queries": self.slow_queries,
            "avg_query_ms": round(self.total_time_ms / self.total_queries, 2) if self.total_queries > 0 else 0,
            "by_type": self.queries_by_type
        }
    
    def reset(self):
        self.total_queries = 0
        self.total_time_ms = 0.0
        self.slow_queries = 0
        self.queries_by_type = {}


# Global query stats instance
query_stats = QueryStats()


def classify_query(sql: str) -> str:
    """Classify a SQL query by type."""
    sql_upper = sql.strip().upper()
    
    if sql_upper.startswith("SELECT"):
        return "select"
    elif sql_upper.startswith("INSERT"):
        return "insert"
    elif sql_upper.startswith("UPDATE"):
        return "update"
    elif sql_upper.startswith("DELETE"):
        return "delete"
    elif sql_upper.startswith("CREATE"):
        return "create"
    elif sql_upper.startswith("DROP"):
        return "drop"
    elif sql_upper.startswith("ALTER"):
        return "alter"
    elif sql_upper.startswith("WITH"):
        # CTE - look for the main operation
        if "INSERT" in sql_upper:
            return "insert_cte"
        elif "UPDATE" in sql_upper:
            return "update_cte"
        elif "DELETE" in sql_upper:
            return "delete_cte"
        return "select_cte"
    elif sql_upper.startswith("TRUNCATE"):
        return "truncate"
    elif sql_upper.startswith("COPY"):
        return "copy"
    else:
        return "other"


def truncate_sql(sql: str, max_length: int = 200) -> str:
    """Truncate SQL for logging."""
    sql_clean = ' '.join(sql.split())  # Normalize whitespace
    if len(sql_clean) > max_length:
        return sql_clean[:max_length] + "..."
    return sql_clean


@contextmanager
def timed_query(query_type: str = "unknown", sql: str = None):
    """
    Context manager to time database queries.
    
    Usage:
        with timed_query("select", "SELECT * FROM users") as timer:
            result = db.execute(...)
        print(f"Query took {timer.elapsed_ms}ms")
    """
    start_time = time.time()
    
    class Timer:
        elapsed_ms = 0.0
        is_slow = False
    
    timer = Timer()
    
    try:
        yield timer
    finally:
        timer.elapsed_ms = (time.time() - start_time) * 1000
        timer.is_slow = timer.elapsed_ms > SLOW_QUERY_THRESHOLD_MS
        
        if QUERY_LOGGING_ENABLED:
            # Record stats
            query_stats.record(query_type, timer.elapsed_ms, timer.is_slow)
            
            # Record metrics
            if METRICS_AVAILABLE:
                record_db_query(query_type, timer.elapsed_ms / 1000)
            
            # Log slow queries
            if timer.is_slow:
                sql_preview = truncate_sql(sql) if sql else "N/A"
                logger.warning(
                    f"SLOW QUERY ({timer.elapsed_ms:.0f}ms > {SLOW_QUERY_THRESHOLD_MS}ms) "
                    f"[{query_type}]: {sql_preview}"
                )
                
                # Alert on very slow queries
                if ALERTS_AVAILABLE and timer.elapsed_ms > SLOW_QUERY_THRESHOLD_MS * 5:
                    alert_manager.alert(
                        AlertLevel.WARNING,
                        f"Very slow query detected: {timer.elapsed_ms:.0f}ms",
                        {"query_type": query_type, "sql_preview": sql_preview}
                    )
            
            # Log all queries if enabled
            elif LOG_ALL_QUERIES:
                sql_preview = truncate_sql(sql) if sql else "N/A"
                logger.debug(f"Query ({timer.elapsed_ms:.1f}ms) [{query_type}]: {sql_preview}")


def log_query(func: Callable) -> Callable:
    """
    Decorator to log database query execution time.
    
    Usage:
        @log_query
        def my_query(db):
            return db.execute(text("SELECT ..."))
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        query_type = kwargs.get('query_type', 'unknown')
        
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            elapsed_ms = (time.time() - start_time) * 1000
            is_slow = elapsed_ms > SLOW_QUERY_THRESHOLD_MS
            
            if QUERY_LOGGING_ENABLED:
                query_stats.record(query_type, elapsed_ms, is_slow)
                
                if is_slow:
                    logger.warning(f"SLOW QUERY ({elapsed_ms:.0f}ms) in {func.__name__}")
    
    return wrapper


def get_query_stats() -> dict:
    """Get current query statistics."""
    return query_stats.get_stats()


def reset_query_stats():
    """Reset query statistics."""
    query_stats.reset()


# SQLAlchemy event listener for automatic query logging
def setup_query_logging(engine):
    """
    Set up automatic query logging for a SQLAlchemy engine.
    
    Usage:
        from database.query_logger import setup_query_logging
        setup_query_logging(engine)
    """
    from sqlalchemy import event
    
    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault('query_start_time', []).append(time.time())
        conn.info.setdefault('query_statement', []).append(statement)
    
    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        start_times = conn.info.get('query_start_time', [])
        statements = conn.info.get('query_statement', [])
        
        if start_times:
            start_time = start_times.pop()
            elapsed_ms = (time.time() - start_time) * 1000
            
            if statements:
                sql = statements.pop()
                query_type = classify_query(sql)
                is_slow = elapsed_ms > SLOW_QUERY_THRESHOLD_MS
                
                if QUERY_LOGGING_ENABLED:
                    query_stats.record(query_type, elapsed_ms, is_slow)
                    
                    if METRICS_AVAILABLE:
                        record_db_query(query_type, elapsed_ms / 1000)
                    
                    if is_slow:
                        sql_preview = truncate_sql(sql)
                        logger.warning(
                            f"SLOW QUERY ({elapsed_ms:.0f}ms) [{query_type}]: {sql_preview}"
                        )
