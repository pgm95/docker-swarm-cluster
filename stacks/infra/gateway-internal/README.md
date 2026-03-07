# Internal Gateway Stack

Internal Traefik gateway for LAN/Tailscale access to `.DOMAIN_PRIVATE` services.

## Dual Gateway Architecture

The infrastructure uses two separate Traefik instances to serve different access patterns and security requirements.

### External Gateway (infra/gateway-external)

The external gateway runs on a VPS with a public IP and handles all internet-facing traffic. It serves the `.DOMAIN_PUBLIC` domain and applies aggressive security measures including CrowdSec threat detection and geographic blocking. This gateway is the only entry point from the public internet, ensuring all external requests pass through hardened security middleware before reaching backend services.

### Internal Gateway (infra/gateway-internal)

This gateway runs on the on-premises VM and serves the `.DOMAIN_PRIVATE` domain exclusively to LAN and Tailscale clients. Since traffic originates from trusted networks, it operates with minimal middleware—only security headers are applied. Services that should never be internet-accessible (monitoring dashboards, internal registries) route exclusively through this gateway.

### Why Two Gateways

A single gateway cannot efficiently serve both use cases. External traffic requires security scanning that adds latency unnecessary for trusted LAN clients. Internal services need isolation from the internet entirely—not just authentication, but complete network separation. The dual gateway model provides defense in depth: even if the external gateway is compromised, internal-only services remain unreachable.

### Scope-Based Discovery

Each gateway's Swarm provider is constrained to only discover services that opt in via scope labels. The internal gateway requires `traefik.scope.internal=true`; the external gateway requires `traefik.scope.external=true`. Services on both gateways (e.g. jellyfin) set both labels. This eliminates cross-gateway log noise from services the gateway can't route.

### Entrypoint Naming

Both gateways use a unified entrypoint name (`websecure`) on `:443`. Routing correctness is enforced by Host rules (different domains) and DNS (each domain resolves to its gateway's node). Each gateway keeps its own cert resolver, middleware chain, and provider constraints. This allows dual-gateway services like jellyfin to define multiple routers with different Host rules without cross-gateway entrypoint errors.

### Certificate Strategy

Both gateways obtain wildcard certificates from Let's Encrypt using Cloudflare DNS challenge. This approach works regardless of network accessibility—the internal gateway can obtain certificates for `.DOMAIN_PRIVATE` without exposing any ports to the internet. Each gateway maintains its own certificate storage to avoid conflicts.

### DNS Configuration

Split DNS ensures proper routing. Public DNS resolves `.DOMAIN_PUBLIC` to the VPS public IP. Internal DNS (AdGuard Home or Tailscale MagicDNS) resolves `.DOMAIN_PRIVATE` to the VM's LAN IP. The private domain has no public DNS records, making it unreachable from the internet even if someone discovers the domain name.

## Services Routed

| Service | URL | Source |
| --------- | ----- | -------- |
| Grafana | `grafana.DOMAIN_PRIVATE` | Docker labels |
| Uptime-Kuma | `status.DOMAIN_PRIVATE` | Docker labels |
| Registry | `registry.${DOMAIN_PRIVATE}` | Docker labels |
| Jellyfin | `stream.DOMAIN_PRIVATE` | Docker labels |
| Home Assistant | `hass.DOMAIN_PRIVATE` | File provider |

Jellyfin and Home Assistant are also accessible via the external gateway at their `.DOMAIN_PUBLIC` addresses, providing remote access with full security middleware.

## Prerequisites

1. `infra/socket` deployed (provides `infra_socket`)
2. `infra/metrics` deployed (provides `infra_metrics`) — or deploy metrics after this stack
3. VM node labeled: `location=onprem`
4. Internal DNS configured for `*.DOMAIN_PRIVATE`
5. Cloudflare API token with DNS edit permissions

**Primary network:** `infra_gw-internal` (pre-created by `swarm:init-networks`)

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DOMAIN_PRIVATE` | Internal domain |
| `CLOUDFLARE_DNS_API_TOKEN_INTERNAL` | API token for DNS challenge |

## Files

```text
stacks/infra/gateway-internal/
├── compose.yml              # Stack definition
├── config/traefik/
│   ├── static/traefik.yml        # Entrypoints, providers, resolvers
│   └── dynamic/base.yml          # Middlewares, file-based routes
├── data/traefik/
│   ├── certs/                    # ACME certificates (gitignored)
│   └── logs/                     # Access logs
└── README.md
```
