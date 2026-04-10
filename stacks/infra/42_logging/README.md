# Logging Stack

Centralized log aggregation with Loki and Alloy.

## Services

| Service | Purpose | Mode |
|---------|---------|------|
| loki | Log storage and query engine | Replicated (1) |
| alloy | Container log collector | Global |
| alloy-socket-proxy | Per-node Docker API proxy for Alloy | Global |

## Architecture

```text
Each node:                                          Storage node:
┌──────────────────────────────────┐                ┌──────────────┐
│  alloy-socket-proxy (global)     │                │  loki         │
│    /var/run/docker.sock:ro       │                │  :3100        │
│    ↓ writes Unix socket          │                │  loki-data:/  │
│  ┌───────────────────┐           │                └──────▲───────┘
│  │ alloy-proxy volume│ ← shared  │                       │
│  └───────────────────┘           │                       │
│    ↑ reads Unix socket           │                       │
│  alloy (global)                  │── infra_metrics ──────┘
│    tails local container logs    │
└──────────────────────────────────┘
```

## Socket-Proxy Sidecar Pattern

Alloy needs each node's Docker socket to tail local container logs (`/containers/{id}/logs` is
node-local). A centralized socket-proxy can't serve this — it only proxies the manager's daemon.

Instead of bind-mounting `docker.sock` directly into Alloy, a dedicated
[wollomatic/socket-proxy](https://github.com/wollomatic/socket-proxy) runs as a global sidecar.
It reads the local Docker socket and exposes a filtered Unix socket in a shared named volume
(`alloy-proxy`). Alloy mounts the same volume read-write (Unix socket connect requires write
permission).

**Why this works**: Swarm named volumes are node-local by name. Two global services referencing
the same volume on the same node share the same filesystem — communication is guaranteed
node-local without overlay routing.

**Why wollomatic over Tecnativa**: wollomatic/socket-proxy supports `-proxysocketendpoint` to
listen on a Unix socket file instead of TCP. Tecnativa only supports TCP, which would require
overlay networking and lose locality guarantees.

### API Surface

The proxy allows only `GET` requests matching container, events, networks, version, and info
endpoints, plus `GET`/`HEAD` for `/_ping` (Docker client connectivity check, no version prefix).
No Swarm API (`/services`, `/tasks`, `/nodes`), no write operations.

The proxy must run as root (`user: "0:0"`) to access the Docker socket — wollomatic defaults to
non-root.

### Distroless / Scratch Image Constraints

Three of the images lack standard CLI tools:

| Image | Base | Shell | Tools | Healthcheck |
|-------|------|-------|-------|-------------|
| wollomatic/socket-proxy | scratch | None | `/healthcheck` binary | `["CMD", "/healthcheck"]` (probes :55555/health) |
| grafana/loki | distroless | None | None | `loki -health` (built-in, added in 3.6.x) |
| grafana/alloy | Ubuntu 24.04 | bash | No wget/curl | `bash </dev/tcp/127.0.0.1/12345` |

## Startup Ordering

Swarm has no `depends_on`. Alloy's entrypoint wrapper (`entrypoint.sh`) polls for the Unix
socket file before starting:

```sh
while [ ! -S /alloy-proxy/docker.sock ]; do sleep 1; done
```

On a normal deploy, the socket-proxy creates the socket within seconds. Alloy's wait loop
handles the race without crashing.

## Alloy Reconnection

When the socket-proxy restarts, the Unix socket is removed and recreated. Alloy's
`loki.source.docker` has known issues with reconnection after socket disconnection (upstream
issues [#691](https://github.com/grafana/alloy/issues/691),
[#3054](https://github.com/grafana/alloy/issues/3054)). Alloy's healthcheck + Swarm restart
policy handles this — if Alloy enters a degraded state, Swarm restarts it with a clean
connection.

## Node Label

Alloy tags every log stream with a `node` label via `external_labels`. Inside a container,
`HOSTNAME` resolves to the container ID (Swarm gives each task its own UTS namespace), so the
compose file injects `NODE_HOSTNAME` using Swarm's `{{.Node.Hostname}}` template. This resolves
at task scheduling time to the actual node hostname (`swarm-vm`, `swarm-lxc`, etc.), matching the
`instance` label that Prometheus Swarm SD produces for cAdvisor and node-exporter.

Direct Loki pushers (e.g., CrowdSec HTTP notifications) define their own label schemas and are
unaffected.

## Loki Storage

Single-node monolithic mode with filesystem backend (TSDB + chunks on a named volume). No
Postgres, no S3. 30-day retention via compactor. Suitable for homelab scale — upgrade to S3 or
central Postgres if retention or query performance becomes an issue.
