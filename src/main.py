# /Users/radiant/Desktop/RXinDexer/src/main.py
# This file serves as a compatibility layer for the RXinDexer application.
# It imports and re-exports the FastAPI app from the API module to maintain backward compatibility.

import sys
import os
from pathlib import Path

# Add the project root to Python path to ensure imports work in all environments
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# Simply import the FastAPI app from the API module
print("Loading app from src.api.main")
from src.api.main import app

# This will allow Docker to find the app object properly
print("Successfully loaded API application")

# Re-export everything needed by the Docker container
__all__ = ['app']
