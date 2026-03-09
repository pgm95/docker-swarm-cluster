# Metrics Stack

Observability and monitoring services.

## Purpose

Centralized metrics collection and long-term storage for the entire cluster.

## Services

| Service | Purpose | Port |
|---------|---------|------|
| prometheus | Metrics collection + alerting | 9090 |
| victoriametrics | Long-term metrics storage | 8428 |

Additional services (grafana, uptime-kuma, influxdb) are defined in compose but currently disabled.

## Data Flow

```
Traefik / exporters
       |
       v
  Prometheus  ------>  VictoriaMetrics
  (scrape)             (long-term storage)
```

Prometheus scrapes targets on the `infra_metrics` overlay, then remote-writes to VictoriaMetrics for retention.

## Volume Ownership

VictoriaMetrics uses an entrypoint wrapper (`/init.sh` via Docker Config) that chowns `/storage` on first run, then drops privileges via `setpriv` before exec'ing the stock binary.

## Prerequisites

- `infra/socket` deployed (provides `infra_socket` — declared but reserved for future consumers)
- `infra/gateway-internal` deployed (provides `infra_gw-internal`)

**Primary network:** `infra_metrics` (pre-created by `swarm:init-networks`)

## Consumer Stacks

| Stack | Relationship |
|-------|--------------|
| infra/gateway-external | Prometheus scrapes Traefik + CrowdSec metrics |
| All stacks | Can expose metrics for Prometheus scraping |
