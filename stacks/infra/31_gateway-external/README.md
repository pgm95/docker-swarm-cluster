# External Gateway

Public ingress with WAF protection. See [main README](../../../README.md#dual-gateways) for dual gateway architecture.

## Services

| Service | Purpose | Port |
|---------|---------|------|
| traefik | Reverse proxy + TLS termination | 443 (host mode) |
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
- Wrapper entrypoint waits for Postgres overlay DNS before starting (survives `stop-first` redeploys)

### Decision logging

CrowdSec pushes ban decisions directly to Loki — this is separate from the general container log pipeline (Alloy).

```text
CrowdSec decision
     │ notifications-http.yaml (Go template)
     │ HTTP POST to loki:3100/loki/api/v1/push
     ▼
   Loki ──► Grafana ("Crowdsec Cyber Threat Insights" dashboard)
```

The notification plugin (`http_loki`) fires on every ban from all three profiles (appsec, IP, range). Each push includes:

- **Stream labels**: `job=crowdsec`, `instance=<host>`
- **Structured metadata**: `country`, `ip`, `scenario`, `type`, `duration`, `asname`, `asnumber`, `latitude`, `longitude`, `iprange`, `scope`
- **Log line**: human-readable summary (`{type} {ip} {scenario} {country}`)

The Grafana dashboard uses LogQL `count_over_time` with `| keep` stages for aggregation panels (summary table, country pie chart, geo map) and raw log queries for the realtime table.

### Debugging

Middleware chain failures are silent. If any middleware in the entrypoint's default chain
fails to initialize (missing database, broken config), Traefik cannot create ANY routers on
that entrypoint. Symptom: 404 for all routes, not an error page. Check `docker service logs`
for the actual error.

## Catch-All Router

Traefik v3 entrypoint-level default middlewares only run on requests that match a router.
Unmatched requests (direct IP scans, wrong Host header) bypass the middleware chain entirely.
A low-priority catch-all router in `base.yml` (`PathPrefix(/)`, `priority: 1`, empty backend)
ensures geoblock and CrowdSec run on all traffic. Allowed unmatched requests get 503;
blocked requests get 403. This is the [officially recommended pattern](https://doc.traefik.io/traefik/getting-started/faq/).

## Geoblock Bootstrap

The IP2Location database is required for the geoblock middleware. If missing, the middleware fails and silently breaks all routes (404). The entrypoint wrapper (`config/traefik/entrypoint.sh`) auto-downloads and extracts the DB on first boot using `GEOBLOCK_IP2LOCATION_TOKEN` (env var, not Docker secret). The plugin's `databaseAutoUpdate` handles subsequent refreshes.

## Port Binding

Uses `mode: host` for port 443 — bypasses Swarm ingress mesh for predictable source IP handling. No HTTP entrypoint; ACME uses DNS-01 via Cloudflare.

Static config via CLI flags in compose `command:`. Dynamic config via Docker Configs (file provider).

## Dual-Scope Services and Phantom Routers

Traefik's `--providers.swarm.constraints` filters at the service level, not the router level.
Once a Swarm service passes the constraint check, all its `traefik.*` labels are processed,
including routers meant for the other gateway.

This only affects services that set both `traefik.scope.internal=true` and `traefik.scope.external=true`.
Each gateway creates phantom routers from the other gateway's labels.
Phantoms are inert (entrypoint-level wildcard `tls.domains` cause a cert mismatch,
and DNS doesn't route to the wrong gateway), but they appear on the dashboard.

This is a known Traefik limitation ([#2009](https://github.com/traefik/traefik/issues/2009),
[#11909](https://github.com/traefik/traefik/issues/11909)).
