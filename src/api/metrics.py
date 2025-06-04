# /Users/radiant/Desktop/RXinDexer/src/api/metrics.py
# This file implements the metrics endpoint for monitoring and observability.
# It provides system metrics, API usage statistics, and performance data.

import logging
import time
import psutil
import os
import platform
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text, func, desc

# Import security module for API key authentication
from src.api.security import get_api_key

from src.models import get_db, Block, Transaction
from src.sync.rpc_selector import RadiantRPC

# Create router with NO API key dependency
router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("")
async def get_metrics(
    db: Session = Depends(get_db)
):
    """
    Get system metrics and statistics for monitoring
    
    Returns:
        Detailed metrics about system performance, database, and API usage
    """
    try:
        start_time = time.time()
        
        # System metrics
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        cpu_percent = process.cpu_percent(interval=0.1)
        
        # Database metrics
        try:
            # Get block count
            block_count = db.query(func.count(Block.id)).scalar()
            
            # Get transaction count
            tx_count = db.query(func.count(Transaction.id)).scalar()
            
            # Get database size
            db_stats = db.execute(text("SELECT pg_database_size(current_database()) as db_size"))
            db_size = db_stats.fetchone()[0]
            
            # Get table statistics
            table_stats = db.execute(text("""
                SELECT 
                    relname as table_name, 
                    n_live_tup as row_count,
                    pg_total_relation_size(relid) as total_size
                FROM 
                    pg_stat_user_tables 
                ORDER BY 
                    n_live_tup DESC
            """))
            table_data = []
            for row in table_stats:
                table_data.append({
                    "name": row.table_name,
                    "rows": row.row_count,
                    "size_mb": row.total_size / (1024 * 1024)
                })
                
            # Database connectivity latency
            db_start = time.time()
            db.execute(text("SELECT 1"))
            db_latency_ms = (time.time() - db_start) * 1000
            
            db_metrics = {
                "block_count": block_count,
                "transaction_count": tx_count,
                "database_size_mb": db_size / (1024 * 1024),
                "tables": table_data,
                "connection_latency_ms": round(db_latency_ms, 2),
                "status": "healthy"
            }
        except Exception as db_err:
            logger.error(f"Error collecting database metrics: {str(db_err)}")
            db_metrics = {
                "status": "error",
                "error": str(db_err)
            }
        
        # RPC metrics
        try:
            rpc = RadiantRPC()
            rpc_start = time.time()
            blockchain_info = rpc.client.getblockchaininfo()
            rpc_latency_ms = (time.time() - rpc_start) * 1000
            
            rpc_metrics = {
                "blockchain_height": blockchain_info.get("blocks", 0),
                "connection_latency_ms": round(rpc_latency_ms, 2),
                "verification_progress": round(blockchain_info.get("verificationprogress", 0) * 100, 2),
                "status": "connected"
            }
        except Exception as rpc_err:
            logger.error(f"Error collecting RPC metrics: {str(rpc_err)}")
            rpc_metrics = {
                "status": "error",
                "error": str(rpc_err)
            }
        
        # Redis metrics
        try:
            # Import redis client inside try block to handle case where redis isn't available
            from redis import Redis
            import os
            
            redis_host = os.environ.get("REDIS_HOST", "redis")
            redis_port = int(os.environ.get("REDIS_PORT", 6379))
            
            redis_client = Redis(host=redis_host, port=redis_port, decode_responses=True)
            redis_start = time.time()
            redis_info = redis_client.info()
            redis_latency_ms = (time.time() - redis_start) * 1000
            
            redis_metrics = {
                "used_memory_mb": int(redis_info.get("used_memory", 0)) / (1024 * 1024),
                "clients_connected": redis_info.get("connected_clients", 0),
                "uptime_days": redis_info.get("uptime_in_days", 0),
                "connection_latency_ms": round(redis_latency_ms, 2),
                "status": "connected"
            }
        except Exception as redis_err:
            logger.error(f"Error collecting Redis metrics: {str(redis_err)}")
            redis_metrics = {
                "status": "error",
                "error": str(redis_err)
            }
        
        # Calculate response time for this API call
        response_time_ms = (time.time() - start_time) * 1000
        
        # Compile all metrics
        metrics = {
            "system": {
                "hostname": platform.node(),
                "cpu_usage_percent": cpu_percent,
                "memory_usage_mb": memory_info.rss / (1024 * 1024),
                "memory_percent": process.memory_percent(),
                "disk_usage_percent": psutil.disk_usage('/').percent,
                "uptime_seconds": time.time() - process.create_time()
            },
            "database": db_metrics,
            "radiant_node": rpc_metrics,
            "redis": redis_metrics,
            "api": {
                "response_time_ms": round(response_time_ms, 2),
                "version": "1.0.0",
                "environment": os.environ.get("ENVIRONMENT", "development")
            },
            "timestamp": time.time()
        }
        
        return metrics
    except Exception as e:
        logger.error(f"Error collecting metrics: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving system metrics")
