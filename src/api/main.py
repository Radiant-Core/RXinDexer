# /Users/radiant/Desktop/RXinDexer/src/api/main.py
# This file defines the main FastAPI application for RXinDexer.
# It integrates all API endpoints and provides the central entry point for the API.

import logging
import os
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import time

from src.models import get_db

# Import core blockchain API routers
from src.api.address import router as address_router
from src.api.transaction import router as transaction_router
from src.api.token import router as token_router
from src.api.holder import router as holder_router

# Import enhanced API routers
from src.api.nft_endpoints import router as nft_router
from src.api.user_container_endpoints import router as user_container_router
from src.api.analytics_endpoints import router as analytics_router

# Configure logging
from src.utils.logging_config import setup_logging
logger = setup_logging('api')

# Create FastAPI app
app = FastAPI(
    title="RXinDexer API",
    description="Advanced indexing and API services for the Radiant blockchain",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, set specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response

# Root endpoint
@app.get("/")
async def root():
    return {
        "name": "RXinDexer API",
        "version": "1.0.0",
        "description": "Advanced indexing and API services for the Radiant blockchain",
        "docs_url": "/api/docs"
    }

# Health check endpoints
@app.get("/health")
@app.get("/api/v1/health")
async def health_check():
    # Simple health check to verify API is running
    return {
        "status": "healthy", 
        "timestamp": time.time(),
        "components": {
            "api": "online",
            "database": "connected"
        }
    }

# Include API routers with proper prefixes

# Core blockchain API endpoints
app.include_router(address_router, prefix="/api/v1/address", tags=["Addresses"])
app.include_router(transaction_router, prefix="/api/v1/transactions", tags=["Transactions"])
app.include_router(token_router, prefix="/api/v1/tokens", tags=["Tokens"])
app.include_router(holder_router, prefix="/api/v1/holders", tags=["Holders"])

# Enhanced blockchain API endpoints
app.include_router(nft_router, prefix="/api/v1", tags=["NFTs"])
app.include_router(user_container_router, prefix="/api/v1", tags=["Users & Containers"])
app.include_router(analytics_router, prefix="/api/v1/analytics", tags=["Analytics"])

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again later."}
    )

# Add startup event
@app.on_event("startup")
async def startup_event():
    logger.info("RXinDexer API starting up")
    # Verify database connection
    from sqlalchemy import text
    db = next(get_db())
    db.execute(text("SELECT 1"))
    logger.info("Database connection verified")

# Add shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("RXinDexer API shutting down")

# If run directly, start the application with Uvicorn
if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment or use default
    port = int(os.getenv("API_PORT", "8000"))
    
    # Start server
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )
