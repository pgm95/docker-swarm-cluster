# External Gateway

Uses `mode: host` for port 443 directly. No HTTP entrypoint; ACME uses DNS-01 via Cloudflare.

Static config via CLI flags in compose `command:`. Dynamic config via Docker Configs (file provider).

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

Middleware Chain, in order: security-headers -> geoblock -> crowdsec

## CrowdSec

- Bouncer plugin blocks malicious IPs in Traefik
- AppSec provides virtual patching
- Log acquisition reads Traefik swarm logs via central socket-proxy
- Postgres-backed for persistent decisions across restarts
- Wrapper entrypoint waits for Postgres overlay DNS before starting

### Self-Ban Guard

The `01-gateway-rejections` whitelist identifies requests already blocked by the
gateway's own middlewares and keeps them from feeding ban scenarios as false probing evidence.
Without it, mass rejections (e.g. a deploy-window 403 burst) ban legitimate clients.

### Decision logging

CrowdSec pushes ban decisions directly to Loki (separate from the general Alloy container log pipeline).

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

## Geoblock

The plugin owns its database lifecycle: a seed DB ships inside the plugin source
(`/plugins-storage/sources/`), auto-updates land in the `traefik-geoblock` volume,
and the newest volume DB wins on restart. Fresh volumes need no bootstrap.

`logBannedRequests` is off because the access log replaces it: geoblock stamps
country and decision headers on every request (blocked included), and the JSON
access log keeps them. Query those fields in Loki instead of a separate log stream.

## Forwarded-Header Trust

Deliberately absent everywhere (no entrypoint `trustedIPs`, no proxy protocol, no
middleware or bouncer trust lists). Nothing proxies into this gateway, so every
client-IP decision uses the unspoofable TCP peer and inbound `X-Forwarded-*` is always stripped.

## Catch-All Router

Traefik v3 entrypoint-level default middlewares only run on requests that match a router.
Unmatched requests (direct IP scans, wrong Host header) bypass the middleware chain entirely.
A low-priority catch-all router in `base.yml` (`PathPrefix(/)`, `priority: 1`, empty backend)
ensures geoblock and CrowdSec run on all traffic. Allowed unmatched requests get 503;
blocked requests get 403. This is the [officially recommended pattern](https://doc.traefik.io/traefik/getting-started/faq/#xxx-instead-of-404).

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
