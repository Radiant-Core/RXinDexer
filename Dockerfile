# ElectrumX for Radiant - The Radiant Blockchain Developers
# https://github.com/Radiant-Core/ElectrumX
# Build with: `docker build -t electrumx .`

FROM ubuntu:22.04

LABEL maintainer="The Radiant Core devs"
LABEL version="1.3.0"
LABEL description="Docker image for electrumx radiantd node"

ARG DEBIAN_FRONTEND=noninteractive
ENV PACKAGES="\
  build-essential \
  libcurl4-openssl-dev \
  software-properties-common \
  ubuntu-drivers-common \
  pkg-config \
  libtool \
  openssh-server \
  git \
  clinfo \
  autoconf \
  automake \
  vim \
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
# Note can remove the opencl and ocl packages above when not building on a system for GPU/mining
# Included only for reference purposes if this container would be used for mining as well.

RUN apt update && apt install --no-install-recommends -y $PACKAGES  && \
    rm -rf /var/lib/apt/lists/* && \
    apt clean
 
# Create directory for DB
RUN mkdir /root/electrumdb

WORKDIR /root

# Clone from GitHub
RUN git clone --depth 1 --branch master https://github.com/Radiant-Core/ElectrumX.git electrumx

WORKDIR /root/electrumx

# Install Cython first to avoid python-rocksdb build issues
RUN python3 -m pip install 'Cython<3'
RUN python3 -m pip install -r requirements.txt

# Core configuration
ENV DAEMON_URL=http://user:pass@localhost:7332/
ENV COIN=Radiant
ENV NET=mainnet
ENV REQUEST_TIMEOUT=60
ENV DB_DIRECTORY=/root/electrumdb
ENV DB_ENGINE=rocksdb
ENV ELECTRUMX_ENV=prod

# SSL configuration
ENV SERVICES=tcp://0.0.0.0:50010,ssl://0.0.0.0:50012,wss://0.0.0.0:50011,rpc://0.0.0.0:8000
ENV SSL_CERTFILE=/root/electrumdb/server.crt
ENV SSL_KEYFILE=/root/electrumdb/server.key

# Production defaults
ENV ALLOW_ROOT=true
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

# Create SSL
WORKDIR /root/electrumdb
RUN openssl genrsa -out server.key 2048
RUN openssl req -new -key server.key -out server.csr -subj "/C=US/ST=Denial/L=Springfield/O=Dis/CN=radiantblockchain.org"
RUN openssl x509 -req -days 1825 -in server.csr -signkey server.key -out server.crt

WORKDIR /root/electrumx

EXPOSE 50010 50011 50012 8000

ENTRYPOINT ["python3", "electrumx_server"]

##### DOCKER INFO
# build it with eg.: `docker build -t electrumx .`
# run it with eg.:
# `docker run -d --net=host -e DAEMON_URL="http://youruser:yourpass@localhost:7332" -e REPORT_SERVICES=tcp://example.com:50010 electrumx`
# for a proper clean shutdown, send TERM signal to the running container eg.: `docker kill --signal="TERM" CONTAINER_ID`
 
