# Registry Stack

Self-hosted Docker Registry for custom container images.

## Purpose

Hosts pre-built images for services that require custom Dockerfiles (e.g., WebFinger). Eliminates external registry dependencies and rate limits.

## Services

| Service | Image | Port |
|---------|-------|------|
| registry | registry:3.0 | 5000 (internal) |

## Access

- **URL**: `registry.DOMAIN_PRIVATE`
- **Entrypoint**: `websecure` (routed via internal gateway, LAN/Tailscale only)
- **Auth**: htpasswd (bcrypt) via Docker Config

## Image Naming Convention

```
${GLOBAL_SWARM_OCI_REGISTRY}/<stack>/<service>:<tag>

Examples:
  ${GLOBAL_SWARM_OCI_REGISTRY}/accounts/webfinger:<content-hash>
  ${GLOBAL_SWARM_OCI_REGISTRY}/jellyfin/jellyfin:<content-hash>
```

## Workflow

1. Build image locally from Dockerfile
2. Tag with registry prefix
3. Push to registry
4. Reference in stack file (remove `build:` directive)

## Consumer Stacks

| Stack | Image |
|-------|-------|
| infra/accounts | `registry.${DOMAIN_PRIVATE}/accounts/webfinger:v1` |

## Prerequisites

- `infra/gateway-internal` deployed (provides `infra_gw-internal`)

## Maintenance

Run garbage collection periodically to clean orphaned layers:

```bash
docker exec <registry_container> registry garbage-collect /etc/docker/registry/config.yml
```
