#!/bin/sh
# The Desktop tmpfs is only writable by the session user on a container's first
# start. Its mountpoint does not exist in the fresh /config volume, so the runtime
# creates it root:root 0755; on any later start of the same container (Docker
# retrying a failed start) the tmpfs inherits that mode and uploads and downloads
# fail with EACCES. Swarm drops a tmpfs mode from the service spec, so set it here.

chmod 1777 /config/Desktop || echo "init: could not chmod /config/Desktop, uploads and downloads will fail" >&2

exec /init "$@"
