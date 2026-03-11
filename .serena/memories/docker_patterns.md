# Swarm Operations Reference

Operational reference for deploying and managing the swarm-cluster infrastructure.

## Deployment Order

Automated by `site:deploy` (calls `site:deploy-infra` then `site:deploy-apps`):

```text
mise run site:deploy           # Full cluster deploy
mise run site:deploy-infra     # Infra only
mise run site:deploy-apps      # Apps only (requires infra)
mise run site:reset            # Full teardown (stacks + secrets + configs + networks)
```

Infra stack deployment order is determined by `NN_` folder prefix (e.g., `00_socket`, `10_postgres`). Both `site:deploy-infra` and `site:reset` discover stacks dynamically via `find_stacks()` from `resolve-stack.sh`. The `stack_name()` helper strips the prefix so Swarm names remain clean (e.g., `socket`, `postgres`).

`site:deploy-infra` auto-calls `swarm:init-networks`.
`site:reset` discovers stacks in reverse order via `find_stacks --reverse`, then purges all versioned secrets/configs and removes overlay networks.

## Manual Pre-Deployment Tasks

- **SOPS secrets** — `mise run sops:init` to generate age key and update SOPS_CONFIG
- **Registry auth** — `mise run registry:auth` (generates htpasswd, docker login)
- **Node labels** — Apply `storage=true` on fileserver, `gpu=true` on mediaserver

## Gateway Security Differences

| Feature | External | Internal |
|---------|----------|----------|
| CrowdSec | Yes | No |
| Geoblock | Yes | No |
| Middlewares | security-headers, geoblock (auto-bootstrap), crowdsec | security-headers only |
| Dashboard | Disabled | Internal-only at `traefik.DOMAIN_PRIVATE` |

**Certificate configuration:**

| Gateway | Resolver | Storage | Token Variable |
|---------|----------|---------|----------------|
| External | `letsencrypt` | `acme.json` | `CLOUDFLARE_DNS_API_TOKEN_EXTERNAL` |
| Internal | `letsencrypt-internal` | `acme-internal.json` | `CLOUDFLARE_DNS_API_TOKEN_INTERNAL` |

**DNS requirements:**

- Public DNS: `*.DOMAIN_PUBLIC` → VPS public IP
- Internal DNS: `*.DOMAIN_PRIVATE` → VM LAN IP (no public records)

## Overlay Networks

Security-segregated networks, all pre-created by `swarm:init-networks`:

| Network | Primary Stack | Purpose | Flags |
|---------|--------------|---------|-------|
| `infra_socket` | socket | Docker API access via socket-proxy (read-only) | `--internal` |
| `infra_gw-internal` | gateway-internal | Internal Traefik routing (LAN/Tailscale) | |
| `infra_gw-external` | gateway-external | External Traefik routing (public internet) | |
| `infra_metrics` | metrics | Prometheus scraping and monitoring | |
| `infra_postgres` | postgres | Central Postgres database access | |

`--internal` blocks egress — containers cannot reach external networks or the internet. Only `infra_socket` uses this flag.

**VIP routing note:** IPVS-based VIP does not work out-of-the-box on the LXC nodes. Root cause: `ip_vs_rr` kernel module not loaded on the Proxmox host. Fix: `modprobe ip_vs_rr` on the host. Workaround: `endpoint_mode: dnsrr` on intra-stack-only services. Full details in `deployment_insights`.

**Why pre-create networks:** A circular dependency between gateway-* and metrics (gateways join `infra_metrics` for scraping, metrics joins `infra_gw-internal` for routing) prevents either from deploying first. Pre-creating all networks externally sidesteps this.

**Security benefit:** External Traefik can only reach services on `infra_gw-external`. VPS compromise does not grant access to internal-only services.

## Versioning Rationale

Swarm secrets and configs are immutable — can't update in-place. Versioning enables:

- **Zero-downtime rotation** — new resources deployed alongside old
- **Atomic deployments** — version ties all secrets/configs to a deployment
- **Cleanup automation** — `swarm:cleanup` removes unused secrets and configs

## Compose Preprocessing Workflow

Docker Swarm doesn't support `include` or centralized anchors natively. The workflow uses `compose_config()` as a preprocessor:

```bash
compose_config <stack>/compose.yml | sed '/^name:/d; s/published: "N"/published: N/' | docker stack deploy -c -
```

`compose_config()` (`.mise/tasks/scripts/compose-config.sh`) concatenates `stacks/_shared/anchors.yml` with the stack's compose file, then runs `docker compose --project-directory <stack-dir> --project-name <stripped-name> -f <merged> config`. The `--project-name` uses the folder basename with `NN_` prefix stripped, ensuring default network names match the Swarm stack name (e.g., `accounts_default` not `60_accounts_default`).

Transformations:

1. Anchor concatenation — centralized anchors from `stacks/_shared/anchors.yml` resolve across the file boundary
2. `docker compose config` — Resolves includes, interpolates variables. Docker Config `file:` directives are resolved to absolute paths but content is NOT inlined — `docker stack deploy` reads those files from local disk at deploy time.
3. Remove `name:` property (Swarm rejects it)
4. Convert quoted ports to integers (Swarm requires integers)

**File layout:**

```text
stacks/
├── _shared/
│   └── anchors.yml    # Centralized YAML anchors (logging, placement, deploy, resources)
├── <namespace>/
│   └── <stack>/
│       ├── compose.yml    # Services, networks, include directives, *anchor references
│       ├── secrets.yml    # Swarm secret definitions (versioned)
│       └── configs.yml    # Docker config definitions (versioned)
```

## Volume Ownership — Entrypoint Wrappers

Services needing non-root file access use entrypoint wrappers instead of `user:` in compose. A Docker Config init script runs as root, chowns volume dirs (skipped if `.volume-init` marker exists), then drops to the target UID before exec'ing the binary/entrypoint.

**Privilege drop method depends on base image:**

| Base | Tool | Notes |
|------|------|-------|
| Debian (util-linux) | `setpriv --reuid --regid --clear-groups` | Direct UID/GID, no passwd entry needed |
| Alpine (BusyBox) | `su -s /bin/sh $USER -c 'exec ...'` | Requires `addgroup`/`adduser` first for passwd entry |

**Pattern per service:**

- Remove `user: ${GLOBAL_NONROOT_DOCKER}` from compose
- Add `entrypoint: ["/bin/sh", "/init.sh"]`
- Add owner env var (e.g., `JELLYFIN_OWNER: ${GLOBAL_NONROOT_DOCKER}`)
- Mount init script as Docker Config at `/init.sh`
- Add `configs.yml` entry and `include:` if not existing

**Services with entrypoint wrappers:**

| Service | Stack | Base | Owner Env Var | Priv Drop | Target |
|---------|-------|------|---------------|-----------|--------|
| registry | infra/50_registry | Alpine | `REGISTRY_OWNER` | `su` | `/entrypoint.sh` (hardcoded CMD) |
| victoriametrics | infra/40_metrics | Alpine | `VM_OWNER` | `su` | `/victoria-metrics-prod "$@"` |
| quantum | apps/quantum | Alpine | `QUANTUM_OWNER` | `su` | `./filebrowser "$@"` |
| jellyfin | apps/jellyfin | Debian | `JELLYFIN_OWNER` | `setpriv` | `/jellyfin/jellyfin "$@"` |
| pinchflat | apps/pinchflat | Debian | `PINCHFLAT_OWNER` | `setpriv` | `/app/bin/docker_start "$@"` |

**Gotchas:**

- Registry hardcodes `registry serve /etc/distribution/config.yml` because compose `entrypoint:` override strips image CMD (`$@` is empty). Services with explicit `command:` in compose are unaffected.
- Quantum suppresses chown errors (`2>/dev/null || true`) because a Docker Config is mounted inside the volume directory.

**Services NOT needing wrappers:**

- `homepage` and `webfinger` have `user:` but no named volumes
- LinuxServer images (servarr) handle permissions via PUID/PGID
- Mealie handles its own user switching via `gosu` in its stock entrypoint
- Grafana runs as UID 472 (image default) — Docker named volumes initialize with correct ownership
- Uptime Kuma (`2-slim-rootless`) runs as UID 1000 — no wrapper needed
- All other services run as root by default

## Validation

Preprocessing exposes stricter `docker stack config` validation:

- Ports must be integers (not quoted strings)
- No `name:` property at root level
