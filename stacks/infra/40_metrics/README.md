# Metrics Stack

Observability, monitoring, and status page services.

## Services

| Service | Purpose | Port |
|---------|---------|------|
| prometheus | Metrics collection + alerting | 9090 |
| victoriametrics | Long-term metrics storage | 8428 |
| grafana | Dashboards + visualization (Postgres-backed) | 3000 |
| uptime-kuma | Status monitoring + uptime checks (SQLite) | 3001 |
| init-db | Postgres database provisioner sidecar | - |

## Data Flow

```
Traefik / exporters
       |
       v
  Prometheus  ------>  VictoriaMetrics
  (scrape)             (long-term storage)
       |
       v
    Grafana
  (dashboards)

  Uptime Kuma
  (status checks)
```

Prometheus scrapes targets on the `infra_metrics` overlay, then remote-writes to VictoriaMetrics for retention. Grafana queries both as datasources. Uptime Kuma runs independent status checks.

## Volume Ownership

VictoriaMetrics uses an entrypoint wrapper (`/init.sh` via Docker Config) that chowns `/storage` on first run, then drops privileges via `setpriv` before exec'ing the stock binary.

Grafana runs as UID 472 (image default). Uptime Kuma uses the rootless image (UID 1000). Neither needs an entrypoint wrapper.

## Prerequisites

- `infra/socket` deployed (provides `infra_socket` for Uptime Kuma Docker monitoring)
- `infra/gateway-internal` deployed (provides `infra_gw-internal`)
- `infra/postgres` deployed (provides `infra_postgres` for Grafana database)
- `infra/accounts` deployed (provides OIDC for Grafana authentication)

**Primary network:** `infra_metrics` (pre-created by `swarm:init-networks`)

## Post-Deploy

1. **Uptime Kuma** -- navigate to `status.DOMAIN_PRIVATE`, create admin account
2. **Docker host** -- in Uptime Kuma UI: Settings > Docker Hosts, add `tcp://socket-proxy:2375`
3. **Prometheus scraping** -- in Uptime Kuma UI: Settings > API Keys, create key, uncomment scrape target in `prometheus.yml`, redeploy

## Consumer Stacks

| Stack | Relationship |
|-------|--------------|
| infra/gateway-external | Prometheus scrapes Traefik + CrowdSec metrics |
| All stacks | Can expose metrics for Prometheus scraping |
