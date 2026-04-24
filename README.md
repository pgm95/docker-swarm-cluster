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

### Setup

1. **Clone and bootstrap:**

   ```bash
   git clone https://github.com/pgm95/docker-swarm-cluster && cd docker-swarm-cluster
   mise run env:setup
   mise run sops:init    # Generates age keypair for secrets decryption
   ```

2. **Configure environment:** Dev and prod each have their own config
   (`.mise/config.dev.toml`, `.mise/config.prod.toml`). Dev is the default profile.
   You must set `SWARM_HOST` (SSH URL of a manager node, e.g. `ssh://root@swarm-vm`) and `SWARM_SSH_USER` (defaults to root).
   See [`.mise/README.md`](.mise/README.md) for all variable sources.

3. **Configure secrets:** Populate SOPS-encrypted secrets files:
   - `mise run sops:edit .secrets/dev|prod.yaml` — domains, OIDC URL, LDAP base DN
   - `mise run sops:edit .secrets/shared.yaml` — registry creds, SMTP, Postgres provisioner
   - Stack-level `secrets.env` files — per-stack API keys and passwords

4. **Deploy:**

   ```bash
   mise run site:deploy-infra   # Deploy infrastructure stacks in order
   mise run site:registry-auth  # Authenticate nodes for custom images
   mise run site:deploy-apps    # Deploy all application stacks
   ```

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

Six overlay networks partition traffic by function:

| Network | Purpose | Flags |
|---------|---------|-------|
| `infra_socket` | Docker API access (read-only socket-proxy) | `--internal` |
| `infra_gw-internal` | Internal Traefik routing (LAN/Tailscale) | |
| `infra_gw-external` | External Traefik routing (public internet) | |
| `infra_metrics` | Prometheus scraping | |
| `infra_postgres` | Central Postgres access | |
| `infra_ldap` | LDAP directory access | |

Networks are discovered dynamically from compose files and pre-created before deployment.
This breaks circular dependencies between stacks that need each other's networks.
Overlay MTU is set at creation time via [`SWARM_OVERLAY_MTU`](.mise/tasks/swarm.toml#L54).
Docker subtracts 50 bytes for VXLAN overhead from the configured value, yielding 1230 on the
VXLAN interface, which produces 1280-byte UDP packets on the wire (exact Tailscale MTU
fit). Docker's `daemon.json` `"mtu"` only affects the default bridge, not overlays.

### Dual Ingress Gateways

Two separate Traefik instances serve different access patterns:

- **External** (`*place-cloud`, `DOMAIN_PUBLIC`): CrowdSec + geoblock + security headers.
  Only entry point from the public internet.
- **Internal** (`*place-vm`, `DOMAIN_PRIVATE`): Security headers only. Serves LAN and
  Tailscale clients exclusively.

Both use host-mode ports and a unified `websecure` entrypoint on `:443`.
Services opt in with scope labels (`traefik.scope.internal=true` / `traefik.scope.external=true`).
Both gateways discover backend services via the socket-proxy on `infra_socket`.
Both obtain wildcard certs via Let's Encrypt DNS-01 challenge.
Each maintains its own cert storage and resolver.

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
| Persistent data | `<service>-<purpose>` named volume | Docker volume |
| Configuration | `./config/<service>/` | Docker Configs (versioned, immutable) |
| Bulk storage (local) | `/mnt/*` | Bind mount (services needing direct filesystem access) |
| Bulk storage (remote) | `cifs-<share>` named volume | Docker CIFS volume |

CIFS volumes use Docker's local driver with `type: cifs`, mounting SMB shares directly.
Credentials come from `GLOBAL_CIFS_*` in shared secrets.

Services needing non-root volume ownership use entrypoint wrappers (Docker Config init
scripts) that chown and drop privileges (`setpriv` on Debian, `su` on Alpine).

### Infrastructure Components

Infra stacks are auto-discovered and deploy in `NN_` prefix order.
Each stack's README documents service-level details and operational procedures.

- **Socket Proxy:** Central read-only Docker API gateway for consumers needing node-agnostic Swarm API info.
- **Postgres:** Central database server.
  All stateful services share one instance via dedicated roles provisioned by init-db sidecars.
- **Backup:** Borgmatic with scheduled backups, deduplication, and encryption.
  Auto-discovers all Postgres databases, streams dumps directly to the repository.
- **Dual Gateways:** Two Traefik instances: external (Coupled with CrowdSec WAF + geoblocking
  for public internet), and internal (Internal services accessible only on LAN/Tailscale).
  Both use host-mode ports and DNS-based routing.
- **Observability:** Node Exporter and cAdvisor for host and per-container metrics.
  Prometheus scrapes these and all other compatible targets via dockerswarm_sd_configs and static_configs
  Alloy collects logs and Loki stores them. Grafana visualizes everything.
  Uptime Kuma monitors service availability and alerts.
- **Registry:** private OCI registry for custom images. Nodes authenticate via `site:registry-auth`.
  Stacks with `build/` directories trigger automatic builds during `swarm:deploy`.
- **Authentication:** Authentik provides OIDC, user directory, LDAP outpost, and WebFinger.
  Group membership (`GLOBAL_ADMIN_GROUP`) maps to application-level admin roles.

## Nuances and Limitations

### Deploy and Update

- **`start-first` fails with exclusive-access files**
  For databases and services with exclusive-access volumes, use stop-first.

- **`start-first` + rollback can silently revert.** If a new task fails (e.g., dependency not
  ready), Swarm auto-rolls back. Deploy appears successful but runs the old version. Fix:
  `docker service update --force <service>`.

- Nodes that need to pull custom images must be able to resolve `DOMAIN_PRIVATE`
  to reach the private registry.

### LXC Nodes

Unprivileged LXC containers cannot use IPVS (Docker Swarm's default VIP load balancing).
Any service that has consumers on LXC must set `endpoint_mode: dnsrr`, regardless of where
the service itself runs. The IPVS limitation is on the client side: LXC nodes cannot
translate VIP addresses to task IPs. Services only receiving traffic via Traefik are
unaffected, as VIP resolution happens on the Traefik node.

### Docker Configs

- **Must be non-zero bytes.** Docker rejects empty config files.
- **Read-only (0444, root-owned).** Apps that write skeleton configs at startup fail with
  EACCES. Provide all expected files as Docker Configs.
- **No `mode` field.** Use `entrypoint: ["/bin/sh", "/script.sh"]` for executable scripts.

### Bind Mounts

Swarm rejects tasks when bind mount paths don't exist on the target node (unlike Compose, no
auto-create). `swarm:validate` task warns but does not block.
