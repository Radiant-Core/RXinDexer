FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG RADIANT_NODE_REPO=https://github.com/Radiant-Core/Radiant-Core.git
ARG RADIANT_NODE_REF=main

RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates \
  git \
  build-essential \
  cmake \
  ninja-build \
  pkg-config \
  libevent-dev \
  libboost-chrono-dev \
  libboost-filesystem-dev \
  libboost-test-dev \
  libboost-thread-dev \
  libminiupnpc-dev \
  libssl-dev \
  libzmq3-dev \
  libdb-dev \
  libdb++-dev \
  python3 \
  python3-dev \
  help2man \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --filter=blob:none ${RADIANT_NODE_REPO} radiant-node \
  && cd radiant-node \
  && git checkout ${RADIANT_NODE_REF}

WORKDIR /src/radiant-node

RUN mkdir -p build
WORKDIR /src/radiant-node/build

RUN cmake -GNinja .. -DBUILD_RADIANT_QT=OFF -DCLIENT_VERSION_IS_RELEASE=ON
RUN ninja && ninja install

EXPOSE 7332 7333

ENTRYPOINT ["radiantd"]
