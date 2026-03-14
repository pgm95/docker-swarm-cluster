# swarm-cluster

Multi-node Docker Swarm infrastructure managed from a single Git repository. All orchestration,
secrets management, and deployment happen locally via [mise](https://mise.jdx.dev) tasks — only
the final `docker stack deploy` goes over SSH to the remote Swarm manager.

## Getting Started

### Prerequisites

- [mise](https://mise.jdx.dev) installed locally
- SSH access to all swarm nodes (DNS-resolvable hostnames)
- Docker Engine on all nodes (tested with 29.x)
- [Tailscale](https://tailscale.com) on all nodes for inter-node connectivity
- Two DNS zones: one public (Cloudflare), one private (local DNS)

### Setup

1. **Clone and install tools:**

   ```bash
   git clone <repo-url> && cd swarm-cluster
   mise run env:setup
   ```

2. **Initialize secrets encryption:**

   ```bash
   mise run sops:init    # Generates age keypair, patches SOPS config
   ```

   This creates `age.key` (gitignored) — the private key for decrypting all secrets.

3. **Configure environment:** Edit `.mise/config.dev.toml` — set `SWARM_NODE_DEFAULT` to
   your manager hostname. `DOCKER_HOST` is auto-derived as `ssh://<SWARM_SSH_USER>@<SWARM_NODE_DEFAULT>`.
   Other nodes are auto-discovered from the swarm.

4. **Configure secrets:** Populate SOPS-encrypted secrets files:
   - `mise run sops:edit .secrets/dev.yaml` — domains, OIDC URL, LDAP base DN
   - `mise run sops:edit .secrets/shared.yaml` — registry creds, SMTP, Postgres provisioner
   - Stack-level `secrets.env` files — per-stack API keys and passwords

5. **Initialize swarm and label nodes:**

   ```bash
   docker node update --label-add location=onprem --label-add type=vm <manager>
   docker node update --label-add storage=true --label-add gpu=true <storage>
   docker node update --label-add location=cloud --label-add ip=public <cloud>
   ```

6. **Configure DNS:**

   | Zone | Provider | Record |
   |------|----------|--------|
   | `*.DOMAIN_PUBLIC` | Cloudflare | A → VPS public IP |
   | `*.DOMAIN_PRIVATE` | Local DNS | A → VM LAN IP (no public records) |

   All swarm nodes must resolve `DOMAIN_PRIVATE` (Tailscale DNS or split DNS for cloud nodes
   to reach the private registry).

7. **Deploy:**

   ```bash
   mise run site:deploy      # Deploy everything (infra then apps)
   ```

   `site:deploy-infra` automatically runs `registry:auth` after the registry stack deploys,
   so nodes are authenticated before any stack that uses custom images.

   First deploy may require `docker service update --force <service>` for services that start
   before their dependencies converge.

## Architecture

### Cluster Topology

Nodes join the Swarm over Tailscale. Overlay traffic tunnels through the Tailnet — no public
port exposure beyond HTTPS. Workload placement is driven by node labels, not hostnames.

| Label | Values | Purpose |
|-------|--------|---------|
| `location` | `onprem`, `cloud` | Physical/network location |
| `ip` | `public`, `private` | Internet-routable or behind NAT |
| `type` | `vm`, `lxc` | Hypervisor type (affects kernel capabilities) |
| `storage` | `true` | Bulk storage mounts available |
| `gpu` | `true` | GPU passthrough available |

Placement anchors (`*place-vm`, `*place-storage`, etc.) in `stacks/_shared/anchors.yml` map
label constraints to reusable deploy blocks.

### Networking

Five overlay networks partition traffic by function:

| Network | Purpose | Flags |
|---------|---------|-------|
| `infra_socket` | Docker API access (read-only socket-proxy) | `--internal` |
| `infra_gw-internal` | Internal Traefik routing (LAN/Tailscale) | |
| `infra_gw-external` | External Traefik routing (public internet) | |
| `infra_metrics` | Prometheus scraping | |
| `infra_postgres` | Central Postgres access | |

Networks are discovered dynamically from compose files and pre-created before deployment.
This breaks circular dependencies between stacks that need each other's networks.

#### Dual Gateways

Two separate Traefik instances serve different access patterns:

- **External** (`*place-cloud`, `DOMAIN_PUBLIC`): CrowdSec + geoblock + security headers.
  Only entry point from the public internet.
- **Internal** (`*place-vm`, `DOMAIN_PRIVATE`): Security headers only. Serves LAN and
  Tailscale clients exclusively.

A single gateway can't serve both — external traffic needs security scanning that adds
unnecessary latency for trusted clients, and internal services need complete network
separation from the internet. Even if the external gateway is compromised, internal-only
services remain unreachable.

Both bind ports 80/443 in host mode and use a unified `websecure` entrypoint on `:443`.
Routing correctness is via Host rules and DNS, not entrypoint names. Each gateway's Swarm
provider only discovers services that opt in via scope labels
(`traefik.scope.internal=true` / `traefik.scope.external=true`).

Both obtain wildcard certs via Let's Encrypt DNS-01 challenge (Cloudflare). Each maintains
its own cert storage and resolver — the internal gateway obtains certs without exposing
ports to the internet.

See [gateway-external README](stacks/infra/31_gateway-external/README.md) for CrowdSec and
geoblock details.

### Secrets

Secrets are organized in three layers by scope:

| Layer | Location | Delivery |
|-------|----------|----------|
| **Shared** | `PROJECT_SECRETS_DIR/shared.yaml` | Auto-injected by mise `_.file` to all stacks |
| **Per-environment** | `PROJECT_SECRETS_DIR/{env}.yaml` | Auto-injected by mise `_.file` per profile |
| **Per-stack** | `<stack>/secrets.env` | Decrypted at deploy time by `swarm:deploy` |

Secrets reach containers as either **versioned Swarm secrets** (mounted at `/run/secrets/`,
triggered by `${DEPLOY_VERSION}` in `secrets.yml`) or **env var injection** (compose
interpolation). Multi-line values use the `_B64` suffix convention for base64 encoding.
Versioned secrets are immutable — each deploy creates new ones with a unique suffix; old
versions persist until `swarm:cleanup`.

### Storage

| Type | Pattern | Delivery |
|------|---------|----------|
| Persistent data | `<service>-<purpose>` named volume | Docker volume (Swarm-prefixed) |
| Configuration | `./config/<service>/` | Docker Configs (versioned, immutable) |
| Bulk media/files | `/mnt/*` | Bind mount |

Services needing non-root volume ownership use entrypoint wrappers (Docker Config init
scripts) that chown and drop privileges — `setpriv` on Debian, `su` on Alpine. See
[compose rules](/.claude/rules/stack-compose.md) for the pattern.

## Gotchas

### Post-Deploy

- **Uptime Kuma** (metrics stack) requires manual setup on first deploy: navigate to
  `status.DOMAIN_PRIVATE` to create admin account, add Docker host
  `tcp://socket-proxy:2375`, create an API key and uncomment the Prometheus scrape target
  in `prometheus.yml`, then redeploy metrics.

### Deploy and Update

- **`start-first` corrupts exclusive-access volumes.** The default `*deploy` anchor starts a
  new container before stopping the old one. For databases and services with exclusive-access
  volumes, use `*deploy-stop-first`.

- **`start-first` + rollback can silently revert.** If a new task fails (e.g., dependency not
  ready), Swarm auto-rolls back. Deploy appears successful but runs the old version. Fix:
  `docker service update --force <service>`.

- **Restart exhaustion with cross-stack dependencies.** `max_attempts: 3` / `window: 120s`.
  Services that validate external dependencies at startup stall if those aren't ready. Common
  during initial `site:deploy`. Fix: `docker service update --force`.

- **Init sidecars show `0/1` replicas.** Init services use `*deploy-init` anchor and
  exit after provisioning. `docker service ls` shows `0/1` — this is correct, not a
  failure. The deploy pipeline and `swarm:status` recognize completed tasks. `docker
  service update --force` (without `--detach`) misreports this as a task failure; use
  `--detach` for manual re-runs.

### LXC Nodes

Unprivileged LXC containers cannot use IPVS (Docker Swarm's default VIP load balancing).
Intra-stack services on LXC nodes must set `endpoint_mode: dnsrr`. Services routed through
Traefik are unaffected.

### Docker Configs

- **Must be non-zero bytes.** Docker rejects empty config files.
- **Read-only (0444, root-owned).** Apps that write skeleton configs at startup fail with
  EACCES. Provide all expected files as Docker Configs.
- **No `mode` field.** Use `entrypoint: ["/bin/sh", "/script.sh"]` for executable scripts.

### Secrets-related

- **`$$` escaping** is only needed for compose-interpolated values. Docker secrets are not
  compose-interpolated, so `$$` in `secrets.env` would be stored literally.
- **Versioned secrets can't be shared** across stacks — each deploy generates a unique
  `DEPLOY_VERSION`. Shared credentials belong in `GLOBAL_SECRETS`.

### Overlay Networks

- **Encryption over WireGuard is broken.** Docker's `--opt encrypted=true` (IPsec over VXLAN)
  fails over Tailscale. All overlays run unencrypted; Tailscale provides encryption.
- **Networks must be pre-created.** Circular dependencies between stacks prevent any single
  stack from creating all required networks.

### Traefik

- **Labels must be under `deploy.labels`**, not at service level.
- **Middleware chain failures are silent.** If any middleware fails to initialize, Traefik
  returns 404 for all routes on that entrypoint. Check `docker service logs`.

### Bind Mounts

Swarm rejects tasks when bind mount paths don't exist on the target node (unlike Compose, no
auto-create). `swarm:validate` warns but does not block.

## Documentation

| Document | Content |
|----------|---------|
| [`.mise/README.md`](.mise/README.md) | Task reference, deploy pipeline, environment profiles, compose preprocessing |
| [`.claude/CLAUDE.md`](.claude/CLAUDE.md) | AI agent instructions |
| [`.claude/rules/*.md`](.claude/rules/) | Domain-specific editing rules for AI agent |

**Stack-specific documentation:**

| Stack | Topics |
|-------|--------|
| [`infra/backup`](stacks/infra/20_backup/README.md) | Restore procedures, borgmatic limitations |
| [`infra/gateway-external`](stacks/infra/31_gateway-external/README.md) | CrowdSec, geoblock, middleware chain |
| [`infra/metrics`](stacks/infra/40_metrics/README.md) | Scraping global services, Swarm SD, node exporter limitations, recording rules |
| [`infra/logging`](stacks/infra/42_logging/README.md) | Socket-proxy sidecar pattern, Alloy reconnection, Loki storage |
| [`infra/accounts`](stacks/infra/60_accounts/README.md) | Convergence behavior, init sidecars, WebFinger build |
| [`apps/jellyfin`](stacks/apps/jellyfin/README.md) | GPU passthrough, custom Mesa drivers, codec support |
