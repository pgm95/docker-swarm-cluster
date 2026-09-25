# Docker Swarm Homelab

On-prem Docker Swarm cluster managed from a single Git repository, with a small
relay VPS in front of it for public ingress. All orchestration, secrets, and
preprocessing run locally via mise tasks. Only the final `docker stack deploy`
(cluster) or `docker compose up` (relay) executes over SSH.

## Getting Started

### Prerequisites

- [mise](https://mise.jdx.dev) installed locally
- SSH access to all swarm nodes and to the relay VPS (DNS-resolvable hostnames)
- Docker Engine on all nodes and on the relay (tested with 29.x); the Docker CLI with the
  Compose plugin (5.x) on the operator's machine, since the relay is deployed by the local
  Compose client driving the remote daemon
- Docker Swarm initialized with nodes [labeled for placement](#cluster-topology)
- A relay VPS with a public IP, a WireGuard tunnel from the home router to it, and a
  router DNAT for the relay's direct path (both managed in the infrastructure repos;
  see [Public Ingress Relay](#public-ingress-relay))
- Two domains (one for public ingress, one private) with DNS zones configured:

  | Zone | Provider | Record |
  |------|----------|--------|
  | `*.DOMAIN_PUBLIC` | Cloudflare | A → VPS public IP |
  | `*.DOMAIN_PRIVATE` | Local DNS | A → VM LAN IP (no public records) |

### Setup

1. **Bootstrap:**

   ```bash
   mise run env:setup
   mise run sops:init    # Generates age keypair for secrets decryption
   ```

2. **Configure environment:** Dev and prod each have their own config
   (`.mise/config.dev.toml`, `.mise/config.prod.toml`). Dev is the default profile.
   You must set `SWARM_HOST` (SSH URL of a manager node, e.g. `ssh://root@swarm-vm`),
   `SWARM_SSH_USER` (defaults to root) and one `COMPOSE_HOST_<HOST>` per relay host.
   See [`.mise/README.md`](.mise/README.md) for all variable sources.

3. **Configure secrets:** Populate SOPS-encrypted secrets files by target:
   - `mise run sops:edit dev|prod` for per-environment values (domains, OIDC URL, LDAP base DN)
   - `mise run sops:edit global` for infrastructure credentials (registry, SMTP, Postgres provisioner); `oidc`, `backup`, `ldap` and `widget` for values two stacks share
   - `mise run sops:edit <stack>` for stack-local API keys and passwords

4. **Deploy:**

   ```bash
   mise run compose:deploy swarm-vps/haproxy   # Relay: owns public 443
   mise run site:deploy-infra                  # Infrastructure stacks in order
   mise run site:registry                      # Authenticate nodes for custom images
   mise run site:deploy-apps                   # All application stacks
   ```

   First deploy may require `docker service update --force <service>` for services that start
   before their dependencies converge.

## Architecture

### Cluster Topology

Every node is a VM on one LAN segment. Three managers keep quorum; one of them is a
small drained node that only votes and never runs workloads. Workload placement is
driven by node labels. Placement anchors in `stacks/_shared/anchors.yml` map label
constraints to reusable deploy blocks.

| Label | Values | Purpose |
|-------|--------|---------|
| `location` | `onprem` | Physical/network location |
| `ip` | `private` | Behind NAT |
| `type` | `vm` | Node type |
| `gpu` | `true` | GPU available |

The relay VPS is not a Swarm node. It runs a standalone Compose project (see
[Public Ingress Relay](#public-ingress-relay)).

### Networking

All nodes share one LAN segment, so overlay traffic never leaves it and nothing on the
cluster is exposed to the internet; public ingress arrives through the relay.
Overlay networks partition traffic by function:

| Network | Purpose |
|---------|---------|
| `infra_socket` | Docker API access (GET-only socket-proxy with a per-consumer allowlist) |
| `infra_gw-internal` | Internal Traefik routing (LAN) |
| `infra_gw-external` | External Traefik routing (public internet) |
| `infra_metrics` | Prometheus scraping |
| `infra_postgres` | Central Postgres access |
| `infra_ldap` | LDAP directory access |

Networks are discovered dynamically from compose files and pre-created before deployment.
This breaks circular dependencies between stacks that need each other's networks.
Overlay MTU is set at creation time via [`SWARM_OVERLAY_MTU`](.mise/tasks/swarm.toml#L50)
(1500 on a plain LAN). Docker's `daemon.json` `"mtu"` does not affect overlays, and existing
overlays keep their MTU.

### Public Ingress Relay

Public 443 terminates on a relay VPS, not on the cluster. HAProxy there
(`compose/swarm-vps/haproxy`, a standalone Compose project deployed with `compose:deploy`)
forwards every public client as its own TCP connection to the external gateway at home,
carrying the real client address in a PROXY protocol v2 header. The client's TLS session
runs end to end; the relay never terminates it and cannot read what it relays.

Two paths reach home, HAProxy picks:

- **direct**: over the WAN to the router, which DNATs port 8443 from the relay's address to
  the gateway node. One TCP flow per client, so throughput is bounded by the home WAN
  link, not by the VPS.
- **tunnel**: a WireGuard tunnel the router originates to the VPS, used as backup, for
  ssh and for metrics. VPS providers police UDP, so this path carries a fraction of the
  direct path's throughput regardless of how many clients share it.

A sidecar reads the home public address off the WireGuard peer (authenticated, roams on
IP change) and sets it on HAProxy through the runtime API, so there is no DDNS. Nothing
is copied to the VPS: the local Compose client drives the remote daemon over ssh and
builds both images there.

### Dual Ingress Gateways

Two separate Traefik instances serve different access patterns:

- **External** (`*place-main`, host port 8443, `DOMAIN_PUBLIC`): CrowdSec + geoblock +
  security headers. Reachable only through the relay; trusts the PROXY protocol header
  from the relay's two source addresses and nothing else.
- **Internal** (`*place-main`, host port 443, `DOMAIN_PRIVATE`): Security headers only.
  Serves LAN clients exclusively.

Both use host-mode ports and a unified `websecure` entrypoint.
Services opt in with scope labels (`traefik.scope.internal=true` / `traefik.scope.external=true`).
Both gateways discover backend services via the socket-proxy on `infra_socket`.
Both obtain wildcard certs via Let's Encrypt DNS-01 challenge.
Each maintains its own cert storage and resolver.

See [gateway-external README](stacks/infra/31_gateway-external/README.md) for more details.

### Secrets

Secrets are organized in three layers by scope:

| Layer | Location | Delivery |
|-------|----------|----------|
| **Global** | `.secrets/global.sops.yaml` and the topic files `oidc`, `backup`, `ldap`, `widget` | Auto-injected by mise `_.file` to all stacks |
| **Per-environment** | `.secrets/{env}.sops.yaml` | Auto-injected by mise `_.file` per profile |
| **Per-stack** | `<stack>/secrets.sops.yaml` | Decrypted at deploy time by `swarm:deploy` |

Every encrypted file is YAML and carries the `.sops.yaml` suffix, so one SOPS creation rule
and one pre-commit check cover them all.

Every value is defined in exactly one file and classified by relationship, not by who reads it.
A singleton has one consumer and lives in that stack's file. A pairing is a value two parties
must agree on, one of which writes it, and lives in the global topic file named after the
topic. Infrastructure values consumed by many stacks live in the global file, or in the
per-environment file when they differ by environment. Each value carries one identifier of the
form scope, subject, purpose, and that identifier is the SOPS key in upper case and the compose
alias, the Swarm object base name and the file under `/run/secrets/` in lower case. The
[secrets rule](.claude/rules/secrets.md) holds the vocabulary and the placement rules.

Secrets reach containers as either **versioned Swarm secrets** (mounted at `/run/secrets/`,
triggered by `${DEPLOY_VERSION}` in the stack's `include.yml`) or **env var injection** (compose
interpolation). File delivery is the default: apps read the mounted file through a native file
reference, an s6 `FILE__` variable or an entrypoint wrapper that exports it. Plain env is the
exception and carries a comment saying why, because anything compose interpolates lands in the
service spec. Credentials another stack consumes through dashboard widgets are never
interpolated into labels: the owning stack's label carries a `{{HOMEPAGE_FILE_*}}` placeholder
and both sides mount the same global key. Multi-line values are plain YAML block scalars.
Versioned secrets are immutable: each deploy creates new ones with a unique suffix; old
versions persist until `swarm:cleanup`.

### Storage

| Type | Pattern | Delivery |
|------|---------|----------|
| Persistent data | `<service>-<purpose>` named volume | Docker volume |
| Configuration | `./config/<service>/` | Docker Configs (versioned, immutable) |
| Bulk storage | `cifs-<share>` named volume | Docker CIFS volume |

CIFS volumes use Docker's local driver with `type: cifs`, mounting SMB shares directly.
Credentials come from `GLOBAL_CIFS_*` in the global secrets file.

Services needing non-root volume ownership use entrypoint wrappers (Docker Config init
scripts) that chown and drop privileges (`setpriv` on Debian, `su` on Alpine).

### Infrastructure Components

Stacks are organized by namespace: A subdir of `SWARM_STACKS_DIR` is considered a namespace.
`site:deploy-<namespace>` auto-discovers and deploys stacks in alphabetical order.

- **Socket Proxy:** Central GET-only Docker API gateway with a per-consumer allowlist, for consumers needing node-agnostic Swarm API info.
- **Postgres:** Central database server.
  All stateful services share one instance via dedicated roles provisioned by init-db sidecars.
- **Backup:** Borgmatic with scheduled backups, deduplication, and encryption.
  Targets multiple database instances. Streams dumps directly to the repository.
- **Relay:** HAProxy on the VPS, the only thing facing the internet. A Compose project,
  not a Swarm stack; deployed with `compose:deploy` over ssh.
- **Dual Gateways:** Two Traefik instances: external (behind the relay, coupled with
  CrowdSec WAF + geoblocking), and internal (services accessible only on the LAN).
  Both use host-mode ports and DNS-based routing.
- **Observability:** Node Exporter and cAdvisor for host and per-container metrics.
  Prometheus scrapes these and all other compatible targets via dockerswarm_sd_configs and static_configs
  Loki/Alloy for log collection and processing. Grafana is central observability hub.
  Gatus monitors service availability with synthetic workflows and alerts.
- **Registry:** private OCI registry for custom images. Nodes authenticate via `site:registry`.
  Stacks with `build/` directories trigger automatic builds during `swarm:deploy`.
- **Authentication:** Authentik provides OIDC and WebFinger; Syncs with lldap as LDAP source.
  Apps without native login sit behind forward auth through Authentik's embedded outpost.
  Group membership (`GLOBAL_ADMIN_GROUP`) maps to application-level admin roles.

## Nuances and Limitations

### Deploy and Update

- **`start-first` fails with exclusive-access files**
  For databases and services with exclusive-access volumes, use stop-first.
- **`start-first` + rollback can silently revert.** If a new task fails,
  Swarm auto-rolls back. Deploy appears successful but runs the old version.
  Fix: `docker service update --force <service>`.
- Nodes that need to pull custom images must be able to resolve `DOMAIN_PRIVATE`
  to reach the private registry.

### Service VIPs Drop Idle Connections

- **Swarm's VIP load balancer (IPVS) forgets a TCP connection after 900 s without a
  packet**, and neither end is told. Pooled connections to a database or cache then fail
  on first use with a reset. Docker cannot change the timeout. Stateful single-replica
  backends therefore use `endpoint_mode: dnsrr`, so clients connect to the task directly.

### tmpfs Mounts

- **Swarm drops the short `tmpfs:` list** without an error, and `shm_size` with it:
  the task gets the default 64 MB `/dev/shm` and no other tmpfs. Declare tmpfs as a
  long-form `type: tmpfs` entry under `volumes:` with `tmpfs.size`.

### Docker Configs

- **Must be non-zero bytes.** Docker rejects empty config files.
- **Read-only (0444, root-owned).** Apps that write skeleton configs at startup fail with
  EACCES. Provide all expected files as Docker Configs.
- **No `mode` field.** Use `entrypoint: ["/bin/sh", "/script.sh"]` for executable scripts.

### Device Passthrough

Swarm lacks support for passing devices to services.
The `dmm` stack works around this by running a privileged manager that grants
the needed cgroup rule for any bind-mounted `/dev` file.
See its [README](stacks/infra/70_dmm/README.md) for details.
