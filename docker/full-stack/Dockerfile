# RXinDexer - Radiant Blockchain Indexer
# ElectrumX with Glyph/WAVE/Swap token indexing
# https://github.com/Radiant-Core/RXinDexer
#
# Build with: docker build -t rxindexer .
# Build timestamp: 2026-02-02

FROM ubuntu:22.04

LABEL maintainer="The Radiant Core devs"
LABEL version="2.0.0"
LABEL description="RXinDexer - Radiant blockchain indexer with Glyph/WAVE/Swap support"

ARG DEBIAN_FRONTEND=noninteractive
ENV PACKAGES="\
  build-essential \
  libcurl4-openssl-dev \
  software-properties-common \
  pkg-config \
  libtool \
  git \
  autoconf \
  automake \
  wget \
  curl \
  netcat-openbsd \
  cmake \
  python3 \
  python3-pip \
  python3-dev \
  libleveldb-dev \
  librocksdb-dev \
  libsnappy-dev \
  libbz2-dev \
  libzstd-dev \
  liblz4-dev \
  zlib1g-dev \
  libgflags-dev \
"

RUN apt update && apt install --no-install-recommends -y $PACKAGES && \
    rm -rf /var/lib/apt/lists/* && \
    apt clean

RUN groupadd -r electrumx && useradd -r -g electrumx -u 1000 electrumx

# Create directories
RUN mkdir -p /data/electrumdb /data/contracts && chown -R electrumx:electrumx /data

WORKDIR /opt

# Clone RXinDexer from GitHub
ARG RXINDEXER_BRANCH=main
RUN git clone --depth 1 --branch ${RXINDEXER_BRANCH} \
    https://github.com/Radiant-Core/RXinDexer.git electrumx

WORKDIR /opt/electrumx

# Install Python dependencies
# Fix: Pin Cython<3 BEFORE installing python-rocksdb to avoid Cython 3 incompatibility
# The --no-build-isolation flag forces pip to use our installed Cython version
RUN python3 -m pip install --upgrade pip setuptools wheel
RUN python3 -m pip install 'Cython<3'
RUN python3 -m pip install plyvel 'aiorpcX>=0.22.0,<0.23.0' pylru cbor2 websockets aiohttp uvicorn fastapi

# Install python-rocksdb with build isolation disabled to use Cython<3
RUN python3 -m pip install --no-build-isolation python-rocksdb

# Ensure the electrumx user can read and execute the repo
RUN chown -R root:electrumx /opt/electrumx && \
    chmod -R g+rX /opt/electrumx && \
    chmod g+x /opt/electrumx/electrumx_server

# Database engine
ENV DB_ENGINE=rocksdb

# Core configuration
ENV DAEMON_URL=http://user:pass@localhost:7332/
ENV COIN=Radiant
ENV NET=mainnet
ENV REQUEST_TIMEOUT=60
ENV DB_DIRECTORY=/data/electrumdb
ENV ELECTRUMX_ENV=prod

# SSL configuration
ENV SERVICES=tcp://0.0.0.0:50010,ssl://0.0.0.0:50012,wss://0.0.0.0:50011,rpc://127.0.0.1:8001
ENV SSL_CERTFILE=/data/electrumdb/server.crt
ENV SSL_KEYFILE=/data/electrumdb/server.key

# REST API (HTTP)
ENV REST_API_ENABLED=1
ENV REST_API_HOST=0.0.0.0
ENV REST_API_PORT=8000

# Production defaults
ENV CACHE_MB=10000
ENV MAX_SESSIONS=10000
ENV MAX_SEND=10000000
ENV MAX_RECV=10000000

# Rate limiting (relaxed to prevent local RPC throttling disconnects during sync)
ENV COST_SOFT_LIMIT=10000
ENV COST_HARD_LIMIT=100000
ENV INITIAL_CONCURRENT=50
ENV REQUEST_SLEEP=500

# RocksDB production tuning
ENV ROCKSDB_COMPRESSION=lz4
ENV ROCKSDB_BLOCK_CACHE_MB=256
ENV ROCKSDB_MAX_OPEN_FILES=512

# RXinDexer: Glyph token indexing (FT, NFT, dMint, Mutable, Containers)
ENV GLYPH_INDEX=1

# RXinDexer: WAVE naming system indexing
ENV WAVE_INDEX=1
ENV WAVE_HOT_NAMES=10000

# RXinDexer: Swap DEX order indexing
ENV SWAP_INDEX=1

# RXinDexer: Real-time WebSocket subscriptions
ENV GLYPH_SUBSCRIPTIONS=1

# RXinDexer: Mempool indexing for unconfirmed transactions
ENV MEMPOOL_GLYPH_INDEX=1
ENV MEMPOOL_SWAP_INDEX=1

# RXinDexer: dMint contracts configuration
ENV DMINT_CONTRACTS_FILE=/data/contracts/contracts.json

# Logging
ENV LOG_LEVEL=INFO

# Copy entrypoint script (generates SSL certs at runtime into the mounted volume)
COPY entrypoint.sh /opt/electrumx/entrypoint.sh
RUN chmod +x /opt/electrumx/entrypoint.sh

USER electrumx

WORKDIR /opt/electrumx

ENV PYTHONPATH=/opt/electrumx

EXPOSE 50010 50011 50012 8000

ENTRYPOINT ["/opt/electrumx/entrypoint.sh"]
