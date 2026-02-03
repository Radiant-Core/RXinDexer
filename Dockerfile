# RXinDexer - Radiant Blockchain Indexer
# https://github.com/Radiant-Core/RXinDexer
# Build with: `docker build -t rxindexer .`

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

RUN apt update && apt install --no-install-recommends -y $PACKAGES  && \
    rm -rf /var/lib/apt/lists/* && \
    apt clean

RUN groupadd -r electrumx && useradd -r -g electrumx -u 1000 electrumx
 
# Create directory for DB
RUN mkdir -p /data/electrumdb && chown -R electrumx:electrumx /data

WORKDIR /root

# Clone RXinDexer from GitHub
ARG RXINDEXER_BRANCH=main
RUN git clone --depth 1 --branch ${RXINDEXER_BRANCH} \
    https://github.com/Radiant-Core/RXinDexer.git electrumx

WORKDIR /root/electrumx

# Install Python dependencies
# Pin Cython<3 BEFORE installing python-rocksdb to avoid Cython 3 incompatibility
RUN python3 -m pip install --upgrade pip setuptools wheel
RUN python3 -m pip install 'Cython<3'

# Install core dependencies first (except python-rocksdb)
RUN python3 -m pip install plyvel 'aiorpcX[ws]>=0.22,<0.23' attrs pylru 'aiohttp>=3.3,<4.0' \
    'websockets>=10.0,<11.0' psutil 'cbor2>=5.4.0' 'fastapi>=0.109.0' \
    'uvicorn[standard]>=0.27.0' 'pydantic>=2.5.0'

# Install python-rocksdb with build isolation disabled to use Cython<3
RUN python3 -m pip install --no-build-isolation 'python-rocksdb<=0.7.0'

# Core configuration
ENV DAEMON_URL=http://user:pass@localhost:7332/
ENV COIN=Radiant
ENV NET=mainnet
ENV REQUEST_TIMEOUT=60
ENV DB_DIRECTORY=/data/electrumdb
ENV DB_ENGINE=rocksdb
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

# Rate limiting (security)
ENV COST_SOFT_LIMIT=1000
ENV COST_HARD_LIMIT=10000

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

USER electrumx

# Create SSL certificates
WORKDIR /data/electrumdb
RUN openssl genrsa -out server.key 2048
RUN openssl req -new -key server.key -out server.csr -subj "/C=US/ST=Denial/L=Springfield/O=Dis/CN=radiantblockchain.org"
RUN openssl x509 -req -days 1825 -in server.csr -signkey server.key -out server.crt

WORKDIR /root/electrumx

EXPOSE 50010 50011 50012 8000

ENTRYPOINT ["python3", "electrumx_server"]

# DOCKER USAGE
# Build: docker build -t rxindexer .
# Run:   docker run -d --net=host \
#          -e DAEMON_URL="http://user:pass@localhost:7332" \
#          -e REPORT_SERVICES=tcp://example.com:50010 \
#          rxindexer
# Stop:  docker kill --signal="TERM" CONTAINER_ID
 
