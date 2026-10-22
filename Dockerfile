# RXinDexer - Radiant Blockchain Indexer
# https://github.com/Radiant-Core/RXinDexer
# Build with: `docker build -t rxindexer .`

# Base image pinned to the multi-arch index digest resolved 2026-10-21
# (docker buildx imagetools inspect ubuntu:22.04). Bump deliberately.
FROM ubuntu:22.04@sha256:0a9887931b0e38af2e3487ed39e29436b0535c4f3f52e57745baaf79d1898956

LABEL maintainer="The Radiant Core devs"
LABEL version="3.0.1"
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
  libjemalloc2 \
"

RUN apt update && apt install --no-install-recommends -y $PACKAGES  && \
    rm -rf /var/lib/apt/lists/* && \
    apt clean

RUN groupadd -r electrumx && useradd -r -g electrumx -u 1000 electrumx
 
# Create directory for DB
RUN mkdir -p /data/electrumdb && chown -R electrumx:electrumx /data

WORKDIR /opt

# Clone RXinDexer from GitHub
ARG RXINDEXER_BRANCH=main
# Cache-bust the clone: ADD of a URL is re-fetched on every build, so the
# layers below are invalidated when the branch tip moves (and stay cached
# when it doesn't). Without this, Docker reuses a stale clone forever.
ADD https://api.github.com/repos/Radiant-Core/RXinDexer/git/refs/heads/${RXINDEXER_BRANCH} /tmp/rxindexer-ref.json
RUN git clone --depth 1 --branch ${RXINDEXER_BRANCH} \
    https://github.com/Radiant-Core/RXinDexer.git electrumx

WORKDIR /opt/electrumx

# Install Python dependencies
# Pin Cython<3 BEFORE installing python-rocksdb to avoid Cython 3 incompatibility
RUN python3 -m pip install --upgrade pip setuptools wheel
RUN python3 -m pip install 'Cython<3'

# Install all dependencies from the hashed lockfile (see requirements.txt
# header for regeneration). --no-build-isolation lets the python-rocksdb
# sdist build against the Cython<3 installed above; every downloaded
# artifact is verified against its pinned sha256.
RUN python3 -m pip install --require-hashes --no-build-isolation -r requirements.lock

# Ensure the electrumx user can read and execute the repo
RUN chown -R root:electrumx /opt/electrumx && \
    chmod -R g+rX /opt/electrumx && \
    chmod g+x /opt/electrumx/electrumx_server

# Core configuration
ENV DAEMON_URL=http://user:pass@localhost:7332/
ENV COIN=Radiant
ENV NET=mainnet
ENV REQUEST_TIMEOUT=60
ENV DB_DIRECTORY=/data/electrumdb
ENV DB_ENGINE=rocksdb

# jemalloc returns freed pages to the OS (pymalloc/glibc retain them as
# fragmented RSS across flush cycles). Verify with:
#   grep -m1 jemalloc /proc/$(pgrep -f electrumx_server)/maps
# (check the python process, not PID 1 = docker-init; ldd never shows an LD_PRELOAD)
ENV LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2
ENV MALLOC_CONF=background_thread:true,dirty_decay_ms:10000
ENV ELECTRUMX_ENV=prod

# SSL configuration
ENV SERVICES=tcp://0.0.0.0:50010,ssl://0.0.0.0:50012,wss://0.0.0.0:50011,rpc://127.0.0.1:8001
ENV SSL_CERTFILE=/data/electrumdb/server.crt
ENV SSL_KEYFILE=/data/electrumdb/server.key

# REST API (HTTP) - enabled by default in container
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

# RXinDexer: Token indexing features (all enabled by default)
ENV GLYPH_INDEX=1
ENV WAVE_INDEX=1
ENV WAVE_HOT_NAMES=10000
ENV SWAP_INDEX=1
ENV GLYPH_SUBSCRIPTIONS=1
ENV MEMPOOL_GLYPH_INDEX=1
ENV MEMPOOL_SWAP_INDEX=1

# Logging
ENV LOG_LEVEL=INFO

# Copy entrypoint script (generates SSL certs at runtime into the mounted volume)
COPY entrypoint.sh /opt/electrumx/entrypoint.sh
RUN chmod +x /opt/electrumx/entrypoint.sh

USER electrumx

WORKDIR /opt/electrumx

EXPOSE 50010 50011 50012 8000

ENV PYTHONPATH=/opt/electrumx

ENTRYPOINT ["/opt/electrumx/entrypoint.sh"]

# DOCKER USAGE
# Build: docker build -t rxindexer .
# Run:   docker run -d --net=host \
#          -e DAEMON_URL="http://user:pass@localhost:7332" \
#          -e REPORT_SERVICES=tcp://example.com:50010 \
#          rxindexer
# Stop:  docker kill --signal="TERM" CONTAINER_ID
 
