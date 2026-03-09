# Deployment Test — Issues encountered, lessons learned

This memory retains historical debugging context and app-specific details.

## Test Cluster

Three-node setup (reduced from the documented 4-node topology):

| Node | Type | Hostname | Swarm Role | Labels |
|------|------|----------|------------|--------|
| VM | Proxmox VM | swarm-vm.home.arpa (10.50.50.179) | Manager/Leader | location=onprem, ip=private, type=vm |
| LXC | Proxmox LXC | swarm-lxc.home.arpa | Manager | location=onprem, ip=private, storage=true, gpu=true, type=lxc |
| VPS | Remote VPS | nerd1.jay-halibut.ts.net | Worker | location=cloud, ip=public, type=vps |

All nodes communicate over Tailscale (100.88.0.x addresses). Docker Engine 29.2.1.

### Verified Working End-to-End

- All 3 swarm nodes Ready/Active
- Cross-node overlay (VPS→VM) over all 4 networks — unencrypted, Tailscale handles encryption
- Both gateways discovering services via socket-proxy
- DNS: `*.DOMAIN_PRIVATE` → swarm-vm local IP (AGH), `*.DOMAIN_PUBLIC` → VPS public IP (Cloudflare)
- TLS: Both domains have valid Let's Encrypt certs (DNS-01 challenge)
- CrowdSec: Docker stdout acquisition working (via socket-proxy on `infra_socket`), bouncer reconnects after startup race
- Geoblock: Enabled with auto-bootstrap, DB version 26.2.15, country filtering active
- Registry docker login works end-to-end through Traefik TLS
- Prometheus healthy at `prometheus.DOMAIN_PRIVATE`
- VictoriaMetrics healthy at `victoria.DOMAIN_PRIVATE`
- LLDAP dashboard at `ldap.DOMAIN_PRIVATE` (HTTP 200)
- Authelia login portal at `auth.DOMAIN_PUBLIC` (HTTP 200, health endpoint verified)
- Authelia OIDC provider chain: Traefik-external → TLS → geoblock → CrowdSec bouncer → Authelia

## Accounts Stack — Full Resolution

### OIDC JWKS Key — Base64 Encoding Mismatch (RESOLVED)

**Symptom**: Authelia crashed with `identity_providers: oidc: jwks: key #1: symmetric keys are not permitted for signing`

**Root cause**: The JWKS private key is stored base64-encoded in `secrets.env` as `AUTHELIA_OIDC_JWKS_KEY_B64` (single-line for env file compatibility). The deploy task stored this base64 string directly as a Docker secret. Authelia read the raw base64 blob instead of a PEM key, interpreted it as a symmetric key, and rejected it.

**Fix**: Added `_B64` suffix handling to `swarm:_create-secrets`. This is a generic mechanism — any future secret with a `_B64` suffix will be automatically decoded.

### LLDAP Healthcheck Crash Loop (RESOLVED)

**Symptom**: LLDAP started successfully then received SIGTERM ~32 seconds after startup.

**Root cause**: The rootless Debian LLDAP image lacks `nc`, `wget`, and `curl`. Healthcheck failed silently. Fixed with `test: ["NONE"]`.

### Network Isolation — Services Couldn't Communicate (RESOLVED)

**Symptom**: Authelia crashed with `lookup redis on 127.0.0.11:53: no such host`

**Root cause**: Services that explicitly declare `networks` do NOT get the implicit default stack network. Fixed by adding `default` network to services needing intra-stack communication.

### OIDC Client Secret `$$` Escaping (RESOLVED)

pbkdf2 hashes in `secrets.env` had doubled `$$` from Docker Compose escaping. Since these are now Docker secrets (not compose-interpolated), the `$$` was stored literally. Fixed by replacing all `$$` with single `$`.

### Secret Name Mismatches (RESOLVED)

Two Docker secret names in `secrets.yml` didn't match their env var counterparts. Fixed by aligning names.

## External Gateway — Geoblock (RESOLVED)

**Symptom**: All requests to `auth.DOMAIN_PUBLIC` returned HTTP 404.

**Root cause**: Geoblock middleware failed to initialize (missing IP2Location DB), preventing ALL routers on that entrypoint.

**Problems discovered**:

1. `GEOBLOCK_IP2LOCATION_TOKEN` needs a real env var, not a `_FILE` path (Go template reads it)
2. IP2Location download API returns a ZIP archive, not a raw BIN file
3. Plugin's `databaseAutoUpdate` cannot bootstrap from empty state — needs initial seed

**Fix**: Env var injection for token, entrypoint wrapper that auto-downloads and extracts DB on first boot.

## Additional Insights

### Docker Compose Config Does NOT Inline Docker Config Contents

`docker compose config` resolves Docker Config `file:` directives to absolute paths but does NOT embed the file contents inline. `docker stack deploy -c -` reads those files from local disk at deploy time. This means sed/envsubst in the piped compose output cannot modify Docker Config file contents — preprocessing must happen on the source files before `docker compose config` runs.

### Traefik Logs to Stdout

Both gateways log to stdout (no `--log.filePath`). All logs — including provider errors, router creation errors, and middleware initialization failures — are visible via `docker service logs`. CrowdSec reads Traefik access logs from Docker stdout using the `docker` acquisition source via socket-proxy.

### LLDAP Rootless Debian Image Details

The `lldap/lldap:2026-01-22-debian-rootless` image runs as `lldap` (1000:1000) by default. It lacks `nc` and `wget` but does include `curl`, `jq`, and `jo` (required by the built-in `/app/bootstrap.sh`). Healthchecks using `nc`/`wget` fail silently — use `["NONE"]`.

### LLDAP Init Sidecar — `init-ldap` (accounts stack)

Fresh deployments failed because Authelia/WebFinger need LDAP bind users that don't exist yet. Solved with a sidecar service (`init-ldap`) using LLDAP's built-in `/app/bootstrap.sh`.

**How it works**: Entrypoint wrapper (Docker Config) generates user config JSON from compose-interpolated env vars at runtime, then calls `bootstrap.sh` which polls LLDAP, seeds users via GraphQL API, sets passwords via `lldap_set_password` (OPAQUE protocol), then `exec sleep infinity` to keep convergence at 1/1.

**Key details**:

- Writes JSON to `/tmp/bootstrap/user-configs` (avoids root for filesystem writes) with `USER_CONFIGS_DIR` env var override
- Passwords via Docker secret mounts (`password_file` in JSON), user IDs from compose env var interpolation
- `secrets.env` is single source of truth — no hardcoded values in Docker Configs
- Idempotent: skips existing users, updates changed, re-syncs passwords on every deploy
- `lldap_password_manager` group for Authelia (password reset enabled), `lldap_strict_readonly` for WebFinger

### Postgres Init Sidecar — `init-db` (accounts stack)

Authelia and LLDAP need dedicated Postgres roles and databases. The `init-db` sidecar in the accounts stack connects as the provisioner role and creates them idempotently.

**PG 17 quirks encountered during first deploy:**

- `psql` defaults to a database named after `PGUSER` — must set `PGDATABASE=postgres` explicitly since there's no database named after the provisioner
- `CREATE DATABASE ... OWNER <role>` requires `SET ROLE` privilege — must `GRANT <role> TO <provisioner>` after creating each role, before creating the database. PG 16+ changed `CREATEROLE` semantics; creating a role no longer implies `SET ROLE` on it
- Both the provisioner username and password come from `GLOBAL_SECRETS` env vars — nothing hardcoded

**Convergence behavior**: All accounts services start concurrently. `init-db` polls postgres via `pg_isready`, provisions within seconds. LLDAP/Authelia crash on first attempt (database doesn't exist yet), succeed on retry within the `max_attempts: 3` / `window: 120s` restart policy.

### Registry Auth — Node Login

`REGISTRY_USER` and `REGISTRY_PASS` in `GLOBAL_SECRETS` are consumed by `registry:auth` to run `docker login` on all swarm nodes. All nodes must be able to resolve `DOMAIN_PRIVATE` (configure Tailscale DNS or split DNS for cloud nodes).

### Docker Overlay Encryption Over WireGuard Is Broken

Docker's `--opt encrypted=true` (IPsec over VXLAN) breaks over Tailscale WireGuard. Triple encapsulation causes cross-node connectivity failures. All 5 overlay networks run unencrypted — Tailscale provides the encryption layer.

## Immich Stack — Deployment Issues

### VIP Routing on LXC Node

Root cause: unprivileged LXC user namespace prevents IPVS write operations. IPVS forwarding tables stay empty — DNS resolves to VIPs but TCP connections get `ECONNREFUSED`. Fix: `endpoint_mode: dnsrr` on intra-stack services that don't need Traefik routing.

### `start-first` Corrupts Exclusive-Access Volumes (RESOLVED)

**Symptom**: Postgres crash loop with `PANIC: could not locate a valid checkpoint record` after `swarm:deploy` updated the database service.

**Root cause**: The default `*deploy` anchor uses `update_config.order: start-first`. For databases, this starts a new container before stopping the old one — both Postgres instances access the same data volume simultaneously. Old instance receives SIGTERM mid-write, corrupting WAL. Required volume deletion to recover.

**Fix**: Database services now use `*deploy-stop-first` anchor (e.g., `<<: [*place-storage, *deploy-stop-first]`) instead of the default `*deploy`. The anchor modularization separates placement from behavior — no inline `update_config` overrides needed. Rule documented in `.claude/rules/stack-compose.md`.

## External Gateway — CrowdSec Postgres Migration (RESOLVED)

**Symptom**: `docker stack deploy` reported "Updating service gateway-external_crowdsec" but the CrowdSec container was not replaced — it continued running on the old SQLite config.

**Root cause**: `start-first` update order + `FailureAction: rollback` + cross-stack race with init-db sidecar.

Timeline:

1. Stack deploy triggers rolling update with `start-first` — new CrowdSec task starts before the old one stops
2. New task can't connect to Postgres (init-db sidecar hasn't provisioned the database yet), exits cleanly (exit code 0, state `complete`)
3. Swarm evaluates the new task within the 5s `Monitor` window, sees it failed (exited), triggers automatic rollback (`FailureAction: rollback`, `MaxFailureRatio: 0`)
4. Old task continues running on SQLite — silent rollback, no visible error in deploy output
5. Convergence loop sees 1/1 replicas (old task), declares success

**Fix**: `docker service update --force gateway-external_crowdsec` after init-db sidecar has converged.

**One-time issue**: Only occurs on initial migration when the database doesn't exist yet. Subsequent deploys work — database already provisioned, CrowdSec connects immediately.

**Key insight**: `start-first` + `FailureAction: rollback` can silently revert to the old task when new tasks fail due to cross-stack dependency races. Unlike `max_attempts` exhaustion (which stalls visibly), rollback restores the old task and the deploy appears successful.

**Env var note**: CrowdSec requires `CROWDSEC_BYPASS_DB_VOLUME_CHECK=true` when the `crowdsec-db` volume (`/var/lib/crowdsec/data`) is removed — the entrypoint hard-exits without it.

## Volume Init Migration — Resolved Issues

### `swarm:validate` bind mount section was silent (RESOLVED)

**Symptom**: Bind mount path checks produced no output after migrating to `resolve-nodes.sh`.
**Root cause**: Python f-string `v[\"source\"]` inside a single-quoted bash string passed to `python3 -c`. The literal `\"` caused a Python SyntaxError. The `2>/dev/null` on the python command silently swallowed the error.
**Fix**: Extract dict access to a local variable (`src = v["source"]`), avoiding backslashes in the f-string.

### Quantum OIDC TLS (UNRESOLVED)

FileBrowser (`quantum`) fails with `x509: certificate signed by unknown authority` when validating OIDC against `auth.DOMAIN_PUBLIC`. The container's CA bundle doesn't trust the Let's Encrypt cert chain. Not a volume issue — init wrapper works correctly (no more permission denied). User plans to fix via Tailscale DNS so all nodes can resolve `DOMAIN_PRIVATE` and the OIDC URL can point to the internal gateway instead.

## DNS Setup

| Domain | Provider | Resolution |
|--------|----------|------------|
| `*.DOMAIN_PUBLIC` | Cloudflare | Public IP of node running external gateway |
| `*.DOMAIN_PRIVATE` | AGH local | LAN IP of node running internal gateway |

Both Cloudflare zones have API tokens with Zone:Read + DNS:Edit for Let's Encrypt DNS-01 challenges.
