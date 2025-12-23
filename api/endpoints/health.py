from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
import psutil
import time
import os

from api.dependencies import get_db
from api.utils import rpc_call
from api.cache import cache
from api.endpoints.wallets import cached_rxd_holder_count
from api.endpoints.tokens import cached_token_holder_count
from database.session import engine

router = APIRouter()

@router.get("/health")
def basic_health():
    """Basic health check - returns OK if API is running"""
    return {"status": "healthy", "service": "rxindexer-api"}

@router.get("/health/detailed")
def detailed_health_check(db: Session = Depends(get_db)):
    """Comprehensive health check for production monitoring"""

    # Cache detailed health briefly to avoid hammering DB/RPC and to keep the
    # explorer homepage responsive even if the node is slow.
    cache_key = "health:detailed"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    health_status = {
        "status": "healthy",
        "timestamp": int(time.time()),
        "service": "rxindexer-api",
        "version": "1.0.0"
    }
    
    # Database health check
    try:
        db.execute(text("SELECT 1"))
        # Test a simple query
        latest_block = db.execute(text("SELECT MAX(height) FROM blocks")).scalar()
        health_status["database"] = {
            "status": "healthy",
            "latest_block": latest_block or 0,
            "connection": "active"
        }
    except Exception as e:
        health_status["database"] = {
            "status": "unhealthy",
            "error": str(e),
            "connection": "failed"
        }
        health_status["status"] = "degraded"
    
    # Sync status check
    try:
        sync_info = rpc_call("getblockcount")
        db_height = health_status["database"].get("latest_block", 0)
        sync_lag = sync_info - db_height if sync_info and db_height else 0

        network_hash_rate = None
        difficulty = None
        try:
            network_hash_rate = rpc_call("getnetworkhashps")
        except Exception:
            try:
                difficulty = rpc_call("getdifficulty")
                network_hash_rate = (float(difficulty) * (2 ** 32)) / 300.0
            except Exception:
                network_hash_rate = None

        health_status["sync"] = {
            "blockchain_height": sync_info,
            "indexed_height": db_height,
            "sync_lag": sync_lag,
            "network_hash_rate": network_hash_rate,
            "status": "critical" if sync_lag > 50000 else "healthy" if sync_lag < 100 else "behind"
        }
        
        if sync_lag > 50000:
            health_status["status"] = "degraded"
            
    except Exception as e:
        health_status["sync"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # System resource monitoring
    try:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        health_status["system"] = {
            "memory_usage_percent": memory.percent,
            "memory_available_gb": round(memory.available / (1024**3), 2),
            "disk_usage_percent": disk.percent,
            "disk_free_gb": round(disk.free / (1024**3), 2),
            "cpu_count": psutil.cpu_count(),
            "load_average": os.getloadavg()[0] if hasattr(os, 'getloadavg') else None
        }
        
        # Warning thresholds
        if memory.percent > 90 or disk.percent > 90:
            health_status["status"] = "degraded"
            
    except Exception as e:
        health_status["system"] = {
            "status": "unavailable",
            "error": str(e)
        }
    
    # Cache status
    cache.cleanup()  # Clean expired entries
    health_status["cache"] = {
        "type": "in-memory-ttl",
        "status": "active",
        "entries": len(cache._cache)
    }
    
    # Connection pool status
    try:
        pool = engine.pool
        health_status["connection_pool"] = {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "invalid": pool.invalidatedcount() if hasattr(pool, 'invalidatedcount') else 0
        }
    except Exception as e:
        health_status["connection_pool"] = {
            "status": "unavailable",
            "error": str(e)
        }

    cache.set(cache_key, health_status, 30)  # Cache health for 30 seconds
    return health_status

@router.get("/db-health")
def db_health(db: Session = Depends(get_db)):
    """Dedicated database health check endpoint"""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unhealthy: {str(e)}")

@router.get("/health/db")
def health_db(db: Session = Depends(get_db)):
    """Database health check endpoint (alternative path for monitoring)"""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unhealthy: {str(e)}")

@router.get("/status")
def status():
    try:
        info = rpc_call("getblockchaininfo")
        return {"status": "ok", "blockchain": info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/admin/cache/clear", summary="Clear all analytics endpoint caches", tags=["Admin"])
def clear_cache():
    """
    Clears all analytics endpoint caches. POST only. Returns 405 for GET requests.
    """
    # Clear TTL cache
    cache.clear()
    # Clear lru_cache decorators
    cached_rxd_holder_count.cache_clear()
    cached_token_holder_count.cache_clear()
    return {"status": "all caches cleared", "ttl_cache": "cleared", "lru_cache": "cleared"}
