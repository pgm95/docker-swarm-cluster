# Mise Tasks & Tooling

Task orchestration, deployment pipeline, and development tooling for swarm-cluster.

## Environment Profiles

Dev/prod separation uses mise's `MISE_ENV` profile system. Dev is default (set in `.config/miserc.toml`).

```bash
# Dev (default)
mise run swarm:deploy stacks/infra/00_socket

# Prod
MISE_ENV=prod mise run swarm:deploy stacks/infra/00_socket
```

Each profile provides:

- `_.file`: SOPS-encrypted secrets (`PROJECT_SECRETS_DIR/{env}.yaml`)
- `SWARM_NODE_DEFAULT`: Swarm manager hostname
- `DOCKER_HOST`: SSH target (derived from `SWARM_NODE_DEFAULT`)
- `GLOBAL_SWARM_OCI_REGISTRY`: derived from `DOMAIN_PRIVATE`
- `GLOBAL_ACME_CA_SERVER`: staging CA in dev, production in prod

### Processing Order

mise processes base `[env]` BEFORE profile `[env]`. Tera templates in base config cannot reference profile vars. Both `_.file` directives (base + profile) are additive.

This is why `GLOBAL_SWARM_OCI_REGISTRY` (uses `DOMAIN_PRIVATE` from SOPS) lives in each profile file, not base config.

### Variable Sources

| Variable | Source | Location |
|----------|--------|----------|
| `DOMAIN_PUBLIC`, `DOMAIN_PRIVATE`, `GLOBAL_OIDC_URL`, `GLOBAL_LDAP_BASE_DN` | SOPS | `PROJECT_SECRETS_DIR/{env}.yaml` |
| `SWARM_NODE_DEFAULT`, `DOCKER_HOST`, `SWARM_SSH_USER` | Plaintext | `.mise/config.{env}.toml` |
| `GLOBAL_SWARM_OCI_REGISTRY` | Derived | `.mise/config.{env}.toml` |
| `GLOBAL_SMTP_*`, `REGISTRY_*`, `GLOBAL_LDAP_ADDRESS` | SOPS | `PROJECT_SECRETS_DIR/shared.yaml` |
| `GLOBAL_TZ`, `GLOBAL_NONROOT_*` | Plaintext | `.mise/config.toml` (base) |

## Compose Preprocessing

Docker Swarm doesn't natively support `include:` or cross-file YAML anchors. `compose_config()` bridges this:

```text
stacks/_shared/anchors.yml + <stack>/compose.yml
    → cat (concatenate)
    → docker compose --project-directory <stack-dir> --project-name <name> config
    → sed: strip 'name:', fix quoted ports
    → docker stack deploy -c -
```

`--project-name` uses the folder name with `NN_` prefix stripped, so default network names match the Swarm stack name.

Two sed transforms required because `docker stack deploy` rejects:

- `name:` property at root level
- Quoted port numbers (`published: "443"` → `published: 443`)

**Docker Config note:** `docker compose config` resolves `file:` paths to absolute local paths but does NOT inline contents. `docker stack deploy` reads files from local disk at deploy time. Config file contents cannot be modified by sed/envsubst in the piped output — preprocessing must happen on source files before `docker compose config` runs.

## Deploy Pipeline

`mise run swarm:deploy stacks/<ns>/<stack>` stages:

1. **Secret detection** — scans `*.yml` for `DEPLOY_VERSION` references
2. **SOPS decryption** — decrypts `secrets.env`, exports variables
3. **Pre-flight validation** — verifies secrets and config files exist
4. **Secret/config creation** — versioned Docker secrets/configs (`<name>_<sha>_<ts>`)
5. **Auto-build** — detects `build/*/`, content-hash tags, builds+pushes if new
6. **Compose preprocessing** — `compose_config` + sed
7. **Stack deploy** — `docker stack deploy --detach --with-registry-auth -c -`
8. **Convergence wait** — polls until replicas running (default 180s, configurable via `CONVERGE_TIMEOUT`)

### Custom Image Builds

Stacks with `build/<service>/` directories trigger automatic builds. Tags are content-based (12-char SHA-256 of build context, excluding `.md` files).

```text
Image:    ${GLOBAL_SWARM_OCI_REGISTRY}/<stack>/<service>:<content-hash>
Env var:  OCI_TAG_<SERVICE>=<content-hash>
```

The deploy task checks the registry first (`docker manifest inspect`); existing images skip the build.

### Deployment Order

Infra stacks deploy in `NN_` folder-prefix order (auto-discovered by `find_stacks()`). App stacks deploy alphabetically after infra. `site:deploy` runs both.

### Init Sidecars

Stacks needing external resources use `init-` prefixed sidecar services. These run idempotent setup (DB roles, LDAP users), then `exec sleep infinity` for Swarm convergence. Provisioner credentials come from `GLOBAL_SECRETS`.

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
mise run swarm:remove stacks/<ns>/<stack>     # Remove one stack

# Operations
mise run swarm:status                         # Node health + stack replica status
mise run swarm:cleanup                        # Remove unused secrets/configs, prune containers
mise run swarm:cleanup --prune-images         # Also prune unused images on all nodes

# Registry
mise run registry:auth                        # Login all swarm nodes to private registry

# Secrets
mise run sops:init                            # Generate age key, patch SOPS config
mise run sops:encrypt                         # Encrypt all plaintext secrets files
mise run sops:edit <file>                     # Decrypt in editor, re-encrypt on save
mise run sops:status                          # Show encryption status

# Environment
mise run env:setup                            # Install tools, configure pre-commit hooks
mise run validate                             # Run all pre-commit hooks on all files
```

## Shared Scripts

Reusable bash function libraries in `.mise/tasks/scripts/`, sourced by tasks:

| Script | Key Functions |
|--------|---------------|
| `compose-config.sh` | `compose_config()` — anchor concatenation + docker compose config |
| `content-hash.sh` | `compute_content_hash()` — SHA-256 of build context |
| `deploy-convergence.sh` | `wait_for_convergence()`, `check_replica_health()` |
| `deploy-secrets.sh` | `validate_required_secrets()`, `create_versioned_secrets()` |
| `find-secret-files.sh` | `find_secret_files()` — SOPS-managed file discovery |
| `resolve-networks.sh` | `get_infra_networks()` — overlay network discovery from compose files |
| `resolve-nodes.sh` | `get_swarm_nodes()`, `ssh_node()` — node discovery from swarm API |
| `resolve-stack.sh` | `stack_name()`, `find_stacks()` — stack name/order resolution |
| `sops-decrypt.sh` | `sops_decrypt()` — SOPS file to key=value lines |
| `sops-export.sh` | `sops_export()` — decrypt + export as env vars (handles `_B64`) |

Scripts are pure function libraries — no hardcoded config. Operational knobs are task-level `env` vars.

## Validation & Pre-commit

Pre-commit hooks run on every commit (`.config/pre-commit.yaml`):

| Hook | Scope | Action |
|------|-------|--------|
| `compose-validate` | `compose.yml`, `anchors.yml` | Full Swarm compatibility via `swarm:validate` |
| `check-secrets-encrypted` | `secrets.env`, `.secrets/*.yaml` | Verify SOPS markers present |
| `yamllint` | YAML (excl `.secrets/`) | Syntax/style |
| `markdownlint-cli2` | Markdown | Documentation linting |
| `taplo-lint` | TOML | TOML linting |
| `gitleaks` | All files | Secret detection |

`compose-validate` runs the full pipeline (anchors → compose config → sed → `docker stack config`) and checks bind mount paths on target nodes.

## Adding a New Stack

1. Create `stacks/<namespace>/<stack-name>/compose.yml`
2. Follow compose conventions (see existing stacks or [compose rules](../.claude/rules/stack-compose.md))
3. Add `secrets.env` if needed → `mise run sops:encrypt`
4. Add `secrets.yml` / `configs.yml` if using versioned secrets/configs
5. For infra: prefix folder with `NN_` for deploy ordering
6. For Postgres consumers: add `init-db` sidecar (see `stacks/infra/60_accounts/compose.yml`)
7. Validate: `mise run validate`

App stacks are auto-discovered. A `.nodeploy` file opts out of bulk `site:deploy-apps`.
