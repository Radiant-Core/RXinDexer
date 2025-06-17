# /Users/radiant/Desktop/RXinDexer/src/api/status.py
# This file implements API endpoints for system status information.
# It provides information about synchronization status and system health.

import logging
from fastapi import APIRouter, Request
from fastapi.responses import Response
import time
import json

# Create router
router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("")
async def get_system_status():
    """
    Get the current system status
    """
    try:
        data = {
            "status": "ok",
            "message": "API is running",
            "timestamp": time.time()
        }
        content = json.dumps(data).encode("utf-8")
        return Response(
            content=content,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Error getting system status: {str(e)}")
        error_content = json.dumps({"error": str(e)}).encode("utf-8")
        return Response(
            content=error_content,
            status_code=500,
            media_type="application/json"
        )

@router.get("/sync")
async def get_sync_status():
    """
    Get the current blockchain sync status.
    """
    try:
        data = {
            "status": "ok",
            "message": "Sync status endpoint is working",
            "timestamp": time.time()
        }
        content = json.dumps(data).encode("utf-8")
        return Response(
            content=content,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Error getting sync status: {str(e)}", exc_info=True)
        error_content = json.dumps({"error": str(e)}).encode("utf-8")
        return Response(
            content=error_content,
            status_code=500,
            media_type="application/json"
        )
