# Metrics Stack

Metrics collection, storage, visualization, and uptime monitoring.

## Services

| Service | Purpose | Mode |
|---------|---------|------|
| prometheus | Scrapes targets, stores TSDB, evaluates recording rules | Replicated (1) |
| node-exporter | Host-level metrics (CPU, memory, disk, network) | Global |
| grafana | Dashboards and visualization (OIDC auth) | Replicated (1) |
| init-db | Provisions Grafana's Postgres database | Replicated (1) |

## Scraping Global Services

Replicated services have a single Swarm VIP — `static_configs` with the service DNS name works
fine for single-instance services.

Global services run one task per node, each producing distinct per-host metrics. VIP
load-balances to a random task, so `static_configs` would scrape only one node per interval.

### `dockerswarm_sd_configs` (not `dns_sd_configs`)

Global services use Prometheus's Swarm service discovery (`dockerswarm_sd_configs` with
`role: tasks`) instead of `dns_sd_configs`. Both discover all task IPs, but Swarm SD provides
node metadata (`__meta_dockerswarm_node_hostname`) that enables stable `instance` labels.

With `dns_sd_configs`, `instance` labels are overlay IPs (e.g., `10.0.3.125:9100`). These
change on every service update, breaking time series continuity. `dockerswarm_sd_configs`
lets us relabel `instance` to the node hostname before scraping — the label survives
redeployments.

Prometheus reaches the Docker API via the central socket-proxy on `infra_socket`. The
socket-proxy runs on the manager and serves Swarm-wide data (`/nodes`, `/tasks`,
`/services`) — no per-node API access needed.

### `port` parameter and unpublished services

Neither node-exporter nor alloy publish ports — they're only reachable via overlay. Prometheus
Swarm SD handles this: when a task has no published ports, `__address__` is set to the task's
overlay IP + the `port` fallback from the SD config (verified in Prometheus source:
`discovery/moby/tasks.go`).

### Network filter

Swarm SD generates one target **per network attachment**. Services on multiple networks produce
duplicate targets. The `relabel_configs` filter on `__meta_dockerswarm_network_name` ensures
exactly one target per task.

### Service name format

Swarm prefixes service names with the stack name: `metrics_node-exporter`, `cadvisor_cadvisor`,
`logging_alloy`. The `relabel_configs` filter on `__meta_dockerswarm_service_name` must use the
full prefixed name.

## Grafana

Uses `stop-first` update order. Grafana's bleve search index (in `grafana-data` volume) requires
exclusive access — `start-first` starts a new task before stopping the old one, and the new
task crashes with "index is locked by another process" when both try to hold the lock
simultaneously.

## Node Exporter

Containerized global service with bind-mounted host paths (`/proc`, `/sys`, `/` all `:ro`).
On LXC nodes, bind-mounted `/proc` correctly reflects the LXC's cgroup-scoped view (cgroup memory
limits, allocated cores, visible block devices), not the Proxmox host.

### Swarm Limitations

Swarm does not support `pid: host`, `privileged`, or `cap_add`:

| Lost | Reason |
|------|--------|
| Process count/states | No host PID namespace |
| `systemd` collector | No D-Bus socket access |
| `perf` collector | No `CAP_PERFMON` (irrelevant — virtualized PMCs unreliable on VMs) |
| `node_uname_info.nodename` | Container ID, not hostname (own UTS namespace) |

Collectors `hwmon`, `bcache`, `infiniband` are disabled — bare-metal only. Filesystem
exclusion flags filter Docker overlay mounts, `tmpfs`, `nsfs`, `tracefs`, and `cgroup2`.

### Recording Rules

All rules filter on `{job="node"}` — the Prometheus scrape job name must match. Network
rules exclude `lo|docker.*|veth.*|vx-.*|br-.*` to avoid summing Docker virtual interfaces
with physical traffic. Remaining interfaces are physical NICs and Tailscale.
Omitted: `vmstat` (partially virtualized `/proc/vmstat` on LXC), per-device disk IO (no
aggregation over raw metrics), network drops (inflated by virtual interfaces).
