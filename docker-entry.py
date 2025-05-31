#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/docker-entry.py
# This file serves as a direct entry point for the RXinDexer API in Docker.
# It ensures that the application loads correctly regardless of environment.

import os
import sys
import importlib.util
from pathlib import Path

# Print banner for easy identification in logs
print("=" * 50)
print("RXinDexer API Starting")
print("=" * 50)

# Set up Python path properly
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
    print(f"Added {project_root} to Python path")

# Ensure environment variables are set
os.environ["IN_DOCKER"] = "true"

# Try to import the FastAPI app directly from the API module
try:
    print("Importing FastAPI app from src.api.main...")
    from src.api.main import app
    print("Successfully imported app from src.api.main")
except ImportError as e:
    print(f"Error importing from src.api.main: {e}")
    print("Falling back to compatibility layer...")
    
    # Create a compatibility module dynamically
    try:
        import fastapi
        print("Creating app dynamically...")
        app = fastapi.FastAPI(
            title="RXinDexer API",
            description="API for Radiant blockchain indexer with Glyph token support",
            version="1.0.0"
        )
        
        @app.get("/")
        def root():
            return {
                "status": "degraded",
                "message": "Only basic endpoints available due to import error",
                "service": "RXinDexer"
            }
        
        @app.get("/health")
        def health():
            return {
                "status": "degraded",
                "components": {
                    "api": "online",
                    "database": "unknown",
                    "radiant_node": "unknown"
                }
            }
        
        print("Created minimal compatibility API")
    except Exception as e:
        print(f"Fatal error creating compatibility API: {e}")
        sys.exit(1)

# Start the server
if __name__ == "__main__":
    import uvicorn
    
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8000"))
    
    print(f"Starting Uvicorn server on {host}:{port}")
    
    # Start with a direct reference to the app object
    uvicorn.run(app, host=host, port=port)
