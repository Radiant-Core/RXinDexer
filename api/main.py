from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from api.middleware import rate_limit_middleware, security_headers_middleware
from api.endpoints import blocks, transactions, tokens, wallets, users, health, mempool, market, glyphs, stats, auth, tasks
from api.endpoints import websocket as ws_endpoints
from api.background_tasks import start_background_tasks, stop_background_tasks
from api.graphql import graphql_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown tasks."""
    # Startup
    await start_background_tasks()
    yield
    # Shutdown
    await stop_background_tasks()


app = FastAPI(
    title="RXinDexer API",
    description="Radiant blockchain indexer API for wallets, explorers, and token tracking",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://explorer.radiant.org", "http://localhost:3000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Add middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.middleware("http")(security_headers_middleware)
app.middleware("http")(rate_limit_middleware)

@app.get("/")
async def read_root():
    return {"message": "RXinDexer API is running", "async_enabled": True}

# Include routers
app.include_router(auth.router)
app.include_router(health.router)
app.include_router(blocks.router)
app.include_router(transactions.router)
app.include_router(tokens.router)
app.include_router(wallets.router)
app.include_router(users.router)
app.include_router(mempool.router)
app.include_router(market.router)
app.include_router(stats.router)
app.include_router(glyphs.router)
app.include_router(ws_endpoints.router)
app.include_router(tasks.router)

# GraphQL API endpoint
app.include_router(graphql_router, prefix="")

# Also expose the same routes under /api/* for compatibility with frontend proxies.
app.include_router(auth.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(blocks.router, prefix="/api")
app.include_router(transactions.router, prefix="/api")
app.include_router(tokens.router, prefix="/api")
app.include_router(wallets.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(mempool.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(glyphs.router, prefix="/api")
