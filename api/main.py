from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from api.middleware import rate_limit_middleware, security_headers_middleware

from api.endpoints import blocks, transactions, tokens, wallets, users, health, mempool, market, glyphs, stats

app = FastAPI(
    title="RXinDexer API",
    description="Radiant blockchain indexer API for wallets, explorers, and token tracking",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.middleware("http")(security_headers_middleware)
app.middleware("http")(rate_limit_middleware)

@app.get("/")
def read_root():
    return {"message": "RXinDexer API is running"}

# Include routers
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

# Also expose the same routes under /api/* for compatibility with frontend proxies.
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
