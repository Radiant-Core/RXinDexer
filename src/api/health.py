# /Users/radiant/Desktop/RXinDexer/src/api/health.py
# This file provides health check endpoints for the API
# It is responsible for providing system health status and does not interact with blockchain data

from fastapi import APIRouter
import psutil
import time

# Create a router for health check endpoints
health_router = APIRouter(
    prefix="/health",
    tags=["health"],
    responses={
        404: {"description": "Not found"},
        500: {"description": "Internal server error"}
    }
)

@health_router.get("/")
async def health_check():
    """
    Basic health check endpoint
    
    Returns a simple response to confirm the API is running.
    """
    return {
        "status": "healthy",
        "timestamp": time.time()
    }

@health_router.get("/system")
async def system_health():
    """
    Detailed system health check
    
    Returns information about system resources usage.
    """
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "system": {
            "cpu_usage": psutil.cpu_percent(),
            "memory_usage": psutil.virtual_memory().percent,
            "disk_usage": psutil.disk_usage('/').percent
        }
    }
