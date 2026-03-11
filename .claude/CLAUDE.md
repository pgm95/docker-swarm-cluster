# swarm-cluster

Docker Swarm homelab infrastructure with centralized management.

## Key Files

| File | Purpose |
|----------|---------|
| `README.md` | Architecture decisions, infrastructure details |
| `PROJECT_SECRETS_DIR/shared.yaml` | SOPS-encrypted shared secrets (`GLOBAL_SECRETS`) |
| `PROJECT_SECRETS_DIR/{env}.yaml` | SOPS-encrypted env-specific secrets (per `MISE_ENV`) |
| `.config/miserc.toml` | Default `MISE_ENV` (dev) |
| `.mise/config.{env}.toml` | Per-environment config (manager node, `_.file`, OCI registry) |
| `stacks/<namespace>/<stack>/secrets.env` | SOPS-encrypted stack-specific secrets |
| `stacks/<namespace>/<stack>/secrets.yml` | Swarm secret definitions (versioned) |
| `stacks/<namespace>/<stack>/configs.yml` | Docker config definitions (versioned) |
| `stacks/_shared/anchors.yml` | Centralized YAML anchors (logging, placement, deploy, resources) |
| `.mise/tasks/scripts/compose-config.sh` | `compose_config()` — preprocesses compose files with shared anchors |
| `.mise/tasks/scripts/resolve-stack.sh` | `stack_name()` strips `NN_` folder prefix, `find_stacks()` ordered directory discovery |
| `.mise/tasks/scripts/deploy-secrets.sh` | Versioned secret validation and creation |
| `.mise/tasks/scripts/deploy-convergence.sh` | Stack convergence waiting and replica health checks |
| `.mise/tasks/scripts/resolve-networks.sh` | Dynamic overlay network discovery from compose files |
| `.mise/tasks/scripts/find-secret-files.sh` | SOPS-managed file discovery |
| `.config/` | Tool configs (pre-commit, yamllint, markdownlint, sops, taplo) |

## Remote Management

All Docker commands target the remote Swarm manager via SSH to `DOCKER_HOST` (set per-environment in mise profile).
SOPS decryption and compose preprocessing happen locally; only final deployment goes over SSH.

## Environment Profiles

Dev/prod separation uses mise's native `MISE_ENV` profile system. Dev is the default; prod requires `MISE_ENV=prod`.

- `.config/miserc.toml` sets default `MISE_ENV=dev`
- `.mise/config.{env}.toml` holds env-specific `_.file` (SOPS secrets) + manager hostname + derived OCI registry
- `.mise/config.toml` holds shared config + shared `_.file` for `GLOBAL_SECRETS`
- Both `_.file` directives are processed additively

**Processing order:** mise processes base `[env]` BEFORE profile `[env]`. Tera templates in base config cannot reference profile vars. This is why `GLOBAL_SWARM_OCI_REGISTRY` (derived from `DOMAIN_PRIVATE`) lives in profile files, not base.

## Stack Namespaces

| Namespace | Purpose | Stacks |
| --------- | ------- | ------ |
| `_shared` | Centralized YAML anchors | (anchors.yml) |
| `apps` | User-facing applications | forwarder, homepage, immich, jellyfin, mealie, pinchflat, portainer, quantum, servarr, stirling, syncthing, tools |
| `infra` | Core infrastructure | `NN_` prefixed for deploy order: socket, postgres, backup, gateway-internal, gateway-external, metrics, registry, accounts |

## Swarm Patterns

Node count is environment-specific. Labels drive placement — hostnames are irrelevant to scheduling.

| Node Role | Swarm Role | Key Labels |
| --------- | ---------- | ---------- |
| VM | Manager | `location=onprem`, `ip=private`, `type=vm` |
| LXC | Manager | `location=onprem`, `storage=true`, `gpu=true`, `type=lxc` |
| VPS | Worker | `location=cloud`, `ip=public`, `type=vps` |

**Image naming:** `${GLOBAL_SWARM_OCI_REGISTRY}/<stack>/<service>:<tag>`

**Custom images:** Build context in `stacks/<ns>/<stack>/build/<service>/`. Content-based tagging — tag defaults to SHA-256 hash of build context, so Dockerfile changes automatically produce new tags. `swarm:deploy` auto-detects `build/*/` directories, computes content hash, builds+pushes if the image doesn't exist in registry, and exports `OCI_TAG_<SERVICE>` env vars for compose interpolation.

## Secrets & Configs Patterns

| Mode | Trigger | Delivery |
|------|---------|----------|
| **Versioned Swarm Secrets** | `${DEPLOY_VERSION}` in `secrets.yml` | Mounted at `/run/secrets/` |
| **Versioned Docker Configs** | `${DEPLOY_VERSION}` in `configs.yml` | Mounted at target path |
| **Env Var Injection** | No `DEPLOY_VERSION` | SOPS decrypt → compose interpolation |

**Docker Configs:** Config files (previously bind-mounted `./config/*`) now use Docker Configs for zero-drift deployments. Local git repo is single source of truth — no file sync needed to remote nodes.

**`_FILE` support:** Check app docs before migrating — not all apps support reading secrets from files. See README.md for tested compatibility.

**Shared secrets:** Versioned secrets are per-deploy (`<name>_<sha>_<ts>`). Secrets needed by multiple stacks stay in `GLOBAL_SECRETS`, auto-injected as env vars by base config `_.file`. Environment-specific secrets (domains, OIDC, LDAP base DN) live in `PROJECT_SECRETS_DIR/{env}.yaml`, auto-injected by profile `_.file`. All SOPS-encrypted secrets files are centralized in `PROJECT_SECRETS_DIR`. Non-sensitive shared config lives in mise base `[env]`, env-specific non-secrets in mise profile `[env]`.

## Data Patterns

**Backups:** The `infra/backup` stack provides automated encrypted pg_dump backups of all Postgres databases via borgmatic + BorgBackup. Dumps use `name: all` for auto-discovery, borg handles deduplication and encryption (`repokey-blake2`). Restores require postgres superuser credentials passed via CLI flags at restore time.

**Initialization:** `site:deploy-infra` automatically runs `swarm:init-networks` via `depends` before deploying stacks. Infra stacks are discovered dynamically via `find_stacks()` and deployed in folder-name order (`NN_` prefix). Overlay networks are discovered dynamically from `infra_*: external: true` declarations in compose files — adding a network to any infra stack automatically includes it in creation and teardown.

**Volume ownership:** Services needing non-root file access use entrypoint wrappers (Docker Config init scripts) that chown volume dirs and drop privileges before exec'ing the target binary. Debian-based images use `setpriv`; Alpine-based images (BusyBox) use `su` with a dynamically created passwd entry. This runs inside the container on the correct node — no external pre-creation needed.

**Node discovery:** Swarm nodes are discovered dynamically via `docker node inspect`. The `resolve-nodes.sh` helper matches placement constraints against live node labels for operations that need SSH access (bind mount validation, cleanup, registry auth). Only `SWARM_NODE_DEFAULT` (manager) is configured manually; all other nodes are auto-discovered.
