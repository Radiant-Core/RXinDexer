#!/bin/sh
# RXinDexer entrypoint - generates SSL certs if missing, then starts the server

SSL_CERTFILE="${SSL_CERTFILE:-/data/electrumdb/server.crt}"
SSL_KEYFILE="${SSL_KEYFILE:-/data/electrumdb/server.key}"
SSL_DIR="$(dirname "$SSL_CERTFILE")"

if [ ! -f "$SSL_CERTFILE" ] || [ ! -f "$SSL_KEYFILE" ]; then
    echo "Generating SSL certificates in $SSL_DIR..."
    mkdir -p "$SSL_DIR"
    openssl genrsa -out "$SSL_KEYFILE" 2048
    openssl req -new -key "$SSL_KEYFILE" -out "$SSL_DIR/server.csr" \
        -subj "/C=US/ST=Denial/L=Springfield/O=Dis/CN=radiantblockchain.org"
    openssl x509 -req -days 1825 -in "$SSL_DIR/server.csr" \
        -signkey "$SSL_KEYFILE" -out "$SSL_CERTFILE"
    rm -f "$SSL_DIR/server.csr"
    echo "SSL certificates generated."
fi

exec python3 electrumx_server
