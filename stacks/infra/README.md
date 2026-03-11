# Infra Namespace

Core infrastructure stacks that support the cluster.

## Stacks

| Stack | Purpose | Placement | Primary Network |
|-------|---------|-----------|-----------------|
| [socket](00_socket/) | Socket-proxy for Docker API | Manager + `*place-vm` | `infra_socket` |
| [postgres](10_postgres/) | Central PostgreSQL database | `*place-storage` | `infra_postgres` |
| [backup](20_backup/) | Borgmatic pg_dump backups (BorgBackup) | `*place-storage` | `infra_postgres` |
| [gateway-internal](30_gateway-internal/) | Internal Traefik (*.DOMAIN_PRIVATE) | `*place-vm` | `infra_gw-internal` |
| [gateway-external](31_gateway-external/) | External Traefik + CrowdSec (*.DOMAIN_PUBLIC) | `*place-cloud` | `infra_gw-external`, `infra_postgres` |
| [metrics](40_metrics/) | Prometheus, VictoriaMetrics, Grafana, Uptime Kuma | `*place-vm` | `infra_metrics`, `infra_postgres` |
| [registry](50_registry/) | Docker image hosting | `*place-vm` | `infra_gw-internal` |
| [accounts](60_accounts/) | Authentication (Authelia + LLDAP) | `*place-vm` | `infra_postgres` |

## Deploy Order

Overlay networks are pre-created by `swarm:init-networks` (runs automatically via `site:deploy-infra`).
Deploy order is determined by the `NN_` folder prefix — `site:deploy-infra` discovers and sorts automatically.

## Network Topology

Security-segregated networks, pre-created by `swarm:init-networks`:

| Network | Primary Stack | Purpose |
|---------|-------|---------|
| `infra_socket` | socket | Docker API access via socket-proxy |
| `infra_gw-internal` | gateway-internal | Internal Traefik routing (LAN/Tailscale) |
| `infra_gw-external` | gateway-external | External Traefik routing (public internet) |
| `infra_metrics` | metrics | Prometheus scraping and monitoring |
| `infra_postgres` | postgres | Central Postgres database access |

## Secrets

Stacks with sensitive config have a `secrets.env` file (SOPS-encrypted). Edit with `mise run sops:edit <path>`.
