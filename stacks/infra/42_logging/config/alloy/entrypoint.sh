#!/bin/sh
set -e

TIMEOUT=60
ELAPSED=0
echo "Waiting for Docker socket proxy..."
while [ ! -S /alloy-proxy/docker.sock ]; do
    sleep 1
    ELAPSED=$((ELAPSED + 1))
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo "ERROR: socket proxy not ready after ${TIMEOUT}s"
        exit 1
    fi
done
echo "Socket proxy ready."

exec alloy run /etc/alloy/config.alloy \
    --storage.path=/tmp/alloy \
    --server.http.listen-addr=0.0.0.0:12345
