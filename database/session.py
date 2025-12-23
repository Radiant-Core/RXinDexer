from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from contextlib import contextmanager
import os

POSTGRES_DB = os.getenv("POSTGRES_DB", "rxindexer")
POSTGRES_USER = os.getenv("POSTGRES_USER", "rxindexer")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "rxindexerpass")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "db")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

DB_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

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


@contextmanager
def get_session():
    """Context manager for API/general use - auto-closes session."""
    session = SessionLocal()
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
