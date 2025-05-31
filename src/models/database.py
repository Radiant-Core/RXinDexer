# /Users/radiant/Desktop/RXinDexer/src/models/database.py
# This file handles database connection and session management.
# It provides the SQLAlchemy engine and session factory for the application.

import os
import sys
import json
import time
import socket
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, Any, Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, TypeDecorator, TEXT, event, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session, Session
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, OperationalError, DisconnectionError
import logging

# Custom JSON type for SQLite compatibility
class JSONType(TypeDecorator):
    """Represents a JSON structure stored as text in SQLite and JSONB in PostgreSQL"""
    impl = TEXT
    cache_ok = True
    
    def process_bind_param(self, value, dialect):
        if value is None:
            return '{}'
        if dialect.name == 'sqlite':
            return json.dumps(value)
        return value
    
    def process_result_value(self, value, dialect):
        if value is None:
            return {}
        if dialect.name == 'sqlite':
            return json.loads(value)
        return value

# Custom ARRAY type for SQLite compatibility
class ArrayType(TypeDecorator):
    """Represents an array stored as JSON in SQLite and as ARRAY in PostgreSQL"""
    impl = TEXT
    cache_ok = True
    
    def __init__(self, item_type):
        super(ArrayType, self).__init__()
        self.item_type = item_type
    
    def process_bind_param(self, value, dialect):
        if value is None:
            return '[]'
        if dialect.name == 'sqlite':
            return json.dumps(value)
        return value
    
    def process_result_value(self, value, dialect):
        if value is None:
            return []
        if dialect.name == 'sqlite':
            return json.loads(value)
        return value

# Ensure environment variables are loaded
load_dotenv()

# Configure logger
logger = logging.getLogger(__name__)

# Add project root to path if needed
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Database connection configuration with robust environment detection
def get_database_url() -> str:
    """Determine the database URL based on environment variables with fallbacks."""
    # First try to get a complete DATABASE_URL from environment
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        logger.info(f"Using database URL from environment: {database_url}")
        return database_url
    
    # Determine database type
    db_type = os.getenv("DB_TYPE", "sqlite").lower()
    
    # Configure based on database type
    if db_type == "postgresql":
        # PostgreSQL configuration
        db_user = os.getenv("DB_USER", "postgres")
        db_password = os.getenv("DB_PASSWORD", "postgres")
        
        # Smart environment detection
        in_docker = os.getenv("IN_DOCKER", "false").lower() == "true"
        
        # Try to auto-detect Docker environment if not explicitly set
        if os.getenv("IN_DOCKER") is None:
            # Check for .dockerenv file which exists in Docker containers
            if os.path.exists("/.dockerenv"):
                in_docker = True
                logger.info("Auto-detected Docker environment")
        
        # Determine appropriate host
        if in_docker:
            # In Docker, use service name as host
            db_host = os.getenv("DB_HOST", "db")
        else:
            # For local development, use localhost
            db_host = os.getenv("DB_HOST", "localhost")
        
        # Check if DB host is reachable
        if not in_docker:
            try:
                # Check if we can connect to the host
                db_port_int = int(os.getenv("DB_PORT", "5432"))
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)  # 1 second timeout
                result = s.connect_ex((db_host, db_port_int))
                s.close()
                
                if result != 0:
                    logger.warning(f"Database host {db_host}:{db_port_int} is not reachable. Falling back to SQLite.")
                    # Fall back to SQLite if PostgreSQL is not reachable
                    return get_sqlite_url()
            except Exception as e:
                logger.warning(f"Error checking database host: {e}. Proceeding anyway.")
        
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "rxindexer")
        
        # Construct PostgreSQL URL
        return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    else:
        # Default to SQLite for development/testing
        return get_sqlite_url()

def get_sqlite_url() -> str:
    """Get a SQLite database URL with a valid path."""
    # Determine appropriate SQLite path
    db_path = os.getenv("DB_PATH")
    
    if not db_path:
        # Default path in project directory
        db_path = os.path.join(str(project_root), "rxindexer.db")
        
        # Ensure directory exists and is writable
        db_dir = os.path.dirname(db_path)
        try:
            os.makedirs(db_dir, exist_ok=True)
        except (IOError, OSError) as e:
            logger.warning(f"Could not create directory for SQLite database: {e}")
            # Fall back to current directory
            db_path = "rxindexer.db"
    
    logger.info(f"Using SQLite database at: {db_path}")
    return f"sqlite:///{db_path}"

# Determine the database URL
DATABASE_URL = get_database_url()
logger.info(f"Final database connection URL: {DATABASE_URL}")


# Create SQLAlchemy engine with robust connection handling
MAX_RETRIES = int(os.getenv("DB_CONNECT_RETRIES", "3"))
RETRY_DELAY = int(os.getenv("DB_CONNECT_RETRY_DELAY", "2"))

def create_db_engine(url: str) -> Engine:
    """Create a database engine with appropriate settings and error handling."""
    for attempt in range(MAX_RETRIES):
        try:
            if url.startswith('sqlite'):
                # SQLite configuration
                return create_engine(
                    url,
                    connect_args={'check_same_thread': False},  # Allow multi-threading for SQLite
                    pool_pre_ping=True,
                    pool_timeout=30,
                    echo=os.getenv("SQL_ECHO", "false").lower() == "true"
                )
            else:
                # PostgreSQL configuration with optimized connection pool
                return create_engine(
                    url,
                    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
                    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
                    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
                    pool_pre_ping=True,
                    pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "300")),
                    connect_args={
                        "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
                        "application_name": "RXinDexer"
                    },
                    echo=os.getenv("SQL_ECHO", "false").lower() == "true"
                )
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                logger.warning(f"Database connection attempt {attempt+1} failed: {e}. Retrying in {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)
            else:
                logger.error(f"All database connection attempts failed: {e}")
                # For PostgreSQL failures, try to fall back to SQLite
                if not url.startswith('sqlite'):
                    logger.warning("Falling back to SQLite database")
                    return create_db_engine(get_sqlite_url())
                raise
    
    # This should never be reached due to the exception in the last iteration
    raise RuntimeError("Failed to create database engine after retries")

# Create the engine
try:
    engine = create_db_engine(DATABASE_URL)
    logger.info("Database engine created successfully")
except Exception as e:
    logger.critical(f"Failed to create database engine: {e}")
    # Continue with a memory-only SQLite as last resort to allow API to start
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={'check_same_thread': False},
        echo=True
    )
    logger.warning("Using in-memory SQLite database as fallback - DATA WILL NOT BE PERSISTED")

# Create session factory with connection pooling
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create thread-safe scoped session
db_session = scoped_session(SessionLocal)

# Declarative base for ORM models
Base = declarative_base()

# Provide a convenient property for table names
Base.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}

@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Context manager for database sessions.
    Handles session creation, exception handling, and cleanup.
    
    Example:
        with get_db_context() as db:
            db.query(Model).all()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Database session error: {e}")
        raise
    finally:
        db.close()

def get_db() -> Generator[Session, None, None]:
    """Dependency function to get database session.
    Used in FastAPI endpoints to provide a session for each request.
    
    Example:
        @app.get("/items/")
        def read_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError as e:
        logger.error(f"Database error in endpoint: {str(e)}")
        raise
    finally:
        db.close()

# Set up query interception to catch problematic queries before execution
@event.listens_for(Engine, "before_cursor_execute")
def intercept_problematic_queries(conn, cursor, statement, params, context, executemany):
    """
    Intercepts SQL queries before execution to identify and modify problematic queries.
    Specifically targets the problematic JOIN between utxos and glyph_tokens tables.
    
    Args:
        conn: Connection
        cursor: Cursor
        statement: SQL statement
        params: Query parameters
        context: Execution context
        executemany: Whether this is an executemany operation
    """
    # Check if this is the problematic JOIN query
    if ("utxos JOIN glyph_tokens ON utxos.token_ref = glyph_tokens.ref" in statement and 
        "WHERE utxos.spent = false AND utxos.token_ref IS NOT NULL" in statement):
        
        logger.warning("Intercepted problematic JOIN query, replacing with safer version")
        
        # Replace with a safer query that avoids direct JOINs
        safer_statement = """
        WITH unspent_tokens AS (
            SELECT address, token_ref
            FROM utxos
            WHERE spent = false AND token_ref IS NOT NULL
        )
        SELECT ut.address, ut.token_ref
        FROM unspent_tokens ut
        WHERE EXISTS (
            SELECT 1 FROM glyph_tokens gt WHERE gt.ref = ut.token_ref
        )
        """
        
        # Update the statement with our safer version
        context.statement = safer_statement

def init_db() -> bool:
    """Initialize database by creating all tables.
    Returns True if successful, False otherwise.
    
    Only use this for development or testing.
    In production, use Alembic migrations.
    """
    try:
        logger.info("Initializing database tables")
        Base.metadata.create_all(bind=engine)
        
        # Initialize sync state if needed
        with get_db_context() as db:
            # Check if sync_state table is empty
            from src.models.sync_state import SyncState
            if db.query(SyncState).count() == 0:
                logger.info("Creating initial sync state record")
                sync_state = SyncState()
                sync_state.last_block_height = 0
                sync_state.is_syncing = False
                sync_state.last_updated_at = time.time()
                db.add(sync_state)
        
        logger.info("Database initialization complete")
        logger.info("Query interceptor activated to prevent problematic JOINs")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False
        
# Test database connection on module load
try:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    logger.info("Database connection test successful")
except Exception as e:
    logger.error(f"Database connection test failed: {e}")
    # Don't raise - let the application handle connection issues gracefully
