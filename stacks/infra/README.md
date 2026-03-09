# Infra Namespace

Core infrastructure stacks that support the cluster.

## Stacks

| Stack | Purpose | Placement | Primary Network |
|-------|---------|-----------|-----------------|
| [socket](socket/) | Socket-proxy for Docker API | Manager + `*place-vm` | `infra_socket` |
| [postgres](postgres/) | Central PostgreSQL database | `*place-storage` | `infra_postgres` |
| [gateway-internal](gateway-internal/) | Internal Traefik (*.DOMAIN_PRIVATE) | `*place-vm` | `infra_gw-internal` |
| [gateway-external](gateway-external/) | External Traefik + CrowdSec (*.DOMAIN_PUBLIC) | `*place-cloud` | `infra_gw-external`, `infra_postgres` |
| [metrics](metrics/) | Prometheus, VictoriaMetrics | `*place-vm` | `infra_metrics` |
| [registry](registry/) | Docker image hosting | `*place-vm` | `infra_gw-internal` |
| [accounts](accounts/) | Authentication (Authelia + LLDAP) | `*place-vm` | `infra_postgres` |

## Deploy Order

All overlay networks are pre-created by `swarm:init-networks` (runs automatically via `site:deploy-infra`).

```text
0. swarm:init-networks  # Auto-run by site:deploy-infra
1. socket
2. postgres
3. gateway-internal
4. gateway-external
5. metrics
6. registry
7. accounts
```

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
