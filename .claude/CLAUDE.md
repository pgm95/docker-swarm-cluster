# swarm-cluster

Docker Swarm homelab infrastructure with centralized management.

## Key Files

| File | Purpose |
|----------|---------|
| `README.md` | Architecture decisions, infrastructure details |
| `PROJECT_SECRETS_DIR/shared.yaml` | SOPS-encrypted shared secrets (`GLOBAL_SECRETS`) |
| `PROJECT_SECRETS_DIR/{env}.yaml` | SOPS-encrypted env-specific secrets (per `MISE_ENV`) |
| `.config/miserc.toml` | Default `MISE_ENV` (dev) |
| `.mise/config.{env}.toml` | Per-environment config (nodes, `_.file`, OCI registry) |
| `stacks/<namespace>/<stack>/secrets.env` | SOPS-encrypted stack-specific secrets |
| `stacks/<namespace>/<stack>/secrets.yml` | Swarm secret definitions (versioned) |
| `stacks/<namespace>/<stack>/configs.yml` | Docker config definitions (versioned) |
| `stacks/_shared/anchors.yml` | Centralized YAML anchors (logging, placement, deploy, resources) |
| `.mise/tasks/scripts/compose-config.sh` | `compose_config()` — preprocesses compose files with shared anchors |
| `.config/` | Tool configs (pre-commit, yamllint, markdownlint, sops, taplo) |

## Remote Management

All Docker commands target the remote Swarm manager via SSH to `DOCKER_HOST` (set per-environment in mise profile).
SOPS decryption and compose preprocessing happen locally; only final deployment goes over SSH.

## Environment Profiles

Dev/prod separation uses mise's native `MISE_ENV` profile system. Dev is the default; prod requires `MISE_ENV=prod`.

- `.config/miserc.toml` sets default `MISE_ENV=dev`
- `.mise/config.{env}.toml` holds env-specific `_.file` (SOPS secrets) + plaintext node hostnames + derived OCI registry
- `.mise/config.toml` holds shared config + shared `_.file` for `GLOBAL_SECRETS`
- Both `_.file` directives are processed additively

**Processing order:** mise processes base `[env]` BEFORE profile `[env]`. Tera templates in base config cannot reference profile vars. This is why `GLOBAL_SWARM_OCI_REGISTRY` (derived from `DOMAIN_PRIVATE`) lives in profile files, not base.

## Stack Namespaces

| Namespace | Purpose | Stacks |
| --------- | ------- | ------ |
| `_shared` | Centralized YAML anchors | (anchors.yml) |
| `apps` | User-facing applications | forwarder, homepage, immich, jellyfin, mealie, pinchflat, portainer, quantum, servarr, stirling, syncthing, tools |
| `infra` | Core infrastructure | socket, postgres, registry, accounts, gateway-internal, gateway-external, metrics |

## Swarm Patterns

| Host | Role | Key Labels |
| ------ | ------ | ------------ |
| vm | Manager | `location=onprem`, `ip=private` |
| fileserver | Manager | `storage=true` |
| mediaserver | Manager | `gpu=true` |
| vps | Worker | `location=cloud`, `ip=public` |

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

**Initialization:** `site:deploy-infra` automatically runs `swarm:init-networks` and `swarm:init-volumes` via `depends` before deploying stacks. For manual use: `mise run swarm:init-networks` and `mise run swarm:init-volumes`.

**Node mapping:**

| Constraint | SSH Target |
|------------|------------|
| `storage == true` | `SWARM_NODE_STORAGE` |
| `gpu == true` | `SWARM_NODE_GPU` |
| `location == cloud` | `SWARM_NODE_CLOUD` |
| Default | `SWARM_NODE_DEFAULT` |
