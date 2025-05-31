# /Users/radiant/Desktop/RXinDexer/Dockerfile
# This file defines the Docker image for the RXinDexer application.
# It sets up a Python environment with all dependencies and configures the application to run reliably across environments.

# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    IN_DOCKER=true

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    postgresql-client \
    curl \
    netcat-traditional \
    git \
    ca-certificates \
    procps \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file separately (leverages Docker layer caching)
COPY requirements.txt .

# Install dependencies with explicit version pinning
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    # Install additional packages for production readiness
    pip install --no-cache-dir gunicorn uvloop httptools && \
    # Verify installation
    python -c "import sqlalchemy, fastapi, cbor2, uvicorn; print('Dependencies installed successfully')"

# Copy application code
COPY . .

# Create necessary directories with proper permissions
RUN mkdir -p /app/logs /app/data /app/src/db/functions && \
    chmod -R 755 /app/logs /app/data

# Expose ports
EXPOSE 8000

# Create a non-root user and set permissions
RUN groupadd -r rxindexer && \
    useradd -r -g rxindexer -d /app -s /bin/bash rxindexer && \
    chown -R rxindexer:rxindexer /app

# Set up entrypoint script with proper permissions
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && \
    chown rxindexer:rxindexer /entrypoint.sh

# Add healthcheck with multiple endpoint fallbacks
HEALTHCHECK --interval=15s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:8000/health || curl -f http://localhost:8000/api/v1/health || exit 1

# Switch to non-root user for better security
USER rxindexer

ENTRYPOINT ["/entrypoint.sh"]

# Use our unified entry point for more reliable startup
CMD ["python", "docker-entry.py"]
