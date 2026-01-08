"""
ARQ worker configuration and utilities.

This module configures the ARQ worker for background task processing.
"""

import os
import logging
from typing import Optional
from arq import create_pool as arq_create_pool
from arq.connections import RedisSettings, ArqRedis

logger = logging.getLogger("rxindexer.tasks")

# Redis URL from environment (same as caching)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Parse Redis URL for ARQ settings
def get_redis_settings() -> RedisSettings:
    """Parse REDIS_URL into ARQ RedisSettings."""
    url = REDIS_URL
    
    # Parse redis://host:port/db format
    if url.startswith("redis://"):
        url = url[8:]
    
    # Split host:port/db
    if "/" in url:
        host_port, db = url.rsplit("/", 1)
        db = int(db) if db else 0
    else:
        host_port = url
        db = 0
    
    if ":" in host_port:
        host, port = host_port.split(":")
        port = int(port)
    else:
        host = host_port
        port = 6379
    
    # Use different DB for task queue (db + 1) to separate from cache
    return RedisSettings(host=host, port=port, database=db + 1)


# Global pool reference
_pool: Optional[ArqRedis] = None


async def create_pool() -> ArqRedis:
    """Create or return existing ARQ Redis connection pool."""
    global _pool
    if _pool is None:
        try:
            _pool = await arq_create_pool(get_redis_settings())
            logger.info("ARQ task queue pool created")
        except Exception as e:
            logger.warning(f"Failed to create ARQ pool: {e}. Tasks will run synchronously.")
            _pool = None
    return _pool


async def close_pool():
    """Close the ARQ Redis connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("ARQ task queue pool closed")


async def enqueue_task(task_name: str, *args, _defer_by: int = 0, **kwargs) -> Optional[str]:
    """
    Enqueue a task for background processing.
    
    Args:
        task_name: Name of the task function to run
        *args: Positional arguments for the task
        _defer_by: Delay in seconds before running the task
        **kwargs: Keyword arguments for the task
    
    Returns:
        Job ID if enqueued successfully, None if queue unavailable
    """
    pool = await create_pool()
    if pool is None:
        logger.debug(f"Task queue unavailable, skipping task: {task_name}")
        return None
    
    try:
        from datetime import timedelta
        job = await pool.enqueue_job(
            task_name,
            *args,
            _defer_by=timedelta(seconds=_defer_by) if _defer_by else None,
            **kwargs
        )
        logger.debug(f"Enqueued task {task_name} with job_id={job.job_id}")
        return job.job_id
    except Exception as e:
        logger.warning(f"Failed to enqueue task {task_name}: {e}")
        return None


async def get_queue_stats() -> dict:
    """Get task queue statistics."""
    pool = await create_pool()
    if pool is None:
        return {"status": "unavailable", "message": "Redis not connected"}
    
    try:
        info = await pool.info()
        return {
            "status": "connected",
            "redis_version": info.get("redis_version"),
            "connected_clients": info.get("connected_clients"),
            "used_memory_human": info.get("used_memory_human"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


class WorkerSettings:
    """
    ARQ worker settings.
    
    To run the worker:
        arq api.tasks.worker.WorkerSettings
    """
    
    redis_settings = get_redis_settings()
    
    # Import task functions
    functions = [
        "api.tasks.jobs.update_holder_counts",
        "api.tasks.jobs.refresh_token_metadata",
        "api.tasks.jobs.refresh_balances",
        "api.tasks.jobs.send_webhook",
        "api.tasks.jobs.cleanup_old_data",
    ]
    
    # Worker configuration
    max_jobs = 10
    job_timeout = 300  # 5 minutes max per job
    keep_result = 3600  # Keep results for 1 hour
    poll_delay = 0.5
    
    # Retry configuration
    max_tries = 3
    retry_delay = 60  # 1 minute between retries
    
    # Logging
    @staticmethod
    async def on_startup(ctx):
        """Called when worker starts."""
        logger.info("ARQ worker started")
        
        # Initialize database session
        from database.session import AsyncSessionLocal
        ctx["db_session_factory"] = AsyncSessionLocal
    
    @staticmethod
    async def on_shutdown(ctx):
        """Called when worker shuts down."""
        logger.info("ARQ worker shutting down")
