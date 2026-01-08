from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from contextlib import contextmanager, asynccontextmanager
import os
import logging

logger = logging.getLogger(__name__)

POSTGRES_DB = os.getenv("POSTGRES_DB", "rxindexer")
POSTGRES_USER = os.getenv("POSTGRES_USER", "rxindexer")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "dsUEZPX1mqwPhRlicEGbjhERjioXqgdcvoEKCZMkwLc=")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "db")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

# Primary database URL (read-write)
DB_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# Async database URL (uses asyncpg driver)
ASYNC_DB_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# Read replica URL (optional - falls back to primary if not set)
READ_REPLICA_URL = os.getenv("READ_REPLICA_URL")
ASYNC_READ_REPLICA_URL = os.getenv("READ_REPLICA_URL")
if ASYNC_READ_REPLICA_URL:
    # Convert to asyncpg URL format
    ASYNC_READ_REPLICA_URL = ASYNC_READ_REPLICA_URL.replace("postgresql://", "postgresql+asyncpg://")

# Production-ready connection pool settings
# - pool_size: Number of persistent connections to keep
# - max_overflow: Additional connections allowed when pool is exhausted
# - pool_timeout: Seconds to wait for a connection before giving up
# - pool_recycle: Recycle connections after N seconds (prevents stale connections)
# - pool_pre_ping: Test connections before use (handles dropped connections)
engine = create_engine(
    DB_URL,
    poolclass=QueuePool,
    pool_size=20,           # Base pool size for concurrent requests
    max_overflow=30,        # Allow up to 50 total connections (20 + 30)
    pool_timeout=10,        # Wait max 10s for connection (fail fast)
    pool_recycle=1800,      # Recycle connections every 30 minutes
    pool_pre_ping=True,     # Verify connection is alive before using
    echo=False,             # Set True for SQL debugging
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Separate engine for indexer (heavier workloads, different pool settings)
indexer_engine = create_engine(
    DB_URL,
    poolclass=QueuePool,
    pool_size=5,            # Indexer needs fewer but longer-held connections
    max_overflow=5,
    pool_timeout=60,        # Indexer can wait longer
    pool_recycle=3600,      # Recycle every hour
    pool_pre_ping=True,
    echo=False,
)

IndexerSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=indexer_engine)

# Read replica engine (optional - for horizontal read scaling)
# Falls back to primary engine if READ_REPLICA_URL is not configured
if READ_REPLICA_URL:
    try:
        read_engine = create_engine(
            READ_REPLICA_URL,
            poolclass=QueuePool,
            pool_size=15,           # Slightly smaller pool for read replica
            max_overflow=25,
            pool_timeout=10,
            pool_recycle=1800,
            pool_pre_ping=True,
            echo=False,
        )
        ReadSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=read_engine)
        logger.info("Read replica configured successfully")
        _read_replica_available = True
    except Exception as e:
        logger.warning(f"Failed to configure read replica: {e}, falling back to primary")
        read_engine = engine
        ReadSessionLocal = SessionLocal
        _read_replica_available = False
else:
    read_engine = engine
    ReadSessionLocal = SessionLocal
    _read_replica_available = False


# =============================================================================
# ASYNC DATABASE SUPPORT (for API - non-blocking I/O)
# =============================================================================

# Async engine for API (non-blocking I/O, 2-3x throughput improvement)
async_engine = create_async_engine(
    ASYNC_DB_URL,
    pool_size=20,
    max_overflow=30,
    pool_timeout=10,
    pool_recycle=1800,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Async read replica engine (optional)
if ASYNC_READ_REPLICA_URL:
    try:
        async_read_engine = create_async_engine(
            ASYNC_READ_REPLICA_URL,
            pool_size=15,
            max_overflow=25,
            pool_timeout=10,
            pool_recycle=1800,
            echo=False,
        )
        AsyncReadSessionLocal = async_sessionmaker(
            async_read_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
        _async_read_replica_available = True
        logger.info("Async read replica configured successfully")
    except Exception as e:
        logger.warning(f"Failed to configure async read replica: {e}, falling back to primary")
        async_read_engine = async_engine
        AsyncReadSessionLocal = AsyncSessionLocal
        _async_read_replica_available = False
else:
    async_read_engine = async_engine
    AsyncReadSessionLocal = AsyncSessionLocal
    _async_read_replica_available = False


def is_read_replica_configured() -> bool:
    """Check if a read replica is configured and available."""
    return _read_replica_available


@contextmanager
def get_session():
    """Context manager for API/general use - auto-closes session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def get_read_session():
    """
    Context manager for read-only queries.
    Uses read replica if configured, otherwise falls back to primary.
    
    Use this for:
    - List endpoints (GET /tokens, GET /blocks/recent, etc.)
    - Search queries
    - Analytics and reporting
    - Any read-only operation that doesn't require immediate consistency
    """
    session = ReadSessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def get_indexer_session():
    """Context manager for indexer use - separate pool."""
    session = IndexerSessionLocal()
    try:
        yield session
    finally:
        session.close()


# =============================================================================
# ASYNC CONTEXT MANAGERS (for FastAPI async endpoints)
# =============================================================================

@asynccontextmanager
async def get_async_session():
    """
    Async context manager for API use - non-blocking I/O.
    
    Use this for async endpoints to achieve 2-3x throughput improvement.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_async_read_session():
    """
    Async context manager for read-only queries.
    Uses async read replica if configured, otherwise falls back to primary.
    """
    async with AsyncReadSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_async_db():
    """
    FastAPI dependency for async database sessions.
    
    Usage:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_async_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_pool_stats(pool_name: str = "api") -> dict:
    """
    Get connection pool statistics.
    
    Args:
        pool_name: 'api' for API engine, 'indexer' for indexer engine, 'read' for read replica
    
    Returns:
        dict with pool_size, checkedout, overflow, checkedin
    """
    if pool_name == "read":
        target_engine = read_engine
    elif pool_name == "indexer":
        target_engine = indexer_engine
    else:
        target_engine = engine
    
    pool = target_engine.pool
    
    return {
        "pool_name": pool_name,
        "pool_size": pool.size(),
        "checkedout": pool.checkedout(),
        "overflow": pool.overflow(),
        "checkedin": pool.checkedin(),
        "max_overflow": target_engine.pool._max_overflow,
        "is_replica": pool_name == "read" and _read_replica_available,
    }


def collect_pool_metrics():
    """
    Collect pool metrics for both API and indexer engines.
    Call this periodically to update Prometheus metrics.
    """
    try:
        from config.metrics import update_db_pool_metrics
        
        # API pool stats
        api_stats = get_pool_stats("api")
        update_db_pool_metrics(
            active=api_stats["checkedout"],
            pool_size=api_stats["pool_size"],
            overflow=api_stats["overflow"],
            checkedout=api_stats["checkedout"]
        )
    except ImportError:
        pass  # Metrics not available
