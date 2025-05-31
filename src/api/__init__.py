# /Users/radiant/Desktop/RXinDexer/src/api/__init__.py
# This file makes the api directory a Python package.
# It provides access to the API routers for the FastAPI application.

from .address import router as address_router
from .token import router as token_router
from .holder import router as holder_router
from .transaction import router as transaction_router

__all__ = [
    'address_router',
    'token_router',
    'holder_router',
    'transaction_router'
]
