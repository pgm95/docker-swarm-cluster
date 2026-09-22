# External Gateway

Sits behind the relay VPS (see the main README, Public Ingress Relay). Publishes host port
8443 on the gateway node; the relay forwards public 443 to it with a PROXY protocol v2
header. No HTTP entrypoint; ACME uses DNS-01 via Cloudflare.

Static config via CLI flags in compose `command:`. Dynamic config via Docker Configs (file provider).

## Architecture

```text
Internet :443
     │
     ▼
 Relay VPS (HAProxy, PROXY v2)
     │ direct WAN DNAT :8443, WireGuard tunnel as backup
     ▼
┌──────────────────────────────────────────┐
│              Traefik :8443               │
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
- The bouncer key, the CTI key, the database password and the widget agent password
  arrive as Docker secrets. The wrapper reads them from `/run/secrets/` and exports
  them for the stock entrypoint, so none of them appears in the service spec or on the
  node's disk. The Traefik bouncer plugin reads its key through its file option from the
  same mounted secret
- A dedicated LAPI machine for the homepage widget is registered on every container
  start through the image's `AGENT_USERNAME` / `AGENT_PASSWORD` path (idempotent
  `cscli machines add --force`, no credentials file written). The password is a key in
  the `widget` topic file, mounted by this stack and by Homepage. The container's own
  `localhost` agent is disposable: the entrypoint regenerates it with a random password
  whenever its credentials file and the machine table disagree, so no external consumer
  may borrow it. LAPI has no read-only machine type and bouncer keys cannot read alerts,
  so the widget credential is write-capable by necessity

### Operating cscli inside the container

A shell started with `docker exec` does not run under the wrapper, so the variables it
exported are absent and any `cscli` command that touches the database (machines,
bouncers, decisions) fails to connect. Read the value from `/run/secrets/` as root
(`docker exec -u 0`; PID 1's environment is not readable from an exec shell) and export it
into the shell, then run `cscli`. This is the accepted
cost of keeping the database password out of the service spec and off the node's disk.

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

The gateway runs on prem behind the relay VPS, which owns public 443 and forwards each
client as its own TCP connection with a PROXY protocol v2 header, directly over the WAN
(the router DNATs 8443 from the relay's address, source preserved) or over the WireGuard
tunnel as fallback. The `websecure` entrypoint accepts that header from exactly those two
sources (`proxyProtocol.trustedIPs`); every other peer keeps its TCP source address. The
client's own TLS session runs end to end, the relay never terminates it.

Nothing else is trusted: no `forwardedHeaders.trustedIPs`, no middleware or bouncer trust
lists, so inbound `X-Forwarded-*` is always stripped and every client-IP decision (bouncer,
geoblock, access log) uses the address the PROXY header delivered.

## Memory Limit

Traefik runs with `*resources-huge` and `GOMEMLIMIT` set to 90 % of that limit. Go's
collector paces itself against heap growth, not against the cgroup: with the default
`GOGC=100` the heap may double between collections, and under many concurrent high-rate
flows (a burst of parallel downloads or uploads) that doubling crossed the container limit
and the kernel killed the process, dropping every open connection while the relay failed
over to the tunnel. `GOMEMLIMIT` makes the collector work harder as the heap approaches
the value instead, which is what the Traefik Helm chart does by default. Memory is released
in steps for a minute or two after a burst; a short sample can look like a leak and is not.
If the limit changes, change `GOMEMLIMIT` with it.

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
