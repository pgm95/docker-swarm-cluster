#!/bin/sh
set -e

echo "Waiting for Docker socket proxy..."
while [ ! -S /alloy-proxy/docker.sock ]; do
    sleep 1
done
echo "Socket proxy ready."

exec alloy run /etc/alloy/config.alloy \
    --storage.path=/tmp/alloy \
    --server.http.listen-addr=0.0.0.0:12345
