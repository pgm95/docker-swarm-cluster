# cAdvisor Stack

| Service | Purpose | Mode |
|---------|---------|------|
| cadvisor | Container metrics exporter | Global |
| cadvisor-socket-proxy | Read-only Docker API proxy | Global |

## Host Access

| Mount | Purpose | Notes |
|-------|---------|-------|
| Docker socket (via proxy) | Container discovery, names, labels, image info | `--docker` flag points to proxy socket |
| `/run/containerd/containerd.sock` | Layer resolution (snapshotter) | Direct bind mount, default path |
| `/:/rootfs:ro` | Host filesystem for disk/fs metrics | |
| `/sys:/sys:ro` | Kernel sysfs for cgroup data | |

Not mounted (accepted trade-offs):

- `/dev/kmsg` (OOM event detection, requires `--privileged`)
- `/dev/disk` (disk device metadata)

The official docs recommend mounting all of `/var/run:/var/run:ro`,
providing access to both the Docker and containerd sockets.
This setup is more restrictive: the Docker socket goes through a socket-proxy

The containerd socket is a direct bind mount as no gRPC-aware socket proxy exists.
Requires v0.54.0+ for containerd-snapshotter support.

## Socket-Proxy

Same wollomatic pattern as the logging stack's alloy-socket-proxy. cAdvisor's proxy allows
`containers/*/json`, `images/json`, `info`, `version`, and `/_ping`.
No container list endpoint, no events API, no write operations.

cAdvisor's entrypoint wrapper polls for the proxy socket before starting (`while [ ! -S ... ]`).
The socket-proxy uses `DEPLOY_VERSION` env var to force restart on every deploy (wollomatic
deletes the socket on graceful shutdown).
