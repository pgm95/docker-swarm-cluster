# docker-swarm-cluster

Multi-node Docker Swarm infrastructure managed from a single Git repository. All orchestration,
secrets management, and deployment happen locally via [mise](https://mise.jdx.dev) tasks. Only
the final `docker stack deploy` goes over SSH to the remote Swarm manager.

## Table of Contents

- [Getting Started](#getting-started)
- [Architecture](#architecture)
- [Directory Structure](#directory-structure)
- [Environment Profiles](#environment-profiles)
- [Secrets Architecture](#secrets-architecture)
- [Compose Preprocessing](#compose-preprocessing)
- [Deploy Pipeline](#deploy-pipeline)
- [Task Reference](#task-reference)
- [Validation and Pre-commit](#validation-and-pre-commit)
- [Adding a New Stack](#adding-a-new-stack)
- [Gotchas and Non-obvious Behavior](#gotchas-and-non-obvious-behavior)

## Getting Started

### Prerequisites

- [mise](https://mise.jdx.dev) installed locally
- DNS-based (hostname) SSH access to all swarm nodes
- Docker Engine on all nodes (tested with 29.x)
- [Tailscale](https://tailscale.com) running on all nodes (inter-node connectivity)
- All swarm nodes must resolve `DOMAIN_PRIVATE` (configure Tailscale DNS or split DNS so cloud nodes can reach the private registry)
- Two DNS zones: one public (Cloudflare), one private (local DNS like AdGuard Home)

### 1. Clone and install tools

```bash
git clone <repo-url> && cd swarm-cluster
mise run env:setup    # Installs tools (sops, age, pre-commit) and configures hooks
```

### 2. Initialize secrets encryption

```bash
mise run sops:init    # Generates age keypair, patches SOPS config with public key
```

This creates `age.key` (gitignored) — the private key for decrypting all secrets.

### 3. Configure nodes

Edit `.mise/config.dev.toml` (or `config.prod.toml` for production):

| Variable | Purpose |
|----------|---------|
| `SWARM_NODE_DEFAULT` | Hostname of Swarm manager node |
| `DOCKER_HOST` | Auto-derived as `ssh://root@SWARM_NODE_DEFAULT` |

Other nodes are discovered automatically from the swarm via `docker node inspect`.
Node hostnames must be SSH-resolvable from the local machine (DNS, `/etc/hosts`, or Tailscale MagicDNS).
The SSH user is configurable via `SWARM_SSH_USER` in base config (default: `root`).

### 4. Configure domains and shared secrets

Create the SOPS-encrypted secrets files. Each file needs specific keys:

**Per-environment secrets** (`mise run sops:edit .secrets/dev.yaml`):

| Key | Purpose |
|-----|---------|
| `DOMAIN_PUBLIC` | Public domain for external gateway |
| `DOMAIN_PRIVATE` | Private domain for internal gateway |
| `GLOBAL_OIDC_URL` | Authelia OIDC issuer URL |
| `GLOBAL_LDAP_BASE_DN` | LLDAP base DN |

**Shared secrets** (`mise run sops:edit .secrets/shared.yaml`):

| Key | Purpose |
|-----|---------|
| `REGISTRY_USER`, `REGISTRY_PASS` | Private registry htpasswd credentials |
| `GLOBAL_SMTP_*` | SMTP relay for notifications |
| `GLOBAL_LDAP_ADDRESS` | LLDAP server address |
| `GLOBAL_POSTGRES_PROVISIONER_USER` | Postgres provisioner role name |
| `GLOBAL_POSTGRES_PROVISIONER_PASSWORD` | Postgres provisioner role password |
| `GLOBAL_USERNAME`, `GLOBAL_PASSWORD` | Default admin credentials |

### 5. Configure external API keys

These live in stack-level `secrets.env` files:

| Stack | Key | Purpose |
|-------|-----|---------|
| `gateway-internal` | `CLOUDFLARE_DNS_API_TOKEN_INTERNAL` | DNS-01 challenge for `*.DOMAIN_PRIVATE` |
| `gateway-external` | `CLOUDFLARE_DNS_API_TOKEN_EXTERNAL` | DNS-01 challenge for `*.DOMAIN_PUBLIC` |
| `gateway-external` | `GEOBLOCK_IP2LOCATION_TOKEN` | IP geolocation database download |
| `gateway-external` | `BOUNCER_KEY_TRAEFIK` | CrowdSec bouncer ↔ LAPI authentication |
| `gateway-external` | `CROWDSEC_CTI_KEY` | CrowdSec threat intelligence API |
| `gateway-external` | `CROWDSEC_POSTGRES_PASSWORD` | CrowdSec database role password |
| `registry` | `REGISTRY_HTPASSWD_B64` | Registry auth file (base64-encoded) |
| `registry` | `REGISTRY_HTTP_SECRET` | Upload session token signing |
| `postgres` | `POSTGRES_PASSWORD` | Postgres superuser password |

Edit with `mise run sops:edit <stack>/secrets.env`.

### 6. Initialize the swarm

On the manager node, initialize Docker Swarm and join workers. Then apply node labels:

```bash
# On each node, apply the appropriate labels
docker node update --label-add location=onprem --label-add ip=private --label-add type=vm <manager-node>
docker node update --label-add location=onprem --label-add storage=true <storage-node>
docker node update --label-add location=cloud --label-add ip=public <cloud-node>
```

### 7. Configure DNS

| Zone | Provider | Records |
|------|----------|---------|
| `*.DOMAIN_PUBLIC` | Cloudflare | A → VPS public IP |
| `*.DOMAIN_PRIVATE` | Local DNS (AGH) | A → VM LAN IP |

The private domain must have **no public DNS records**.

### 8. Bootstrap and deploy

```bash
mise run registry:auth      # Login onprem nodes to private registry
mise run site:deploy        # Deploy everything (infra then apps)
```

`site:deploy` handles the full sequence: network initialization, infra stacks in
dependency order, then app stacks. First deploy may require
`docker service update --force <service>` for services that start before their
dependencies converge (see [Gotchas](#deploy-and-update)).

## Architecture

### Cluster Topology

Nodes join the Swarm over Tailscale. Overlay networks (VXLAN) tunnel through Tailscale for
service-to-service communication. The control plane (port 2377), gossip (7946), and overlay
(4789) traffic all flow over the Tailnet, with no public port exposure beyond HTTPS.

Workload placement is driven entirely by **node labels**, not hostnames. Services declare
constraints against capability labels; nodes that match those labels receive the workload.

**Label schema:**

| Label | Values | Purpose |
|-------|--------|---------|
| `location` | `onprem`, `cloud` | Physical/network location |
| `ip` | `public`, `private` | Internet-routable or behind NAT |
| `type` | `vm`, `lxc` | Hypervisor type (affects kernel capabilities) |
| `storage` | `true` | Bulk storage mounts available |
| `gpu` | `true` | GPU for hardware transcoding |

**Placement anchors** (defined in `stacks/_shared/anchors.yml`):

| Anchor | Constraints |
|--------|------------|
| `*place-vm` | `location == onprem`, `type == vm` |
| `*place-onprem` | `location == onprem`, `ip == private` |
| `*place-storage` | `location == onprem`, `type == lxc`, `storage == true` |
| `*place-cloud` | `location == cloud`, `ip == public` |
| `*place-gpu` | `location == onprem`, `type == lxc`, `gpu == true` |

Node counts and hostname mappings are environment-specific; see
[Environment Profiles](#environment-profiles). In a minimal dev setup, `*place-storage` and
`*place-gpu` can resolve to the same physical node.

### Networking

Five pre-created overlay networks partition traffic by function:

| Network | Purpose | Flags |
|---------|---------|-------|
| `infra_socket` | Docker API access (read-only socket-proxy) | `--internal` (no egress) |
| `infra_gw-internal` | Internal Traefik routing (LAN/Tailscale) | |
| `infra_gw-external` | External Traefik routing (public internet) | |
| `infra_metrics` | Prometheus scraping | |
| `infra_postgres` | Central Postgres access | |

Networks are discovered dynamically from `infra_*: external: true` declarations in compose
files and created by `swarm:init-networks` (runs automatically before infra deployment).
Adding a new overlay network to any infra compose file automatically includes it in creation
and teardown. Networks exist independently of any stack, which breaks the circular dependency
between gateways and metrics, which each need to join the other's network.

**Dual Traefik gateways** handle ingress:

| Gateway | Node Constraint | Domain | Security Layer |
|---------|----------------|--------|---------------|
| External | `*place-cloud` | `*.DOMAIN_PUBLIC` | CrowdSec + geoblock + security-headers |
| Internal | `*place-vm` | `*.DOMAIN_PRIVATE` | security-headers only |

Both bind ports 80/443 in host mode on their respective nodes. Both use a `websecure`
entrypoint. Routing correctness comes from DNS and Host rules, not entrypoint names.

Static config uses CLI flags in compose `command:` (Traefik v3's config sources are mutually
exclusive; CLI allows `${VAR}` interpolation). Dynamic config uses the file provider via
Docker Configs. Service routing uses Swarm provider labels under `deploy.labels`.

### Infra Stacks

| Stack | Services | Role |
|-------|----------|------|
| `infra/socket` | socket-proxy | Read-only Docker API proxy for all consumers |
| `infra/postgres` | postgres | Central PostgreSQL 17 instance |
| `infra/backup` | borgmatic, init-db | Automated encrypted pg_dump backups (BorgBackup) |
| `infra/gateway-internal` | traefik | Internal reverse proxy + TLS termination |
| `infra/gateway-external` | traefik, crowdsec, init-db | Public reverse proxy + WAF |
| `infra/metrics` | prometheus, victoria-metrics, grafana, uptime-kuma, init-db | Monitoring + dashboards + status |
| `infra/registry` | registry | Private OCI registry (htpasswd auth, Traefik TLS) |
| `infra/accounts` | authelia, lldap, redis, webfinger, init-db, init-ldap | SSO + LDAP + bootstrap sidecars |

### App Stacks

`stacks/apps/` contains application stacks. Each is independently deployable via
`swarm:deploy`. A `.nodeploy` file in a stack root opts it out of bulk `site:deploy-apps`.

### Storage

| Type | Pattern | Delivery |
|------|---------|----------|
| Persistent data | `<service>-<purpose>` named volume | Docker volume (Swarm-prefixed) |
| Configuration | `./config/<service>/` | Docker Configs (versioned, immutable) |
| Bulk media/files | `/mnt/*` | Bind mount to host |

Volumes are node-local. Services that need non-root ownership use entrypoint wrappers
(Docker Config init scripts) that chown volume directories and drop privileges before
exec'ing the service binary. The privilege-drop method depends on the base image: Debian
images use `setpriv`, Alpine images use BusyBox `su` (BusyBox `setpriv` only handles
capabilities, not UID/GID switching). This runs inside the container on the correct node,
avoiding external volume pre-creation.

## Directory Structure

```text
.
├── .config/                       # Tool configs (pre-commit, yamllint, sops, markdownlint, taplo)
│   ├── miserc.toml                # Sets default MISE_ENV=dev
│   ├── pre-commit.yaml            # Hook definitions
│   ├── sops.yaml                  # SOPS creation rules (age key, path patterns)
│   └── yamllint.yaml
├── .mise/
│   ├── config.toml                # Base config: shared env vars, tools, task includes
│   ├── config.dev.toml            # Dev profile: node hostnames, DOCKER_HOST, ACME staging CA
│   ├── config.prod.toml           # Prod profile: same structure, production values
│   └── tasks/
│       ├── swarm.toml             # Stack operations (deploy, remove, status, cleanup, init-*)
│       ├── site.toml              # Cluster-wide operations (deploy-infra, deploy-apps, reset)
│       ├── sops.toml              # Encryption management (init, encrypt, edit, status)
│       └── scripts/                   # Shared bash function libraries sourced by tasks
│           ├── compose-config.sh      # compose_config(): anchor concatenation + docker compose config
│           ├── content-hash.sh        # compute_content_hash(): SHA-256 of build context
│           ├── deploy-convergence.sh  # wait_for_convergence(), check_replica_health()
│           ├── deploy-secrets.sh      # validate_required_secrets(), create_versioned_secrets()
│           ├── find-secret-files.sh   # find_secret_files(): SOPS-managed file discovery
│           ├── resolve-networks.sh    # get_infra_networks(): dynamic overlay network discovery
│           ├── resolve-nodes.sh       # get_swarm_nodes(), get_service_node(), ssh_node()
│           ├── sops-decrypt.sh        # sops_decrypt(): SOPS file to key=value lines
│           └── sops-export.sh         # sops_export(): decrypt + export as env vars (handles _B64)
├── .secrets/                      # SOPS-encrypted secrets
│   ├── shared.yaml                # Cross-environment secrets (SMTP, registry, LDAP, Postgres provisioner)
│   ├── dev.yaml                   # Dev-specific secrets (domains, OIDC URL, LDAP base DN)
│   └── prod.yaml                  # Prod-specific secrets
├── stacks/
│   ├── _shared/
│   │   └── anchors.yml            # YAML anchors: logging, deploy behavior, placement, resource limits
│   ├── infra/                     # Infrastructure stacks (deployment-order-sensitive)
│   │   └── <stack>/
│   │       ├── compose.yml        # Service definitions (references anchors from _shared)
│   │       ├── secrets.yml        # Swarm secret definitions (versioned via DEPLOY_VERSION)
│   │       ├── secrets.env        # SOPS-encrypted stack-specific secrets
│   │       ├── configs.yml        # Docker Config definitions (versioned)
│   │       ├── config/            # Config source files (become Docker Configs)
│   │       └── build/             # Custom image build contexts (optional)
│   └── apps/                      # Application stacks (order-independent)
│       └── <stack>/               # Same structure as infra stacks
├── age.key                        # SOPS age private key (gitignored)
└── README.md
```

Not every stack has all files. `secrets.yml`, `configs.yml`, `config/`, and `build/` are
present only when needed. A stack using only env var injection (no versioned Swarm secrets)
won't have `DEPLOY_VERSION` references and skips the secret creation step.

## Environment Profiles

Dev and prod are separated using mise's `MISE_ENV` profile system. Dev is the default
(set in `.config/miserc.toml`).

```bash
# Dev (default)
mise run swarm:deploy stacks/infra/socket

# Prod (explicit)
MISE_ENV=prod mise run swarm:deploy stacks/infra/socket
```

Each profile provides:

- **`_.file`**: path to its SOPS-encrypted secrets file (`PROJECT_SECRETS_DIR/{env}.yaml`)
- **`SWARM_NODE_DEFAULT`**: Swarm manager hostname
- **`DOCKER_HOST`**: SSH target for the Swarm manager (derived from `SWARM_NODE_DEFAULT`)
- **`GLOBAL_SWARM_OCI_REGISTRY`**: derived from `DOMAIN_PRIVATE` (which comes from SOPS)
- **`GLOBAL_ACME_CA_SERVER`**: Let's Encrypt staging CA in dev, production CA in prod

## Secrets Architecture

### Encryption Layer (SOPS + age)

All secrets are SOPS-encrypted in Git using age keys. The SOPS config (`.config/sops.yaml`)
maps file paths to encryption keys. `mise run sops:init` generates the keypair and patches
the config.

### Three Layers of Secrets

Secrets are organized by scope. Each layer has a different delivery mechanism:

```text
┌─────────────────────────────────────────────────┐
│  1. Shared secrets (.secrets/shared.yaml)       │
│     SMTP, registry creds, Postgres provisioner  │
│     → auto-injected as env vars by mise _.file  │
│     → available to ALL stacks, no per-stack     │
│       decryption needed                         │
├─────────────────────────────────────────────────┤
│  2. Environment secrets (.secrets/{env}.yaml)   │
│     Domains, OIDC URL, LDAP base DN             │
│     → auto-injected as env vars by mise _.file  │
│     → different values per MISE_ENV profile     │
├─────────────────────────────────────────────────┤
│  3. Stack secrets (<stack>/secrets.env)          │
│     Service-specific passwords, API keys        │
│     → decrypted by swarm:deploy at deploy time  │
│     → scoped to a single stack                  │
└─────────────────────────────────────────────────┘
```

**Configuration precedence:**

```text
1. .mise/config.toml [env]       → shared plaintext vars (TZ, PUID, paths)
   └─ _.file: .secrets/shared.yaml  → shared SOPS secrets
2. .mise/config.{env}.toml [env] → profile-specific vars (nodes, DOCKER_HOST, ACME CA)
   └─ _.file: .secrets/{env}.yaml   → env-specific SOPS secrets
```

Both `_.file` directives are additive — variables from shared and env-specific secrets coexist.

**Important**: Base config `[env]` is processed before profile `[env]`. Tera templates in
base config cannot reference variables defined in profile configs. This is why
`GLOBAL_SWARM_OCI_REGISTRY` (which uses `DOMAIN_PRIVATE` from the profile's SOPS file) must
live in each profile file, not in base config.

### Runtime Delivery

Secrets reach containers through two mechanisms, determined by the stack's file structure:

| Mode | Trigger | Flow |
|------|---------|------|
| **Versioned Swarm Secrets** | `${DEPLOY_VERSION}` in `secrets.yml` | SOPS decrypt → `docker secret create <name>_<sha>_<ts>` → mounted at `/run/secrets/` |
| **Env var injection** | No `DEPLOY_VERSION` references | SOPS decrypt → compose interpolation → container env vars |

Versioned secrets are immutable. Each deploy creates new ones with a unique suffix
(`<git-sha>_<epoch>`). Old versions persist until `swarm:cleanup` removes unused ones.

**Which mode to use** depends on the application:

- Apps with `_FILE` support → versioned Docker secrets (mounted files, more secure)
- Apps without `_FILE` support → env var injection (only option)
- Multi-line values (PEM keys) → `_B64` suffix in `secrets.env`, auto-decoded during deploy

### The `_B64` Convention

Multi-line values (PEM keys, certificates, htpasswd files) can't be stored in `.env` files.
Base64-encode them and use a `_B64` suffix in `secrets.env`. The deploy task automatically
base64-decodes the value and creates the Docker secret under the name without the suffix.

### Shared Secrets Limitation

Versioned Swarm secrets are scoped to a single stack — each `swarm:deploy` generates a unique
`DEPLOY_VERSION`. Credentials needed by multiple stacks (SMTP, registry, LDAP, Postgres
provisioner) live in `GLOBAL_SECRETS` and are auto-injected as environment variables by
mise's `_.file` integration.

## Compose Preprocessing

Docker Swarm doesn't natively support `include:` directives or cross-file YAML anchors.
A preprocessing step bridges this gap.

**The pipeline:**

```text
stacks/_shared/anchors.yml + stacks/<ns>/<stack>/compose.yml
    │
    ▼  cat (concatenate)
merged YAML with resolved anchors
    │
    ▼  docker compose --project-directory <stack-dir> config
fully resolved compose spec (includes resolved, vars interpolated, configs paths absolute)
    │
    ▼  sed: strip 'name:', fix quoted ports
Swarm-compatible YAML
    │
    ▼  docker stack deploy -c - <stack-name>
deployed stack
```

The `compose_config()` function in `.mise/tasks/scripts/compose-config.sh` handles steps 1-2.
It concatenates `anchors.yml` with the stack's `compose.yml` via process substitution, then
runs `docker compose config`. This means compose files can reference anchors like `*logging`,
`*place-vm`, `*deploy` without defining them locally.

Two sed transforms are required because `docker stack deploy` rejects:

- A `name:` property at root level
- Quoted port numbers (e.g., `published: "443"` must become `published: 443`)

**Docker Config note:** `docker compose config` resolves `file:` directives to absolute local
paths but does NOT inline file contents. `docker stack deploy` reads those files from local
disk at deploy time and sends the contents to the Swarm manager. This means config file
contents cannot be modified by sed/envsubst in the piped output, so preprocessing must happen
on source files before `docker compose config` runs.

## Deploy Pipeline

`mise run swarm:deploy stacks/<ns>/<stack>` runs through these stages:

1. **Secret detection**: scans stack `*.yml` files for `DEPLOY_VERSION` references
2. **SOPS decryption**: decrypts `secrets.env`, exports variables (global secrets already
   in environment via mise `_.file`)
3. **Pre-flight validation**: verifies all secrets referenced in `secrets.yml` are available;
   verifies all config files referenced in `configs.yml` exist on disk
4. **Secret creation**: creates versioned Docker secrets (`<name>_<sha>_<ts>`) from both
   stack secrets and global secrets
5. **Auto-build**: detects `build/*/` directories, computes content-hash tags, builds and
   pushes to the private registry if the image doesn't already exist
6. **Compose preprocessing**: `compose_config` + sed transforms
7. **Stack deploy**: `docker stack deploy --detach --with-registry-auth -c -`
8. **Convergence wait**: polls until all service replicas are running (configurable via `CONVERGE_TIMEOUT`, default 180s)
9. **Health check**: reports any services that failed to converge

### Deployment Order

Infra stacks are order-sensitive (encoded in `site:deploy-infra`):

```text
1. socket          # Docker API proxy, needed by gateways
2. postgres        # Database, needed by accounts and gateways
3. backup          # Borgmatic pg_dump backups of all databases
4. gateway-internal
5. gateway-external
6. metrics
7. registry
8. accounts        # SSO + LDAP, sidecars bootstrap DB and LDAP users
```

App stacks deploy after infra, in alphabetical order. `site:deploy` runs both in sequence.

### Custom Image Builds

Stacks with a `build/<service>/` directory trigger automatic image builds. Tags are
content-based, computed as a 12-character SHA-256 hash of the build context (file paths + contents,
excluding `.md` files). Same content always produces the same tag.

```text
Image:    ${GLOBAL_SWARM_OCI_REGISTRY}/<stack>/<service>:<content-hash>
Env var:  OCI_TAG_<SERVICE>=<content-hash>   (available for compose interpolation)
```

The deploy task checks the registry first (`docker manifest inspect`). If the image exists,
the build is skipped.

### Init Sidecars

Stacks that need external resources bootstrapped at deploy time use sidecar services
(prefixed `init-`). These connect to the target service, run idempotent setup (create DB
roles, seed LDAP users), then `exec sleep infinity` to satisfy Swarm's replica count.

Each sidecar's logic is delivered as a Docker Config (shell script). The provisioner
credentials come from `GLOBAL_SECRETS`, so sidecars never store passwords for other stacks.

## Task Reference

```text
# Cluster lifecycle
mise run site:deploy                          # Deploy everything (infra then apps)
mise run site:deploy-infra                    # Infra stacks only (ordered)
mise run site:deploy-apps                     # App stacks only (skips .nodeploy)
mise run site:reset                           # Teardown: stacks, secrets, configs, networks
mise run site:reset --volumes                 # Teardown including named volumes

# Single stack
mise run swarm:deploy stacks/<ns>/<stack>     # Deploy one stack
mise run swarm:remove stacks/<ns>/<stack>     # Remove one stack (waits for drain)

# Operations
mise run swarm:status                         # Node health + all stack replica status
mise run swarm:cleanup                        # Remove unused versioned secrets/configs, prune containers
mise run swarm:cleanup --prune-images         # Also prune all unused images on every node

# Registry
mise run registry:auth                        # Login all swarm nodes to private registry

# Secrets
mise run sops:init                            # Generate age key, patch SOPS config
mise run sops:encrypt                         # Encrypt all plaintext secrets files
mise run sops:edit <file>                     # Decrypt in editor, re-encrypt on save
mise run sops:status                          # Show encryption status of all secrets files

# Environment
mise run env:setup                            # Install tools, configure pre-commit hooks
mise run validate                             # Run all pre-commit hooks on all files
```

## Validation and Pre-commit

Pre-commit hooks run on every commit (config at `.config/pre-commit.yaml`):

| Hook | Scope | What It Does |
|------|-------|-------------|
| `trailing-whitespace` | All files | Strip trailing whitespace |
| `end-of-file-fixer` | All files | Ensure newline at EOF |
| `check-merge-conflict` | All files | Detect unresolved merge markers |
| `check-added-large-files` | All files | Block files >500KB |
| `detect-private-key` | All files | Detect committed private keys |
| `gitleaks` | All files | Scan for hardcoded secrets |
| `yamllint` | YAML (excl. `.secrets/`) | Syntax and style validation |
| `markdownlint-cli2` | Markdown | Documentation linting |
| `taplo-lint` | TOML | TOML linting |
| `compose-validate` | `compose.yml`, `anchors.yml` | Full Swarm compatibility check via `swarm:validate` |
| `check-secrets-encrypted` | `secrets.env`, `.secrets/*.yaml` | Verify SOPS encryption markers present |

The `compose-validate` hook runs the full preprocessing pipeline (anchor concatenation,
`docker compose config`, sed transforms) then pipes through `docker stack config` to catch
Swarm-specific incompatibilities. It also checks that bind mount paths exist on target nodes
(warning only, does not block commits).

## Adding a New Stack

### 1. Create the stack directory

```text
stacks/<namespace>/<stack-name>/
├── compose.yml         # Required
├── secrets.env         # If the stack has secrets (must be SOPS-encrypted)
├── secrets.yml         # If using versioned Swarm secrets
├── configs.yml         # If using Docker Configs
├── config/             # Source files for Docker Configs
└── build/              # Custom image build contexts (optional)
    └── <service>/
        └── Dockerfile
```

### 2. Write compose.yml

Follow these conventions (enforced by validation and pre-commit):

```yaml
services:
  myservice:
    image: org/image:tag
    logging: *logging                    # Required: log rotation anchor
    stop_grace_period: 30s               # Required: 30s default, 60s for stateful
    networks:
      - infra_gw-internal                # Only networks actually needed
    deploy:
      <<: [*place-vm, *deploy]           # Placement + behavior anchors
      labels:
        - "traefik.enable=true"          # Traefik labels under deploy.labels, NOT service-level
        - "traefik.http.routers.myservice.rule=Host(`my.${DOMAIN_PRIVATE}`)"
        - "traefik.http.routers.myservice.entrypoints=websecure"
        - "traefik.http.services.myservice.loadbalancer.server.port=8080"

networks:
  infra_gw-internal:
    external: true
```

- **Anchors** are referenced from `stacks/_shared/anchors.yml` and resolved at preprocessing time
- **Networks**: only join overlays the service actually needs; services that declare
  `networks:` lose the implicit default network, so add `default` explicitly for intra-stack
  communication
- **`external: true`**: all overlay networks must be declared external
- **Variables**: `${VAR}` references are resolved by `docker compose config` from the mise
  environment

### 3. Add secrets (if needed)

Create `secrets.env` with plaintext key=value pairs, then encrypt:

```bash
mise run sops:encrypt
```

If using versioned Swarm secrets, create `secrets.yml`:

```yaml
secrets:
  my_secret:
    name: my_secret_${DEPLOY_VERSION}
    external: true
```

Reference in compose.yml under `secrets:` (service-level) and mount via env var or
`/run/secrets/` path.

### 4. Add Docker Configs (if needed)

Place source files in `config/<service>/`, then define `configs.yml`:

```yaml
configs:
  my_config:
    name: my_config_${DEPLOY_VERSION}
    file: config/myservice/settings.yml
```

### 5. Add to deployment order (infra only)

App stacks are auto-discovered. Infra stacks must be added to the ordered list in
`site:deploy-infra` (`.mise/tasks/site.toml`).

### 6. If the stack needs Postgres

Add an `init-db` sidecar service that connects as the provisioner role and idempotently
creates the stack's database role and database. See `stacks/infra/accounts/compose.yml` for
the pattern.

### 7. Validate

```bash
mise run validate
```

## Gotchas and Non-obvious Behavior

### Deploy and Update

- **`start-first` corrupts exclusive-access volumes.** The default `*deploy` anchor uses
  `start-first` update order, where a new container starts before the old one stops. For databases
  and services with named volumes that require exclusive access, this means two processes
  writing to the same volume simultaneously. Use `*deploy-stop-first` for these services.

- **`start-first` + `FailureAction: rollback` can silently revert.** If a new task fails on
  startup (e.g., database not yet provisioned by a sidecar), Swarm auto-rolls back to the old
  task. The deploy appears successful (1/1 replicas) but you're running the old version. Fix
  with `docker service update --force <service>` after dependencies converge.

- **Restart exhaustion with cross-stack dependencies.** Deploy anchors use `max_attempts: 3`
  with `window: 120s`. Services that validate external dependencies at startup (OIDC
  providers, databases in other stacks) will stall permanently if those dependencies aren't
  ready in time. Common during initial `site:deploy`. Fix:
  `docker service update --force <service>`.

### LXC Node Constraints

Unprivileged LXC containers cannot use IPVS (Docker Swarm's default VIP load balancing).
Services on LXC nodes that communicate intra-stack (not through Traefik) must set
`endpoint_mode: dnsrr` under `deploy`. This bypasses IPVS so that DNS resolves directly to
container IPs. Services that only receive traffic via Traefik are unaffected because VIP
resolution happens on the Traefik node.

### Docker Configs

- **Must be non-zero bytes.** Docker rejects empty config files. Use a comment or doc
  separator as minimal content.
- **Read-only (0444, root-owned).** Non-root containers can read but not create sibling
  files. Apps that write skeleton configs at startup will fail with EACCES. Provide all
  expected files as Docker Configs.
- **No `mode` field.** `docker compose config` serializes mode as an octal string, which
  `docker stack config` rejects. For executable scripts, use `entrypoint: ["/bin/sh", "/script.sh"]`.

### Secrets

- **`$$` escaping** is only needed for values that go through compose interpolation (env var
  injection mode). Values stored as Docker secrets are not compose-interpolated, so `$$`
  in `secrets.env` would be stored literally.
- **Versioned secrets can't be shared across stacks** because each deploy generates a unique
  `DEPLOY_VERSION`. Shared credentials belong in `GLOBAL_SECRETS`.

### Overlay Networks

- **Encryption over WireGuard is broken.** Docker's `--opt encrypted=true` (IPsec over
  VXLAN) fails over Tailscale WireGuard tunnels. Triple encapsulation causes cross-node
  connectivity loss. All overlays run unencrypted; Tailscale provides the encryption layer.
- **Networks must be pre-created.** Circular dependencies between stacks (gateways need
  metrics network, metrics needs gateway network) prevent any single stack from creating all
  required networks. `swarm:init-networks` creates them before any stack deploys.

### Traefik

- **Labels must be under `deploy.labels`**, not at service level. Swarm ignores
  service-level labels for routing.
- **Middleware chain failures are silent.** If any middleware in an entrypoint's default chain
  fails to initialize (missing database, broken config), Traefik returns 404 for all routes
  on that entrypoint, not an error page. Check `docker service logs`.
- **`traefik.docker.network` labels are not needed.** Each gateway's CLI config sets a
  provider-level `network` default.

### Bind Mounts

Swarm rejects tasks immediately (`Rejected` state) when bind mount source paths don't exist
on the target node. Unlike Docker Compose, Swarm does not auto-create missing directories.
`swarm:validate` checks for this but only as a warning.
