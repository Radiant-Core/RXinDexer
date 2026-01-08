from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
import psutil
import time
import os
import logging
from typing import Dict, Any

from api.dependencies import get_db
from api.utils import rpc_call, check_node_connection
from api.cache import cache
from api.endpoints.wallets import cached_rxd_holder_count
from api.endpoints.tokens import cached_token_holder_count
from database.session import engine

# Try to import metrics (optional)
try:
    from config.metrics import metrics, update_db_pool_metrics
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False

# Try to import alert manager (optional)
try:
    from config.logging_config import alert_manager, AlertLevel
    ALERTS_AVAILABLE = True
except ImportError:
    ALERTS_AVAILABLE = False

logger = logging.getLogger("rxindexer.health")

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


@router.get("/health/services")
def inter_service_health(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Check health of all inter-service communications.
    Tests connectivity to radiant-node, database, and internal services.
    """
    services_status = {
        "timestamp": int(time.time()),
        "overall_status": "healthy",
        "services": {}
    }
    
    issues = []
    
    # 1. Database connectivity
    try:
        start = time.time()
        db.execute(text("SELECT 1"))
        latency = (time.time() - start) * 1000
        services_status["services"]["database"] = {
            "status": "healthy",
            "latency_ms": round(latency, 2)
        }
    except Exception as e:
        services_status["services"]["database"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        issues.append(f"Database: {e}")
    
    # 2. Radiant Node connectivity
    node_status = check_node_connection()
    if node_status["connected"]:
        services_status["services"]["radiant_node"] = {
            "status": "healthy",
            "latency_ms": node_status["latency_ms"],
            "block_height": node_status["block_height"]
        }
    else:
        services_status["services"]["radiant_node"] = {
            "status": "unhealthy",
            "error": node_status.get("error", "Connection failed")
        }
        issues.append(f"Radiant Node: {node_status.get('error', 'unreachable')}")
    
    # 3. Check sync status
    try:
        if services_status["services"].get("radiant_node", {}).get("status") == "healthy":
            node_height = node_status["block_height"]
            db_height = db.execute(text("SELECT COALESCE(MAX(height), 0) FROM blocks")).scalar()
            sync_lag = node_height - db_height
            
            sync_status = "healthy" if sync_lag < 100 else "behind" if sync_lag < 1000 else "critical"
            services_status["services"]["sync"] = {
                "status": sync_status,
                "node_height": node_height,
                "db_height": db_height,
                "lag": sync_lag
            }
            
            if sync_lag > 1000:
                issues.append(f"Sync lag critical: {sync_lag} blocks")
    except Exception as e:
        services_status["services"]["sync"] = {
            "status": "unknown",
            "error": str(e)
        }
    
    # 4. Check backfill status
    try:
        backfill_result = db.execute(text("""
            SELECT backfill_type, is_complete, 
                   EXTRACT(EPOCH FROM (NOW() - updated_at)) as seconds_since_update
            FROM backfill_status
        """))
        backfills = {}
        for row in backfill_result:
            backfill_type, is_complete, seconds_since = row
            status = "complete" if is_complete else "in_progress"
            if not is_complete and seconds_since and seconds_since > 3600:
                status = "stalled"
                issues.append(f"Backfill {backfill_type} may be stalled")
            backfills[backfill_type] = {
                "status": status,
                "is_complete": is_complete,
                "seconds_since_update": round(seconds_since) if seconds_since else None
            }
        services_status["services"]["backfills"] = backfills
    except Exception as e:
        services_status["services"]["backfills"] = {
            "status": "unknown",
            "error": str(e)
        }
    
    # Determine overall status
    if issues:
        services_status["overall_status"] = "degraded"
        services_status["issues"] = issues
        
        # Raise alerts for critical issues
        if ALERTS_AVAILABLE:
            for issue in issues:
                if "critical" in issue.lower() or "unhealthy" in str(services_status["services"]).lower():
                    alert_manager.alert(AlertLevel.WARNING, f"Service health issue: {issue}")
    
    return services_status


@router.get("/metrics")
def prometheus_metrics():
    """Export Prometheus metrics."""
    if not METRICS_AVAILABLE:
        return {"error": "Metrics not available. Install prometheus_client."}
    
    from fastapi.responses import Response
    return Response(
        content=metrics.export(),
        media_type=metrics.content_type()
    )


@router.get("/health/alerts")
def get_recent_alerts():
    """Get recent system alerts."""
    if not ALERTS_AVAILABLE:
        return {"alerts": [], "message": "Alert system not configured"}
    
    return {
        "alerts": alert_manager.get_recent_alerts(20),
        "count": len(alert_manager.alerts)
    }
