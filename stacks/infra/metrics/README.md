# Metrics Stack

Observability and monitoring services.

## Purpose

Centralized metrics collection, storage, visualization, and uptime monitoring for the entire cluster.

## Services

| Service | Purpose | Port |
|---------|---------|------|
| prometheus | Metrics collection + alerting | 9090 |
| victoriametrics | Long-term metrics storage | 8428 |
| influxdb | Time-series database | 8086 |
| grafana | Visualization + dashboards | 3009 |
| uptime-kuma | Status page + uptime monitoring | 3001 |

## Data Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Traefik   │────▶│ Prometheus  │────▶│   Grafana   │
│  (metrics)  │     │  (scrape)   │     │  (display)  │
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  Victoria   │
                    │  Metrics    │
                    │ (long-term) │
                    └─────────────┘
```

## Prometheus Scrape Targets

- `localhost:9090` — Self-monitoring
- `traefik-external:9091` — Traefik metrics (external gateway)
- Additional targets can be added for CrowdSec, node exporters

## Uptime Kuma

Provides:

- HTTP endpoint monitoring
- Docker container health (via socket-proxy)
- Status page at `status.<domain>`

Joins `infra_socket` network to access the shared socket-proxy for Docker monitoring.

## Grafana Authentication

Uses Authelia OIDC for login via browser redirect to `auth.<domain>`.

### Circular Dependency Note

This creates a **runtime** (not deployment) circular dependency:

- **Network dependency:** `infra/accounts` requires `infra_metrics` network (this stack must deploy first)
- **Auth dependency:** Grafana OIDC requires `infra/accounts` to be running

**Resolution:** Deploy in order: `metrics` → `accounts`. Grafana will start but OIDC login won't work until accounts is running. This is expected behavior — no deployment blocker.

## Prerequisites

- `infra/socket` deployed (provides `infra_socket`)
- `infra/gateway-internal` deployed (provides `infra_gw-internal`)
- `infra/accounts` deployed (Grafana OIDC)

**Primary network:** `infra_metrics` (pre-created by `swarm:init-networks`)

## Consumer Stacks

| Stack | Relationship |
|-------|--------------|
| infra/gateway-external | Prometheus scrapes Traefik + CrowdSec metrics |
| All stacks | Can expose metrics for Prometheus scraping |
