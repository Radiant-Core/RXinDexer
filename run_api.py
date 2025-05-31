# /Users/radiant/Desktop/RXinDexer/run_api.py
# This file provides a simple entry point to run the RXinDexer API.
# It works consistently across all environments (Docker, development, production).

import os
import sys
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import FastAPI application
from src.api.main import app

if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment or use default
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8000"))
    
    print(f"Starting RXinDexer API on {host}:{port}")
    
    # Start the server
    uvicorn.run(
        "run_api:app",
        host=host,
        port=port,
        reload=os.environ.get("API_DEBUG", "").lower() == "true"
    )
