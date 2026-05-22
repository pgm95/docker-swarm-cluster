#!/bin/sh
set -e

# Swarm cannot run privileged services or pass devices, so this launcher (a
# plain docker CLI task) starts the device-mapping-manager as a privileged
# container directly on the node. The manager watches container start events
# and grants cgroup access for any device bind-mounted under /dev.

NAME=dmm
IMAGE="${DMM_IMAGE:?DMM_IMAGE not set}"

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    exit 0
}
trap cleanup TERM INT

# Drop any instance left by a previous launcher task before starting fresh.
docker rm -f "$NAME" >/dev/null 2>&1 || true

echo "launch: starting $NAME from $IMAGE" >&2
docker run -d --name "$NAME" --restart always \
    --privileged --cgroupns=host --pid=host --userns=host \
    --network none \
    -v /sys:/host/sys \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket \
    "$IMAGE"

# Stay alive as this swarm task and surface the manager's logs to the log driver.
docker logs -f "$NAME" &
wait $!
