# External Gateway

Public ingress with WAF protection. See [main README](../../../README.md#dual-gateways) for dual gateway architecture.

## Services

| Service | Purpose | Port |
|---------|---------|------|
| traefik | Reverse proxy + TLS termination | 80, 443 (host mode) |
| crowdsec | WAF + intrusion detection (Postgres backend) | 8085 (LAPI), 6060 (metrics) |
| init-db | Postgres bootstrap sidecar | — |

## Architecture

```text
Internet :443
     │
     ▼
┌──────────────────────────────────────────┐
│              Traefik                     │
│  ┌──────────────┐   ┌─────────────────┐  │
│  │  Middlewares │   │   Providers     │  │
│  │  - CrowdSec  │   │  - Docker/Swarm │  │
│  │  - Geoblock  │   │  - File         │  │
│  │  - Headers   │   │                 │  │
│  └──────────────┘   └─────────────────┘  │
└──────────────────────────────────────────┘
     │                        │
     │ infra_gw-external      │ infra_socket
     ▼                        ▼
 Backend Services      Socket-Proxy (VM)
```

## Middleware Chain

Requests pass through (in order):

1. **security-headers** — HSTS, CSP, X-Frame-Options
2. **geoblock** — Country-based blocking via IP2Location
3. **crowdsec** — Real-time threat blocking

## CrowdSec

- Bouncer plugin blocks malicious IPs in Traefik
- AppSec provides virtual patching
- Log acquisition reads Traefik logs via local socket-proxy (minimal permissions: CONTAINERS, INFO only)
- Postgres-backed for persistent decisions across restarts

## Geoblock Bootstrap

The IP2Location database is required for the geoblock middleware. If missing, the middleware fails and silently breaks all routes (404). The entrypoint wrapper (`config/traefik/entrypoint.sh`) auto-downloads and extracts the DB on first boot using `GEOBLOCK_IP2LOCATION_TOKEN` (env var, not Docker secret). The plugin's `databaseAutoUpdate` handles subsequent refreshes.

## Port Binding

Uses `mode: host` for ports 80/443 — bypasses Swarm ingress mesh for predictable source IP handling.

Static config via CLI flags in compose `command:`. Dynamic config via Docker Configs (file provider).
