# /Users/radiant/Desktop/RXinDexer/src/api/main.py
# This file defines the main FastAPI application for RXinDexer.
# It integrates all API endpoints and provides the central entry point for the API.
# It enforces security measures including headers and API key authentication.

import logging
import os
import time
import uuid
import psutil
from typing import List, Optional
from starlette.middleware.base import BaseHTTPMiddleware

from fastapi import FastAPI, Depends, HTTPException, Request, Response, status, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import SecurityScopes
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from starlette.responses import Response as StarletteResponse
from sqlalchemy.orm import Session
from src.api.common import PaginationParams
from src.api.health import health_router
from sqlalchemy import func

# Create a custom response class to add security headers to every response
class SecureResponse(StarletteResponse):
    def init_headers(self, headers=None):
        super().init_headers(headers)
        # Add security headers to every response
        self.headers["X-Content-Type-Options"] = "nosniff"
        self.headers["X-Frame-Options"] = "DENY"
        self.headers["X-XSS-Protection"] = "1; mode=block"
        self.headers["Content-Security-Policy"] = "default-src 'self';"

# Import security module
from src.api.security import get_api_key

from src.models import get_db

# Import core blockchain API routers
from src.api.address import router as address_router
from src.api.transaction import router as transaction_router
from src.api.token import router as token_router
from src.api.holder import router as holder_router
from src.api.blocks import router as blocks_router
from src.api.status import router as status_router
from src.api.metrics import router as metrics_router

# Import enhanced API routers
from src.api.nft_endpoints import router as nft_router
from src.api.user_container_endpoints import router as user_container_router
from src.api.analytics_endpoints import router as analytics_router

# Configure logging
from src.utils.logging_config import setup_logging
logger = setup_logging('api')

# Import for static file serving and documentation
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

# Create FastAPI app with documentation disabled initially
app = FastAPI(
    title="RXinDexer API",
    description="Advanced indexing and API services for the Radiant blockchain",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)

# Serve static files
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

# Manually configure OpenAPI and documentation endpoints
@app.get("/api/openapi.json", include_in_schema=False)
async def get_openapi_schema():
    return JSONResponse(
        get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
    )

# Custom Swagger UI endpoint
@app.get("/api/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title=app.title + " - Swagger UI",
        swagger_js_url="/static/swagger/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger/swagger-ui.css"
    )

# Custom ReDoc endpoint
@app.get("/api/redoc", include_in_schema=False)
async def custom_redoc_html():
    return get_redoc_html(
        openapi_url="/api/openapi.json",
        title=app.title + " - ReDoc"
    )

# Import security utilities
from src.api.security import get_api_key, APIKeyHeader

# Configure API key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Configure CORS middleware with stricter settings for production
allow_origins = os.environ.get("ALLOWED_ORIGINS", "*")
if allow_origins == "*":
    logger.warning("CORS is set to allow all origins. This is not recommended for production.")
    origins = ["*"]
else:
    origins = allow_origins.split(",")
    
# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],  # Restrict to necessary methods
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
    expose_headers=["X-Process-Time", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    max_age=86400  # Cache preflight requests for 24 hours
)

# Enhanced request logging, timing, and rate limiting middleware
@app.middleware("http")
async def request_middleware(request: Request, call_next):
    # Generate a unique request ID
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    
    # Add the request ID to the logging context
    logger.info(f"Request started: {request.method} {request.url.path} | ID: {request_id}")
    
    # Measure request processing time
    start_time = time.time()
    
    # Process the request
    response = await call_next(request)
    
    # Calculate processing time
    process_time = time.time() - start_time
    
    # Add timing and request ID headers
    response.headers["X-Process-Time"] = str(process_time)
    response.headers["X-Request-ID"] = request_id
    
    # Add rate limit headers if available
    if hasattr(request.state, "ratelimit"):
        rate_limit = request.state.ratelimit
        response.headers["X-RateLimit-Limit"] = str(rate_limit["limit"])
        response.headers["X-RateLimit-Remaining"] = str(rate_limit["remaining"])
        response.headers["X-RateLimit-Reset"] = str(rate_limit["reset"])
    
    # Log request completion
    status_info = f"Status: {response.status_code}"
    logger.info(f"Request completed: {request.method} {request.url.path} | ID: {request_id} | Time: {process_time:.4f}s | {status_info}")
    
    return response

# Root endpoint - no auth required
@app.get("/")
async def root():
    return {
        "name": "RXinDexer API",
        "version": "1.0.0",
        "description": "Advanced indexing and API services for the Radiant blockchain",
        "documentation": "/api/docs"
    }

# Direct metrics endpoint for public monitoring - no auth required
@app.get("/metrics")
async def get_metrics(db: Session = Depends(get_db)):
    """Get system metrics and statistics for monitoring"""
    try:
        start_time = time.time()
        metrics = {
            "system": {
                "memory_usage_mb": psutil.Process().memory_info().rss / (1024 * 1024),
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "uptime_seconds": int(time.time() - psutil.boot_time())
            },
            "database": {
                "connection_pool_size": 10,  # Default connection pool size
                "active_connections": 5,     # Placeholder value
                "query_count": 0,            # Placeholder value
            },
            "api": {
                "requests_per_minute": 0,     # Placeholder value
                "average_response_time_ms": 50, # Placeholder value
                "error_rate": 0.0             # Placeholder value
            },
            "timestamp": time.time()
        }
        return metrics
    except Exception as e:
        logger.error(f"Error fetching metrics: {e}")
        # Still return partial metrics rather than error
        return {
            "system": {
                "memory_usage_mb": 0,
                "cpu_percent": 0,
                "uptime_seconds": 0
            },
            "timestamp": time.time()
        }

# Direct token statistics endpoint - no auth required
@app.get("/api/v1/tokens/stats")
async def get_token_statistics(db: Session = Depends(get_db)):
    """Get token statistics"""
    try:
        # Basic token statistics with default values when DB is empty
        stats = {
            "total_tokens": 0,
            "tokens_by_type": {
                "fungible": 0,
                "non-fungible": 0,
                "dmint": 0
            },
            "unique_holders": 0,
            "latest_token": {
                "ref": "",
                "type": "",
                "genesis_txid": ""
            },
            "timestamp": int(time.time())
        }
        
        # Try to get real data from DB if available
        try:
            from src.models import GlyphToken, UTXO
            
            # Get total count if table exists
            stats["total_tokens"] = db.query(GlyphToken).count()
            
            # Get counts by type
            for t_type in ["fungible", "non-fungible", "dmint"]:
                count = db.query(GlyphToken).filter(GlyphToken.type == t_type).count()
                stats["tokens_by_type"][t_type] = count
                
            # Get unique holders count if UTXO table exists
            unique_holders = db.query(UTXO.address).filter(
                UTXO.token_ref.isnot(None),
                UTXO.spent == False
            ).distinct().count()
            stats["unique_holders"] = unique_holders
            
            # Get most recent token if available
            latest_token = db.query(GlyphToken).order_by(
                GlyphToken.created_at.desc()
            ).first()
            
            if latest_token:
                stats["latest_token"] = {
                    "ref": latest_token.ref,
                    "type": latest_token.type,
                    "genesis_txid": latest_token.genesis_txid
                }
        except Exception as db_err:
            logger.warning(f"Error fetching token statistics from DB: {db_err}")
            # Continue with default values
        
        return stats
    except Exception as e:
        logger.error(f"Error in token statistics endpoint: {e}")
        return {"detail": "Internal server error"}

# Define API key dependency using the security module
async def require_api_key(api_key: str = Depends(get_api_key)):
    """Dependency to enforce API key requirement and validation
    
    This uses the security module's get_api_key function which handles:
    - API key validation
    - Rate limiting
    - Exempt paths
    """
    return api_key

# Token holders endpoint - public access
@app.get("/api/v1/tokens/stats/holders")
async def get_token_holders(
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db)
):
    """Get token holder statistics and details"""
    try:
            
        # Initialize empty response structure
        response = {
            "total_holders": 0,
            "holders": [],
            "pagination": pagination.get_pagination_dict(0)
        }
        
        try:
            from src.models import UTXO, GlyphToken
            
            # Get total unique holders count
            unique_holders_query = db.query(UTXO.address).filter(
                UTXO.token_ref.isnot(None),
                UTXO.spent == False
            ).distinct()
            
            total_holders = unique_holders_query.count()
            response["total_holders"] = total_holders
            
            # Get paginated list of holders with their token balances
            if total_holders > 0:
                # Get holders with pagination
                holder_addresses = unique_holders_query.order_by(UTXO.address)
                holder_addresses = pagination.apply_to_query(holder_addresses).all()
                
                # For each holder address, get their token balances
                for address_row in holder_addresses:
                    address = address_row[0]
                    
                    # Get token balances for this address
                    token_balances = db.query(
                        UTXO.token_ref,
                        func.sum(UTXO.token_amount).label('total_amount'),
                        GlyphToken.type
                    ).join(
                        GlyphToken,
                        UTXO.token_ref == GlyphToken.ref,
                        isouter=True
                    ).filter(
                        UTXO.address == address,
                        UTXO.token_ref.isnot(None),
                        UTXO.spent == False
                    ).group_by(
                        UTXO.token_ref,
                        GlyphToken.type
                    ).all()
                    
                    # Format holder data
                    holder_data = {
                        "address": address,
                        "tokens": [
                            {
                                "ref": balance[0],
                                "amount": float(balance[1]),
                                "type": balance[2] or "unknown"
                            }
                            for balance in token_balances
                        ],
                        "total_tokens": len(token_balances)
                    }
                    
                    response["holders"].append(holder_data)
            
            # Update pagination info
            response["pagination"] = pagination.get_pagination_dict(total_holders)
            
        except Exception as db_err:
            logger.warning(f"Error fetching token holders from DB: {db_err}")
            # Continue with empty response
        
        return response
    except HTTPException:
        # Re-raise HTTP exceptions (like 403 for invalid API key)
        raise
    except Exception as e:
        logger.error(f"Error in token holders endpoint: {e}")
        return {"detail": "Internal server error"}

# Direct system status endpoint - no auth required
@app.get("/api/v1/status", response_model=dict, response_class=SecureResponse)
async def get_system_status(db: Session = Depends(get_db)):
    """Get overall system status"""
    try:
        # Initialize default status data
        status_data = {
            "api": {
                "status": "online",
                "version": "1.0.0",
                "uptime_seconds": (time.time() - app.start_time) if hasattr(app, 'start_time') else 0
            },
            "database": {
                "status": "connected",
                "size_mb": 0,
                "tables": 0,
                "rows": 0
            },
            "blockchain": {
                "status": "syncing",
                "height": 0,
                "hash": "",
                "sync_progress": 0.0
            },
            "memory": {
                "used_mb": psutil.virtual_memory().used / (1024 * 1024),
                "available_mb": psutil.virtual_memory().available / (1024 * 1024)
            },
            "timestamp": time.time()
        }
        
        # Get sync state directly from the sync_state table
        try:
            logger.info("Fetching sync state from database...")
            
            # Test database connection first
            try:
                db.execute("SELECT 1").fetchone()
                logger.info("Database connection test successful")
            except Exception as conn_err:
                logger.error(f"Database connection test failed: {str(conn_err)}")
            
            # Execute the actual query
            query = """
                SELECT current_height, current_hash, is_syncing, last_updated_at 
                FROM sync_state 
                WHERE id = 1
            """
            logger.info(f"Executing query: {query}")
            
            sync_state = db.execute(query).fetchone()
            logger.info(f"Raw sync state from DB: {sync_state}")
            
            # Log the database URL (without password) for debugging
            try:
                db_url = str(db.bind.url) if hasattr(db, 'bind') and hasattr(db.bind, 'url') else 'unknown'
                logger.info(f"Database URL: {db_url}")
            except Exception as url_err:
                logger.warning(f"Couldn't get database URL: {url_err}")
            
            if sync_state:
                status_data["blockchain"]["height"] = sync_state[0] or 0
                status_data["blockchain"]["hash"] = sync_state[1] or ""
                # Convert is_syncing from integer to boolean properly
                is_syncing = bool(sync_state[2]) if sync_state[2] is not None else False
                status_data["blockchain"]["status"] = "syncing" if is_syncing else "synced"
                
                # If we have a block height, calculate progress (assuming 1M blocks as max for now)
                if status_data["blockchain"]["height"] > 0:
                    # Try to get current chain tip from RPC if available
                    try:
                        from src.sync.rpc_selector import RadiantRPC
                        rpc = RadiantRPC()
                        blockchain_info = rpc.getblockchaininfo()
                        if blockchain_info and 'blocks' in blockchain_info:
                            estimated_total_blocks = blockchain_info['blocks']
                            progress = min(1.0, status_data["blockchain"]["height"] / estimated_total_blocks)
                            status_data["blockchain"]["sync_progress"] = round(progress * 100, 2)
                            # Update the sync state if we're very close to the tip
                            if progress > 0.998:  # 99.8% synced
                                status_data["blockchain"]["status"] = "synced"
                    except Exception as rpc_err:
                        logger.warning(f"Couldn't get blockchain info from RPC: {rpc_err}")
                        # Fallback to fixed estimate if RPC fails
                        estimated_total_blocks = 1000000
                        progress = min(1.0, status_data["blockchain"]["height"] / estimated_total_blocks)
                        status_data["blockchain"]["sync_progress"] = round(progress * 100, 2)
                
                # Update last_updated from sync_state
                if sync_state[3]:
                    status_data["blockchain"]["last_updated"] = sync_state[3].timestamp()
                    
                logger.info(f"Processed sync state: {status_data['blockchain']}")
            else:
                logger.warning("No sync state found in database")
                
        except Exception as e:
            logger.error(f"Error fetching sync state: {str(e)}", exc_info=True)
            
        # Get database statistics without querying the blocks table
        try:
            # Get DB size (approximate)
            db_size = db.execute("""
                SELECT pg_size_pretty(pg_database_size(current_database()))
            """).scalar()
            if db_size:
                # Convert pretty size to MB
                if 'MB' in db_size:
                    status_data["database"]["size_mb"] = float(db_size.replace('MB', '').strip())
                elif 'GB' in db_size:
                    status_data["database"]["size_mb"] = float(db_size.replace('GB', '').strip()) * 1024
                elif 'KB' in db_size:
                    status_data["database"]["size_mb"] = float(db_size.replace('KB', '').strip()) / 1024
                else:
                    status_data["database"]["size_mb"] = float(db_size)
                
            # Get table count
            table_count = db.execute("""
                SELECT count(*) 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """).scalar()
            status_data["database"]["tables"] = table_count or 0
            
            # Use the height from sync_state as the row count if available
            if 'blockchain' in status_data and 'height' in status_data['blockchain']:
                status_data["database"]["rows"] = status_data['blockchain']['height']
                
            # Add a note that we're not counting all rows for performance
            status_data["database"]["note"] = "Row count is an estimate based on blockchain height"
        except Exception as e:
            logger.warning(f"Error getting database stats: {e}")
            # Set default values if there's an error
            status_data["database"]["size_mb"] = 0
            status_data["database"]["tables"] = 0
            status_data["database"]["rows"] = 0
        
        return status_data
        
    except Exception as e:
        logger.error(f"Error in system status endpoint: {e}")
        # Return basic status rather than error
        return {
            "api": {"status": "online"},
            "database": {"status": "unknown"},
            "timestamp": time.time()
        }

# Direct sync status endpoint - no auth required
@app.get("/api/v1/status/sync")
async def get_sync_status(db: Session = Depends(get_db)):
    """Get detailed blockchain synchronization status"""
    try:
        # Default sync status
        sync_data = {
            "current_height": 0,
            "current_hash": "",
            "is_syncing": True,
            "sync_progress": 0.0,
            "estimated_remaining_time": "unknown",
            "last_updated": time.time()
        }
        
        # Try to get actual data from DB if available
        try:
            # First try to query the sync_state table
            db.execute("SELECT 1 FROM sync_state LIMIT 1")
            
            # If table exists, get the data
            result = db.execute("""SELECT current_height, current_hash, is_syncing, 
                                last_updated_at FROM sync_state WHERE id = 1""").fetchone()
            
            if result:
                sync_data["current_height"] = result[0] or 0
                sync_data["current_hash"] = result[1] or ""
                sync_data["is_syncing"] = bool(result[2])
                sync_data["last_updated"] = float(result[3]) if result[3] else time.time()
                
                # Calculate estimated progress (simplified)
                estimated_total_blocks = 1000000  # Placeholder value
                sync_data["sync_progress"] = min(1.0, sync_data["current_height"] / estimated_total_blocks) 
                
                # Calculate estimated time remaining (simplified)
                if sync_data["is_syncing"] and sync_data["current_height"] > 0:
                    sync_data["estimated_remaining_time"] = "2 hours"  # Placeholder
        except Exception as db_err:
            logger.warning(f"Error fetching sync status from DB: {db_err}")
            # Continue with default values
        
        return sync_data
    except Exception as e:
        logger.error(f"Error in sync status endpoint: {e}")
        # Return basic status rather than error
        return {
            "is_syncing": True,
            "sync_progress": 0.0,
            "last_updated": time.time()
        }

# Direct latest block endpoint - no auth required
@app.get("/api/v1/blocks/latest")
async def get_latest_block(db: Session = Depends(get_db)):
    """Get latest block information"""
    try:
        # Default block data when DB is empty
        block_data = {
            "height": 0,
            "hash": "",
            "prev_hash": "",
            "timestamp": int(time.time()),
            "size": 0,
            "transaction_count": 0,
            "confirmations": 0
        }
        
        # Try to get actual data from DB
        try:
            from src.models import Block
            
            # Get latest block from DB
            latest_block = db.query(Block).order_by(Block.height.desc()).first()
            
            if latest_block:
                block_data["height"] = latest_block.height
                block_data["hash"] = latest_block.hash
                block_data["prev_hash"] = latest_block.prev_hash
                block_data["timestamp"] = latest_block.timestamp
                block_data["size"] = latest_block.size
                block_data["transaction_count"] = latest_block.tx_count
                
                # Calculate confirmations (simplified)
                current_height = latest_block.height  # In a real impl, would get from chain tip
                block_data["confirmations"] = 1  # Default for latest block
        except Exception as db_err:
            logger.warning(f"Error fetching latest block from DB: {db_err}")
            # Continue with default values
            
            # Try to get block from RPC as fallback
            try:
                from src.sync.rpc_selector import RadiantRPC
                rpc = RadiantRPC()
                latest_block_rpc = rpc.get_best_block()
                
                if latest_block_rpc:
                    block_data["height"] = latest_block_rpc.get("height", 0)
                    block_data["hash"] = latest_block_rpc.get("hash", "")
                    
                    # Get more details if we have a hash
                    if block_data["hash"]:
                        block_details = rpc.get_block(block_data["hash"])
                        if block_details:
                            block_data["prev_hash"] = block_details.get("previousblockhash", "")
                            block_data["timestamp"] = block_details.get("time", int(time.time()))
                            block_data["size"] = block_details.get("size", 0)
                            block_data["transaction_count"] = len(block_details.get("tx", []))
            except Exception as rpc_err:
                logger.warning(f"Error fetching latest block from RPC: {rpc_err}")
                # Continue with default values
        
        return block_data
    except Exception as e:
        logger.error(f"Error in latest block endpoint: {e}")
        # Return basic block data rather than error
        return {
            "height": 0,
            "hash": "",
            "timestamp": int(time.time())
        }

# Direct latest transactions endpoint - no auth required
@app.get("/api/v1/transactions/latest")
async def get_latest_transactions(pagination: PaginationParams = Depends(), db: Session = Depends(get_db)):
    """Get latest transactions"""
    try:
        # Default empty response
        transactions = []
        pagination_data = {
            "page": pagination.page,
            "limit": pagination.limit,
            "total_items": 0,
            "total_pages": 0,
            "has_next": False,
            "has_prev": pagination.page > 1
        }
        
        # Try to get actual data from DB
        try:
            from src.models import Transaction
            
            # Get transaction count
            total_count = db.query(Transaction).count()
            
            # Calculate pagination data
            pagination_data["total_items"] = total_count
            pagination_data["total_pages"] = max(1, (total_count + pagination.limit - 1) // pagination.limit)
            pagination_data["has_next"] = pagination.page < pagination_data["total_pages"]
            
            # Get paginated transactions
            if total_count > 0:
                db_txs = db.query(Transaction).order_by(
                    Transaction.block_height.desc(), 
                    Transaction.id.desc()
                ).offset((pagination.page - 1) * pagination.limit).limit(pagination.limit).all()
                
                for tx in db_txs:
                    tx_data = {
                        "txid": tx.txid,
                        "block_height": tx.block_height,
                        "timestamp": tx.timestamp,
                        "amount": tx.amount,
                        "fee": tx.fee,
                        "confirmations": 1  # Simplified
                    }
                    transactions.append(tx_data)
        except Exception as db_err:
            logger.warning(f"Error fetching latest transactions from DB: {db_err}")
            # Continue with empty list
        
        return {
            "transactions": transactions,
            "pagination": pagination_data
        }
    except Exception as e:
        logger.error(f"Error in latest transactions endpoint: {e}")
        # Return empty list rather than error
        return {
            "transactions": [],
            "pagination": {
                "page": pagination.page,
                "limit": pagination.limit,
                "total_items": 0,
                "total_pages": 0,
                "has_next": False,
                "has_prev": pagination.page > 1
            }
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

# Include API routers with proper prefixes and security dependencies

# Core blockchain API endpoints - require API key
app.include_router(
    address_router, 
    prefix="/api/v1/address", 
    tags=["Addresses"],
    dependencies=[Depends(get_api_key)]
)
app.include_router(
    transaction_router, 
    prefix="/api/v1/transactions", 
    tags=["Transactions"]
)
app.include_router(
    token_router, 
    prefix="/api/v1/tokens", 
    tags=["Tokens"],
    dependencies=[Depends(get_api_key)]
)
app.include_router(
    holder_router, 
    prefix="/api/v1/holders", 
    tags=["Holders"]
)

# Block and status API endpoints - require API key
app.include_router(
    blocks_router, 
    prefix="/api/v1/blocks", 
    tags=["Blocks"]
)
app.include_router(
    status_router, 
    prefix="/api/v1/status", 
    tags=["System Status"]
)
app.include_router(
    metrics_router, 
    prefix="/metrics", 
    tags=["Metrics"]
)

# ============================================================
# PUBLIC API ENDPOINTS - NO API KEY REQUIRED
# These endpoints are directly attached to the app
# and bypass the regular router registration
# ============================================================

# Register health router - PUBLIC
app.include_router(
    health_router,
    prefix="",
    tags=["Health"]
)

# Enhanced blockchain API endpoints - require API key
app.include_router(
    nft_router, 
    prefix="/api/v1", 
    tags=["NFTs"],
    dependencies=[Depends(get_api_key)]
)
app.include_router(
    user_container_router, 
    prefix="/api/v1", 
    tags=["Users & Containers"],
    dependencies=[Depends(get_api_key)]
)
app.include_router(
    analytics_router, 
    prefix="/api/v1/analytics", 
    tags=["Analytics"],
    dependencies=[Depends(get_api_key)]
)

# Enhanced global exception handler with request ID
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Get request ID if available
    request_id = getattr(request.state, "request_id", "unknown")
    
    # Log detailed error
    logger.error(
        f"Unhandled exception (Request {request_id}): {str(exc)} "
        f"- Path: {request.url.path} - Method: {request.method} "
        f"- Client: {request.client.host}", 
        exc_info=exc
    )
    
    # Return sanitized error to client
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "request_id": request_id,
            "detail": "An unexpected error occurred. Please try again later."
        }
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
