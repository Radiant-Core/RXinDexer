# /Users/radiant/Desktop/RXinDexer/src/db/__init__.py
# This file makes the db directory a Python package.
# It provides database initialization and migration functionality.

from .init_db import create_tables

__all__ = ['create_tables']
