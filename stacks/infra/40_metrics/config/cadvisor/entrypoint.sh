#!/bin/sh
set -e

echo "Waiting for Docker socket proxy..."
while [ ! -S /cadvisor-proxy/docker.sock ]; do sleep 1; done
echo "Socket proxy ready."

exec /usr/bin/entrypoint.sh "$@"
