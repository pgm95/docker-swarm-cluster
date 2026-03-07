# External Gateway Stack

External ingress gateway with WAF protection.

## Purpose

Handles all public-facing traffic. Traefik terminates TLS and routes requests to backend services. CrowdSec provides intrusion detection and blocking.

## Services

| Service | Purpose | Port |
|---------|---------|------|
| traefik | Reverse proxy + TLS termination | 80, 443 (host mode) |
| crowdsec | WAF + intrusion detection (Postgres backend) | 8085 (LAPI), 6060 (metrics) |
| init-db | Postgres bootstrap sidecar — creates `crowdsec` role/database | — |

## Architecture

```
Internet :443
     │
     ▼
┌─────────────────────────────────────────┐
│              Traefik                     │
│  ┌─────────────┐   ┌─────────────────┐  │
│  │  Middlewares │   │   Providers    │  │
│  │  - CrowdSec  │   │  - Docker/Swarm│  │
│  │  - Geoblock  │   │  - File        │  │
│  │  - Headers   │   │                │  │
│  └─────────────┘   └─────────────────┘  │
└─────────────────────────────────────────┘
     │                        │
     │ infra_gw-external      │ infra_socket
     ▼                        ▼
 Backend Services      Socket-Proxy (VM)
```

## Traefik Service Discovery

Traefik uses two providers:

| Provider | Purpose |
|----------|---------|
| Docker (swarmMode) | Discovers Swarm services via labels |
| File | Static routes for LAN hosts (jellyfin, hass, etc.) |

The Swarm provider is scoped with a constraint: only services with `traefik.scope.external=true` are discovered. This prevents the external gateway from attempting to route internal-only services, eliminating `Could not find network` and `EntryPoint doesn't exist` log noise.

Connects to shared socket-proxy on `infra/socket` via `infra_socket` network.

## CrowdSec Integration

- **Bouncer plugin** in Traefik blocks malicious IPs in real-time
- **AppSec** provides virtual patching against known vulnerabilities
- **Log acquisition** reads Traefik container logs via local socket-proxy

The local socket-proxy has minimal permissions (CONTAINERS, INFO only) — just enough for log access.

## Middleware Chain

Requests pass through (in order):

1. **security-headers** — HSTS, CSP, X-Frame-Options
2. **geoblock** — Country-based blocking via IP2Location (auto-bootstrapped on first boot)
3. **crowdsec** — Real-time threat blocking

## Geoblock Bootstrap

The geoblock plugin requires an IP2Location BIN database. If the database is missing on first boot, the middleware fails and silently breaks all routes (404). The entrypoint wrapper (`entrypoint.sh`) handles this:

1. Checks if `IP2LOCATION-LITE-DB1.IPV6.BIN` exists in `/data/geoblock/`
2. If missing: installs `unzip`, downloads ZIP from IP2Location API, extracts BIN
3. Chains into Traefik's stock `/entrypoint.sh`

The download uses `GEOBLOCK_IP2LOCATION_TOKEN` (env var, not Docker secret). The plugin's `databaseAutoUpdate` handles subsequent monthly refreshes. Bootstrap is non-fatal — on failure, Traefik starts anyway (geoblock degrades but other routes still work if the middleware chain can initialize without it).

## Port Binding

Uses `mode: host` for ports 80/443 to bind directly to the VPS public IP. Swarm's ingress routing mesh is bypassed for predictable source IP handling.

## Prerequisites

- `infra/socket` deployed (provides `infra_socket`)
- `infra/postgres` deployed and healthy (provides `infra_postgres`) — CrowdSec stores decisions in central Postgres
- `infra/metrics` deployed (provides `infra_metrics`) — or deploy metrics after this stack
- Host-level Tailscale for control plane connectivity
- VPS node labels: `location=cloud`, `ip=public`

**Primary network:** `infra_gw-external` (pre-created by `swarm:init-networks`)

## Configuration Files

| File | Purpose |
|------|---------|
| `config/traefik/entrypoint.sh` | Bootstrap wrapper — downloads geoblock DB on first boot, then chains into Traefik |
| `config/traefik/static/traefik.yml` | Providers, entrypoints, ACME |
| `config/traefik/dynamic/*.yml` | Routes, middlewares, services |
| `config/crowdsec/config.yaml` | LAPI + Postgres connection config (`.local` overlay) |
| `config/crowdsec/acquis.yaml` | Log acquisition sources |
| `config/crowdsec/profiles.yaml` | Alert remediation rules |
| `config/bootstrap/init-db.sh` | Postgres sidecar — creates role, database, schema grants |
