# cAdvisor Stack

| Service | Purpose | Mode |
|---------|---------|------|
| cadvisor | Container metrics exporter | Global |

## Host Access

| Mount | Purpose | Notes |
|-------|---------|-------|
| `/var/run/docker.sock` | Container discovery, names, labels, image info | Read only, `--docker` flag points at it |
| `/run/containerd/containerd.sock` | Required for the Docker factory to initialize | Read only, default path. Without it the Docker factory fails to register and cadvisor falls back to the raw factory, losing all container names and labels |
| `/:/rootfs:ro` | Host machine info (filesystem inventory, machine id) | |
| `/sys:/sys:ro` | Kernel sysfs for cgroup data | |

Not mounted (accepted trade-offs):

- `/dev/kmsg` (OOM event detection, requires `--privileged`)
- `/dev/disk` (disk device metadata)

The official docs recommend mounting all of `/var/run:/var/run:ro`. This setup is narrower: only
the Docker and containerd sockets are bound, both read only. Requires v0.54.0+ for
containerd-snapshotter support.

## Disabled metrics

`disk` (per container filesystem usage) is disabled via `--disable_metrics`. cadvisor reads
container layers at the data root path the Docker daemon reports, but that root is not the default
and is not uniform across the cluster, so those paths are absent inside the container and every
housekeeping cycle logged a filesystem stat error. Block IO (`diskIO`) is unaffected and stays
enabled; host level disk usage is covered by node-exporter.
