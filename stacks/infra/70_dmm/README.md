# Device Mapping Manager (dmm)

Grants Swarm containers cgroup access to the host devices they bind mount.
On this cluster that means `/dev/dri` for GPU transcoding and ML inference.
Locally cloned and built from [mels0n/device-mapping-manager](https://github.com/mels0n/device-mapping-manager).

## Why this stack exists

Swarm lacks support for passing devices to services. Bind mounting the device node via `volumes:` makes it visible but not usable.
Under cgroup v2, the per-container device controller (an eBPF program the runtime installs from the OCI spec) denies the node even for root.
A daemon-level allow does not fix it, because the container's own leaf cgroup still denies.

## How it works

The manager watches the Docker socket for container starts, scans already-running containers on its own startup, and for every mount sourced under `/dev` writes an allow rule into that container's cgroup.
A container therefore gets access only to the devices it explicitly bind mounts.

The manager must run privileged with the host PID and cgroup namespaces to write device rules into other containers' cgroups, so the manager itself cannot be a Swarm service.

Accordingly, the stack deploys a "launcher" service, which is simply a `docker:cli` task with access to the node's Docker socket.
Through the socket it starts the privileged manager as a sibling container on the same daemon, stays attached to forward its logs to the cluster log driver, and removes it on shutdown.
This keeps a must-be-privileged container scheduled and version-managed by the normal pipeline while the Swarm-visible task stays "unprivileged".

## Consumers

Opt in by bind mounting the device node. No per-consumer configuration:

```yaml
volumes:
  - /dev/dri/renderD128:/dev/dri/renderD128
  - /dev/dri/card0:/dev/dri/card0
```

## Security notes

- Neither container has internet access:
  - The manager itself runs with `--network none`
  - The launcher uses an `internal` overlay, which drops the gateway and denies egress.
- The manager is privileged and mounts host `/sys`, the Docker socket, and the system DBus socket.
- The launcher additionally mounts the node's Docker credentials read-only so its CLI can authenticate the image pull with the cluster's registry.

## Operational notes

- Scheduled only on GPU nodes via `*place-gpu`.
- GPU nodes must be logged in to the private registry.
- Removing the stack stops the launcher, whose cleanup removes the manager container.
- A hard kill leaves the manager running until the next deploy replaces it.
