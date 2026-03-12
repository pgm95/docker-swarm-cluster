# Deployment Insights

Hard-won lessons and historical deployment context.

## Test Cluster

| Node | Type | Hostname | Swarm Role | Labels |
|------|------|----------|------------|--------|
| VM | Proxmox VM | swarm-vm.home.arpa (10.50.50.179) | Manager/Leader | location=onprem, ip=private, type=vm |
| LXC | Proxmox LXC | swarm-lxc.home.arpa | Manager | location=onprem, ip=private, storage=true, gpu=true, type=lxc |
| VPS | Remote VPS | nerd1.jay-halibut.ts.net | Worker | location=cloud, ip=public, type=vps |

All nodes over Tailscale (100.88.0.x). Docker Engine 29.2.1.

## Verified Working

- Cross-node overlay (VPS to VM) over all networks — unencrypted, Tailscale handles encryption
- Both gateways discovering services via socket-proxy
- DNS: `*.DOMAIN_PRIVATE` to VM local IP (AGH), `*.DOMAIN_PUBLIC` to VPS public IP (Cloudflare)
- TLS: Both domains with valid Let's Encrypt certs (DNS-01)
- CrowdSec: Docker stdout acquisition via socket-proxy, bouncer reconnects after startup race
- Geoblock: Auto-bootstrap, country filtering active
- Registry: docker login end-to-end through Traefik TLS
- Full metrics stack (Prometheus, VictoriaMetrics, Grafana with OIDC, Uptime Kuma)
- Prometheus scraping 10 targets: both Traefiks, CrowdSec, Loki, Alloy (3x global), Node Exporter (3x global), Registry, Syncthing, self
- Global services (node-exporter, alloy) use `dockerswarm_sd_configs` for per-task discovery with hostname-based `instance` labels
- 10 Grafana dashboards provisioned via Docker Configs (5 CrowdSec, Traefik, Syncthing, Borgmatic logs, Uptime Kuma, Node Exporter)
- LDAP admin group (`app_admin`) bootstrapped, mapped to Grafana Admin and Mealie admin via OIDC
- LLDAP + Authelia OIDC chain: Traefik-external to TLS to geoblock to CrowdSec to Authelia
- Borgmatic backup and restore tested end-to-end
- Centralized logging: Loki + Alloy (global) + wollomatic socket-proxy sidecar, all 20 stacks ingested

## Key Lessons

### Rollback-Paused State Recovery

When `start-first` + `FailureAction: rollback` triggers and the rollback target is also broken, the service enters `rollback_paused` with zero running tasks. `docker service update --force` retries but rolls back to the same broken spec. Only `swarm:remove` + fresh `swarm:deploy` breaks the cycle.

### CrowdSec Silent Rollback Race

During initial deploy, `start-first` + `FailureAction: rollback` can silently revert CrowdSec to its old config. Timeline: new task can't connect to Postgres (init-db hasn't provisioned yet), exits cleanly (code 0, state `complete`), Swarm evaluates within the 5s Monitor window, sees failure, auto-rolls back. Old task continues on SQLite — deploy appears successful (1/1 replicas). Fix: `docker service update --force` after init-db converges. One-time issue on initial migration.

### Mealie v3 Healthcheck

`python -m mealie.scripts.healthcheck` was removed in mealie v3. The healthcheck returns exit 1, causing Swarm to kill healthy containers (shows as `Complete` exit 0 in `docker service ps`). Fix: `curl -f http://localhost:9025/api/app/about`.

Debugging lesson: `Complete` (exit 0) in `docker service ps` doesn't mean the container is healthy — it means the process exited cleanly. Always check actual container logs.

### Borgmatic docker exec vs s6-overlay

The `FILE__BORG_PASSPHRASE` mechanism only works for s6-managed services. Manual `docker exec` must load the passphrase explicitly from `/run/s6/container_environment/BORG_PASSPHRASE`.

### LLDAP Rootless Image Details

The `lldap/lldap:2026-01-22-debian-rootless` image runs as `lldap` (1000:1000). Lacks `nc` and `wget` — healthcheck must use `test: ["NONE"]`. Does include `curl`, `jq`, `jo` (needed by `/app/bootstrap.sh`).

### LXC IPVS — Proxmox Host Fix

Root cause of IPVS failure on unprivileged LXC: `ip_vs_rr` kernel module not loaded on the Proxmox host. Fix: `modprobe ip_vs_rr` on the host. Workaround without host access: `endpoint_mode: dnsrr`.

### PG 17 Sidecar Quirks

- `psql` defaults to database named after `PGUSER` — set `PGDATABASE=postgres` explicitly since there's no database named after the provisioner role
- `CREATE DATABASE ... OWNER` requires `SET ROLE` privilege — must `GRANT <role> TO <provisioner>` after creating each role, before creating the database (PG 16+ changed `CREATEROLE` semantics)
- Identifier quoting: postgres init script uses `%I` (identifier format) for provisioner name from env var. Sidecar scripts use `%L` (literal) for passwords only

### Registry Auth — Node Login

All nodes must resolve `DOMAIN_PRIVATE` to reach the private registry. Configure Tailscale DNS or split DNS for cloud nodes. `registry:auth` runs `docker login` on all swarm nodes using credentials from `GLOBAL_SECRETS`.

### Logging Stack Deployment - Issues Encountered

1. Alloy image tag is `v1.14.0` (with `v` prefix), not `1.14.0`
2. wollomatic defaults to non-root — needs `user: "0:0"` for docker.sock access
3. Loki 3.6+ is distroless — no shell for CMD-SHELL healthchecks. Use `loki -health`
4. Alloy (Ubuntu) has no wget/curl — healthcheck via `bash </dev/tcp/localhost/12345`
5. wollomatic has bundled `/healthcheck` binary — works with `["CMD", "/healthcheck"]`
6. Docker `/_ping` endpoint has no version prefix. Wollomatic socket-proxy allowlist must include it explicitly or Docker SDK clients log repeated warnings.
7. Unix socket connect needs write permission — alloy-proxy volume must NOT be `:ro`
8. Initial log backlog hit ingestion rate limits — bumped to 16MB/s global, 10MB/s per-stream

### Grafana Datasource UID Migration

Adding explicit `uid` to provisioned datasources that already exist with auto-generated UIDs causes `Datasource provisioning error: data source not found`. Fix: add a one-time `deleteDatasources` block to `datasource.yml` to remove old entries before re-provisioning with new UIDs. Remove the block after successful deploy.

### CrowdSec Prometheus Bind Address

CrowdSec's Prometheus metrics default to `127.0.0.1:6060` — unreachable over Docker overlay networks. Must add `prometheus.listen_addr: 0.0.0.0` in `config.yaml` for cross-stack scraping.

### Syncthing API Key via Env Var

`STGUIAPIKEY` is a runtime CLI override (`--gui-apikey`), not a config file setting. It does NOT affect `syncthing generate` — only takes effect when `syncthing serve` runs. Set it as a compose env var for deterministic API key control. The `/metrics` endpoint requires the same API key via `X-API-Key` header or Bearer token.

### Registry Prometheus Metrics

Registry v3 has a debug server (`:5001`) enabled by default for healthchecks. Prometheus metrics require `REGISTRY_HTTP_DEBUG_PROMETHEUS_ENABLED=true`. The debug address must bind to `0.0.0.0:5001` (not localhost) for overlay access — set via `REGISTRY_HTTP_DEBUG_ADDR`.

### Grafana Bleve Index Lock (stop-first Required)

Grafana 12.x uses a bleve search index in the data volume. With `start-first`, the new task crashes with "index is locked by another process" because both old and new tasks try to hold the lock simultaneously. Grafana must use `*deploy-stop-first`.

### Prometheus dockerswarm_sd_configs — Port Fallback

When tasks have no published ports (overlay-only services), `dockerswarm_sd_configs` sets `__address__` to the task's overlay IP + the `port` parameter from the SD config. Verified in Prometheus source (`discovery/moby/tasks.go`). Tasks on multiple networks generate one target per network — filter with `__meta_dockerswarm_network_name` to avoid duplicates.
