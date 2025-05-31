# /Users/radiant/Desktop/RXinDexer/app.py
# This file provides a direct entry point to the RXinDexer application.
# It ensures the application can be started consistently across all environments.

import os
import sys
from pathlib import Path

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the FastAPI app from the main module
from src.api.main import app

# This allows the app to be run directly or via the Docker container
if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment or use default
    port = int(os.getenv("API_PORT", "8000"))
    host = os.getenv("API_HOST", "0.0.0.0")
    
    print(f"Starting RXinDexer API on {host}:{port}")
    
    # Start server
    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=os.getenv("API_DEBUG", "false").lower() == "true"
    )
