# /Users/radiant/Desktop/RXinDexer/src/api/status.py
# This file implements API endpoints for system status information.
# It provides information about synchronization status, system health, and overall statistics.

import logging
import time
import psutil
import os
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

# Import security module for API key authentication
from src.api.security import get_api_key

from src.models import get_db
from src.sync.rpc_selector import RadiantRPC

# Create router with NO API key dependency
router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("")
async def get_system_status(
    db: Session = Depends(get_db)
):
    """
    Get overall system status information
    
    Returns:
        System status including database, blockchain, and memory metrics
    """
    try:
        # Get memory usage of the API container
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        
        # Get database statistics
        db_stats = db.execute(text("SELECT pg_database_size(current_database()) as db_size"))
        db_size = db_stats.fetchone()[0]
        
        # Get sync state
        sync_state = db.execute(text("""
            SELECT current_height, current_hash, is_syncing, last_updated_at, current_chainwork
            FROM sync_state 
            WHERE id = 1
        """)).fetchone()
        
        # Get tables count
        tables_result = db.execute(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        ))
        tables_count = tables_result.fetchone()[0]
        
        # Get sync status
        is_syncing = bool(sync_state[2]) if sync_state and sync_state[2] is not None else False
        current_height = sync_state[0] if sync_state and sync_state[0] is not None else 0
        current_hash = sync_state[1] if sync_state and sync_state[1] is not None else ""
        last_updated = sync_state[3].timestamp() if sync_state and sync_state[3] is not None else 0
        chainwork = sync_state[4] if sync_state and len(sync_state) > 4 and sync_state[4] is not None else ""
        
        # Check database connectivity
        db_healthy = True
        try:
            db.execute(text("SELECT 1"))
        except Exception as e:
            logger.error(f"Database connection test failed: {str(e)}")
            db_healthy = False
        
        # Check RPC connectivity if needed
        rpc_healthy = False
        network_info = {}
        blockchain_info = {}
        try:
            rpc = RadiantRPC()
            network_info = rpc.client.getnetworkinfo()
            blockchain_info = rpc.client.getblockchaininfo()
            rpc_healthy = True
        except Exception as e:
            logger.warning(f"RPC connection failed: {str(e)}")
            
        # Create system status response
        status_data = {
            "api": {
                "status": "online",
                "version": "1.0.0",
                "uptime_seconds": time.time() - process.create_time(),
                "memory_usage_mb": memory_info.rss / (1024 * 1024)
            },
            "database": {
                "status": "online" if db_healthy else "offline",
                "size_mb": round(db_size / (1024 * 1024), 2),
                "tables": tables_count,
                "rows": current_height if current_height > 0 else 0,
                "note": "Row count is an estimate based on blockchain height"
            },
            "blockchain": {
                "status": "syncing" if is_syncing else "synced",
                "height": current_height,
                "hash": current_hash,
                "sync_progress": 100.0 if not is_syncing else 0.0,
                "last_updated": last_updated,
                "chainwork": chainwork,
                "rpc_status": "connected" if rpc_healthy else "disconnected"
            },
            "memory": {
                "used_mb": memory_info.rss / (1024 * 1024),
                "available_mb": psutil.virtual_memory().available / (1024 * 1024)
            },
            "timestamp": time.time()
        }
        
        # If RPC is connected, enhance the blockchain status with RPC info
        if rpc_healthy:
            status_data["blockchain"].update({
                "rpc_version": network_info.get("version", "unknown"),
                "rpc_blocks": blockchain_info.get("blocks", "unknown"),
                "rpc_verification_progress": round(blockchain_info.get("verificationprogress", 0) * 100, 2)
            })
            
            # Calculate sync progress if we have both current height and RPC height
            if current_height > 0 and 'blocks' in blockchain_info and blockchain_info['blocks'] > 0:
                progress = min(1.0, current_height / blockchain_info['blocks'])
                status_data["blockchain"]["sync_progress"] = round(progress * 100, 2)
        
        return status_data
    except Exception as e:
        logger.error(f"Error getting system status: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving system status")

@router.get("/sync")
async def get_sync_status(
    db: Session = Depends(get_db)
):
    """
    Get detailed blockchain synchronization status
    
    Returns:
        Sync status including current height, target height, and sync percentage
    """
    try:
        # Get sync state from database
        sync_result = db.execute(text("""
            SELECT current_height, current_hash, is_syncing, last_updated_at, 
                   last_error, glyph_scan_height
            FROM sync_state 
            WHERE id = 1
        """))
        sync_data = sync_result.fetchone()
        
        # Get blockchain info from RPC if available
        current_height = 0
        target_height = 0
        verification_progress = 0
        rpc_healthy = False
        
        try:
            rpc = RadiantRPC()
            blockchain_info = rpc.client.getblockchaininfo()
            target_height = blockchain_info.get("blocks", 0)
            verification_progress = blockchain_info.get("verificationprogress", 0) * 100
            rpc_healthy = True
        except Exception as e:
            logger.warning(f"RPC connection failed: {str(e)}")
            # If RPC fails, try to get height from sync_state
            if sync_data and sync_data[0] is not None:
                current_height = sync_data[0]
                target_height = current_height + 1  # Just show as synced if we can't get target
        
        # If we have sync_data, use it for current height
        if sync_data and sync_data[0] is not None:
            current_height = sync_data[0]
        
        # Calculate sync percentage if we have both heights
        sync_percentage = 0
        if target_height > 0:
            sync_percentage = min(100.0, (current_height / target_height * 100))
        
        # Get indexer status from sync_state
        is_syncing = bool(sync_data[2]) if sync_data and sync_data[2] is not None else False
        
        # Create sync status response
        sync_status = {
            "current_height": current_height,
            "target_height": target_height if rpc_healthy else None,
            "sync_percentage": round(sync_percentage, 2) if rpc_healthy else None,
            "is_syncing": is_syncing,
            "last_updated": sync_data[3].timestamp() if sync_data and sync_data[3] else None,
            "last_error": sync_data[4] if sync_data and sync_data[4] else None,
            "glyph_scan_height": sync_data[5] if sync_data and sync_data[5] is not None else 0,
            "blockchain_verification_progress": round(verification_progress, 2) if rpc_healthy else None,
            "rpc_connected": rpc_healthy,
            "timestamp": time.time()
        }
        
        return sync_status
    except Exception as e:
        logger.error(f"Error getting sync status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Error retrieving sync status: {str(e)}"
        )
