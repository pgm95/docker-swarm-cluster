# Hybrid Docker Swarm Cluster

Hybrid Docker Swarm cluster managed from a single Git repository.
Nodes connect with each other and to the dev machine securely over Tailscale.
All orchestration, secrets, and preprocessing run locally via mise tasks.
Only the final `docker stack deploy` command executes over SSH.

## Getting Started

### Prerequisites

- [mise](https://mise.jdx.dev) installed locally
- SSH access to all swarm nodes (DNS-resolvable hostnames)
- Docker Engine on all nodes (tested with 29.x)
- [Tailscale](https://tailscale.com) on all nodes for inter-node connectivity
- At least one node with a public IP (e.g. a cloud VPS) for external ingress
- Docker Swarm initialized with nodes [labeled for placement](#cluster-topology)
- Two domains (one for public ingress, one private) with DNS zones configured:

  | Zone | Provider | Record |
  |------|----------|--------|
  | `*.DOMAIN_PUBLIC` | Cloudflare | A → VPS public IP |
  | `*.DOMAIN_PRIVATE` | Local DNS | A → VM LAN IP (no public records) |

  All swarm nodes must resolve `DOMAIN_PRIVATE` (Tailscale DNS or split DNS for cloud nodes)
  to reach the private registry.

### Setup

1. **Clone and bootstrap:**

   ```bash
   git clone https://github.com/pgm95/docker-swarm-cluster && cd docker-swarm-cluster
   mise run env:setup
   mise run sops:init    # Generates age keypair for secrets decryption
   ```

2. **Configure environment:** Dev and prod each have their own config
   (`.mise/config.dev.toml`, `.mise/config.prod.toml`). Dev is the default profile.
   You must set `SWARM_NODE_DEFAULT` (any manager node hostname) and `SWARM_SSH_USER` (defaults to root)
   See [`.mise/README.md`](.mise/README.md) for all variable sources.

3. **Configure secrets:** Populate SOPS-encrypted secrets files:
   - `mise run sops:edit .secrets/dev|prod.yaml` — domains, OIDC URL, LDAP base DN
   - `mise run sops:edit .secrets/shared.yaml` — registry creds, SMTP, Postgres provisioner
   - Stack-level `secrets.env` files — per-stack API keys and passwords

4. **Deploy:**

   ```bash
   mise run site:deploy      # Deploy everything (infra then apps)
   ```

   `site:deploy-infra` automatically runs `registry:auth` after the registry stack deploys,
   so nodes are authenticated before any stack that uses custom images.

   First deploy may require `docker service update --force <service>` for services that start
   before their dependencies converge.

## Architecture

### Cluster Topology

Overlay traffic tunnels through the Tailnet: no port exposure beyond HTTPS for ingress.
Workload placement is driven by node labels, not hostnames.

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
Tailscale provides the encryption layer for the overlays. Docker's IPsec (`--opt encrypted=true`) fails over WireGuard.

#### Dual Gateways

Two separate Traefik instances serve different access patterns:

- **External** (`*place-cloud`, `DOMAIN_PUBLIC`): CrowdSec + geoblock + security headers.
  Only entry point from the public internet.
- **Internal** (`*place-vm`, `DOMAIN_PRIVATE`): Security headers only. Serves LAN and
  Tailscale clients exclusively.

Both bind ports 80/443 in host mode and use a unified `websecure` entrypoint on `:443`.
Routing correctness is via Host rules and DNS, not entrypoint names. Each gateway's Swarm
provider only discovers services that opt in via scope labels
(`traefik.scope.internal=true` / `traefik.scope.external=true`).

Both obtain wildcard certs via Let's Encrypt DNS-01 challenge (Cloudflare). Each maintains
its own cert storage and resolver. The internal gateway obtains certs without exposing
ports to the internet.

See [gateway-external README](stacks/infra/31_gateway-external/README.md) for more details.

### Secrets

Secrets are organized in three layers by scope:

| Layer | Location | Delivery |
|-------|----------|----------|
| **Shared** | `.secrets/shared.yaml` | Auto-injected by mise `_.file` to all stacks |
| **Per-environment** | `.secrets/{env}.yaml` | Auto-injected by mise `_.file` per profile |
| **Per-stack** | `<stack>/secrets.env` | Decrypted at deploy time by `swarm:deploy` |

Secrets reach containers as either **versioned Swarm secrets** (mounted at `/run/secrets/`,
triggered by `${DEPLOY_VERSION}` in `secrets.yml`) or **env var injection** (compose
interpolation). Multi-line values use the `_B64` suffix convention for base64 encoding.
Versioned secrets are immutable: each deploy creates new ones with a unique suffix; old
versions persist until `swarm:cleanup`.

### Storage

| Type | Pattern | Delivery |
|------|---------|----------|
| Persistent data | `<service>-<purpose>` named volume | Docker volume (Swarm-prefixed) |
| Configuration | `./config/<service>/` | Docker Configs (versioned, immutable) |
| Bulk media/files | `/mnt/*` | Bind mount |

Services needing non-root volume ownership use entrypoint wrappers (Docker Config init
scripts) that chown and drop privileges (`setpriv` on Debian, `su` on Alpine).

### Infrastructure Stacks

Nine infra stacks deploy in `NN_` prefix order.
Each stack's README documents service-level details and operational procedures.

| Stack | Purpose |
|-------|---------|
| `socket` | Read-only Docker API proxy — shared by consumers that need Swarm or container data without direct socket access |
| `postgres` | Central Postgres instance — all stateful services share one database server via dedicated roles |
| `backup` | Borgmatic — scheduled `pg_dump` of all databases, streamed to a borg repository with dedup and encryption |
| `gateway-internal` | Traefik for LAN/Tailscale clients (`DOMAIN_PRIVATE`) — security headers only |
| `gateway-external` | Traefik for public internet (`DOMAIN_PUBLIC`) — CrowdSec WAF + IP geoblocking + security headers |
| `metrics` | Prometheus, Grafana (OIDC), Node Exporter, cAdvisor, Uptime Kuma — scraping and visualization |
| `logging` | Loki + Alloy — centralized container log aggregation from all nodes |
| `registry` | Private Docker registry — hosts custom images built by the deploy pipeline |
| `accounts` | LLDAP + Authelia + Redis — LDAP directory, authentication server, and OIDC provider |

#### Cross-Stack Pipelines

**Request flow:** two paths based on client origin:

```text
Internet → gateway-external (headers → geoblock → CrowdSec) → service
LAN/Tailscale → gateway-internal (headers) → service
```

Both gateways discover backend services via the socket-proxy on `infra_socket`.
Services opt in with scope labels (`traefik.scope.internal=true` / `traefik.scope.external=true`).

**Auth chain:** OIDC-protected services delegate authentication through:

```text
Gateway → Authelia (session + OIDC) → LLDAP (user/group lookup) → Postgres (persistent storage)
```

OIDC consumers validate tokens against Authelia directly.
LDAP group membership (`app_admin`) maps to application-level admin roles.

**Observability:** metrics and logs flow through two parallel pipelines:

```text
Metrics: Prometheus ← scrapes targets via infra_metrics overlay
         Global services (node-exporter, cAdvisor, alloy) discovered via dockerswarm_sd_configs
         Replicated services via static_configs → Grafana dashboards

Logs:    Alloy (global, per-node) → tails local containers via socket-proxy sidecar → Loki
```

**Backup:** Borgmatic connects to central Postgres with a read-only role, auto-discovers
all databases, and streams each dump to a local borg repository.
Restore uses superuser credentials passed at restore time (not stored in the backup stack).

**Custom images:** stacks with `build/` directories trigger automatic builds during
`swarm:deploy`. Images are tagged by content hash, pushed to the private registry, and
pulled by Swarm nodes via `--with-registry-auth`. Existing tags are skipped.

## Nuances and Limitations

### Deploy and Update

- **`start-first` corrupts exclusive-access volumes.** The default `*deploy` anchor starts a
  new container before stopping the old one. For databases and services with exclusive-access
  volumes, use `update_config.order: stop-first`.

- **`start-first` + rollback can silently revert.** If a new task fails (e.g., dependency not
  ready), Swarm auto-rolls back. Deploy appears successful but runs the old version. Fix:
  `docker service update --force <service>`.

- **Restart exhaustion with cross-stack dependencies.** `max_attempts: 3` / `window: 120s`.
  Services that validate external dependencies at startup stall if those aren't ready. Common
  during initial `site:deploy`. Fix: `docker service update --force`.

- **Init sidecars show `0/1` replicas.** This is correct, they exit after provisioning.
  The deploy pipeline and `swarm:status` recognize completed tasks.

### LXC Nodes

Unprivileged LXC containers cannot use IPVS (Docker Swarm's default VIP load balancing).
Intra-stack services on LXC nodes must set `endpoint_mode: dnsrr`. Services routed through
Traefik are unaffected.

### Docker Configs

- **Must be non-zero bytes.** Docker rejects empty config files.
- **Read-only (0444, root-owned).** Apps that write skeleton configs at startup fail with
  EACCES. Provide all expected files as Docker Configs.
- **No `mode` field.** Use `entrypoint: ["/bin/sh", "/script.sh"]` for executable scripts.

### Bind Mounts

Swarm rejects tasks when bind mount paths don't exist on the target node (unlike Compose, no
auto-create). `swarm:validate` task warns but does not block.

## Documentation

| Document | Content |
|----------|---------|
| `.mise/README.md` | Task reference, deploy pipeline, environment profiles, compose preprocessing |
| `stacks/namespace/stackname/*.md` | Stack-specific documentation |
