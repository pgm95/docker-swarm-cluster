# Socket Stack

Socket-proxy for Swarm API access.

## Purpose

Provides the socket-proxy that Traefik and monitoring tools use for Docker/Swarm service discovery. This stack deploys first and is the primary consumer of the `infra_socket` network (pre-created by `swarm:init-networks`).

## Services

| Service | Purpose |
|---------|---------|
| socket-proxy | Read-only Docker/Swarm API proxy (tecnativa/docker-socket-proxy) |

## Network

| Network | Purpose | Properties |
|---------|---------|------------|
| `infra_socket` | Socket-proxy access | Internal (no egress), pre-created by bootstrap |

## Socket-Proxy

Provides Traefik with read-only access to the Swarm API for dynamic service discovery. Uses endpoint-level env var permissions (`SERVICES=1`, `TASKS=1`, etc.) on the proxy itself.

**Why on managers only**: The Swarm API (services, tasks, networks) is only available on manager nodes. Worker nodes (like VPS) cannot query Swarm state directly.

## Permissions Model

tecnativa/docker-socket-proxy provides endpoint-level permissions via environment variables. All services on the `infra_socket` overlay get the same permissions. Current consumers (gateway-internal, gateway-external, metrics) all need the same read-only endpoints — no security concern.

If per-consumer granularity becomes required: deploy sidecar socket-proxy instances per consumer. Not worth the overhead for 3 consumers with identical needs.

## Consumer Stacks

| Stack | Purpose |
|-------|---------|
| infra/gateway-external | Traefik service discovery |
| infra/gateway-internal | Traefik service discovery |
| infra/metrics | Uptime-Kuma Docker monitoring |

## Deployment

```bash
mise run swarm:deploy stacks/infra/00_socket
```
