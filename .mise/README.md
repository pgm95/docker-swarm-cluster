# Mise Tasks & Tooling

Task orchestration, deployment pipeline, and development tooling for swarm-cluster.

## Structure

```text
.mise/
  config.toml             # Base env vars, tool versions, task includes
  config.{dev,prod}.toml  # Per-environment secrets, nodes, domains
  tasks/                  # Task definitions (TOML) — what to run
    swarm.toml            #   Stack operations: deploy, remove, cleanup, validate
    site.toml             #   Cluster-wide: deploy-infra, deploy-apps, drain, registry
    sops.toml             #   Secrets: init, encrypt, edit, status
  lib/swarm/              # Python package — how tasks work
    _*.py                 #   Internal: docker CLI, SSH, SOPS, compose, output, stack resolution
    *.py                  #   User-facing: deploy, convergence, status, validate, cleanup, nodes, etc.
  tests/                  # Pytest suite (mocked Docker/SSH, no live cluster needed)
```

Mise tasks are thin TOML definitions that delegate to the `swarm` Python package via `python3 -m swarm.<module>`. The one exception is `swarm:deploy`, which uses a short bash wrapper to `eval` Python's export output and pipe compose config into `docker stack deploy`.

The Python package centralizes all Docker CLI and SSH calls through `_docker.py` and `_ssh.py`, making the logic testable without a live cluster. Mise provides the environment (`PYTHONPATH`, SOPS keys, Docker host) and the task interface (`mise run swarm:deploy ...`).

## Environment Profiles

Dev/prod separation uses mise's `MISE_ENV` profile system. Dev is default (set in `.config/miserc.toml`).

```bash
# Dev (default) — accepts bare name, dir name, or full path
mise run swarm:deploy socket

# Prod
MISE_ENV=prod mise run swarm:deploy socket
```

Each profile provides:

- `_.file`: SOPS-encrypted secrets (`PROJECT_SECRETS_DIR/{env}.yaml`)
- `SWARM_HOST`: SSH URL of a manager node (e.g. `ssh://root@swarm-vm`)
- `SWARM_SSH_USER`: SSH user for non-manager node access
- `GLOBAL_SWARM_OCI_REGISTRY`: derived from `DOMAIN_PRIVATE`
- `GLOBAL_ACME_CA_SERVER`: staging CA in dev, production in prod

`DOCKER_HOST` is deliberately NOT exported into the shell — it would pin the local Docker CLI and IDE integrations to the remote Swarm. Instead, `SWARM_HOST` is the source of truth; the swarm Python library (`_docker.docker_env()`) maps it to `DOCKER_HOST` on each subprocess invocation. The `swarm:deploy` bash wrapper does the same with an `export` scoped to its shell. Local `docker context` stays free to switch between daemons.

### Processing Order

mise processes base `[env]` BEFORE profile `[env]`. Tera templates in base config cannot reference profile vars. Both `_.file` directives (base + profile) are additive.

This is why `GLOBAL_SWARM_OCI_REGISTRY` (uses `DOMAIN_PRIVATE` from SOPS) lives in each profile file, not base config.

### Variable Sources

| Variable | Source | Location |
|----------|--------|----------|
| `DOMAIN_PUBLIC`, `DOMAIN_PRIVATE`, `GLOBAL_OIDC_URL`, `GLOBAL_LDAP_BASE_DN` | SOPS | `PROJECT_SECRETS_DIR/{env}.yaml` |
| `SWARM_HOST`, `SWARM_SSH_USER` | Plaintext | `.mise/config.{env}.toml` |
| `GLOBAL_SWARM_OCI_REGISTRY` | Derived | `.mise/config.{env}.toml` |
| `GLOBAL_SMTP_*`, `REGISTRY_*`, `GLOBAL_LDAP_ADDRESS` | SOPS | `PROJECT_SECRETS_DIR/shared.yaml` |
| `GLOBAL_CIFS_HOST`, `GLOBAL_CIFS_USERNAME`, `GLOBAL_CIFS_PASSWORD` | SOPS | `PROJECT_SECRETS_DIR/shared.yaml` |
| `GLOBAL_TZ`, `GLOBAL_NONROOT_*` | Plaintext | `.mise/config.toml` (base) |

## Compose Preprocessing

Docker Swarm doesn't natively support `include:` or cross-file YAML anchors. `compose_config()` in `_compose.py` bridges this:

```text
stacks/_shared/anchors.yml + <stack>/compose.yml
    → concatenate into temp file
    → docker compose --project-directory <stack-dir> --project-name <name> config
    → fixup: strip 'name:', unquote stringified integers
    → docker stack deploy -c -
```

`--project-name` uses the folder name with `NN_` prefix stripped, so default network names match the Swarm stack name.

`docker compose config` stringifies certain integer fields that `docker stack deploy` requires as raw integers. `_fixup_config()` in `_compose.py` corrects this automatically before returning output. Currently fixes:

- `name:` property at root level (rejected by stack deploy)
- `published: "443"` → `published: 443` (port numbers)
- `size: "10485760"` → `size: 10485760` (tmpfs size)

**Docker Config note:** `docker compose config` resolves `file:` paths to absolute local paths but does NOT inline contents. `docker stack deploy` reads files from local disk at deploy time. Config file contents cannot be modified by sed/envsubst in the piped output — preprocessing must happen on source files before `docker compose config` runs.

## Deploy Pipeline

`mise run swarm:deploy <stack> [--update]` stages:

The `<stack>` argument accepts a bare stack name (`metrics`), a directory name (`40_metrics`), or a full path (`stacks/infra/40_metrics`). `resolve_stack_path()` searches `stacks/infra/` then `stacks/apps/` for a match.

1. **Prepare** (`swarm.deploy`) — resolves stack name, detects versioning, decrypts stack secrets, validates, creates versioned Docker secrets/configs, builds+pushes custom images. Outputs shell exports for the bash wrapper.
2. **Compose preprocessing** (`swarm._compose`) — anchor concatenation + `docker compose config` + integer fixups
3. **Stack deploy** — `docker stack deploy --detach --with-registry-auth -c -`. With `--update`, adds `--resolve-image always` to force Swarm to re-pull mutable tags (`latest`, `release`, etc.)
4. **Convergence wait** (`swarm.convergence`) — polls until replicas running (default 180s, configurable via `CONVERGE_TIMEOUT`)

The prepare step runs as a Python subprocess. Its stdout contains `export KEY=VALUE` statements that the bash wrapper `eval`s, making decrypted secrets and computed values (STACK_NAME, DEPLOY_VERSION, OCI_TAG_*) available for compose interpolation and the deploy command.

### Secrets Pipeline

Secrets reach containers in two modes: **versioned Swarm secrets** (at `/run/secrets/`) or **env var injection** (compose interpolation). The mode is determined by whether `secrets.yml` uses `${DEPLOY_VERSION}`.

#### Env var injection (no `${DEPLOY_VERSION}`)

Mise decrypts all SOPS files into env vars before any task runs. Compose `${VAR}` references resolve against this environment. No Docker secrets are created.

#### Versioned Swarm secrets (`${DEPLOY_VERSION}` in `secrets.yml`)

The deploy task creates immutable Docker secrets named `<key>_<deploy_version>`. Values are resolved from two sources in priority order:

1. **`secrets.env`** (stack-local) -- SOPS-decrypted at deploy time. Use for secrets scoped to a single stack.
2. **Environment variables** (global) -- already loaded by mise from `shared.yaml` + `{env}.yaml`. Use for secrets shared across stacks or that differ per environment.

For each entry in `secrets.yml` with `${DEPLOY_VERSION}`, the pipeline extracts the base name (e.g. `global_cf_token` from `name: global_cf_token_${DEPLOY_VERSION}`), looks for a matching key in `secrets.env` first, then falls back to `os.environ`. Stack-local secrets always take precedence over global env vars.

#### Example: global secret as versioned Docker secret

Add the secret to a SOPS secrets file loaded by mise (shared or per-env):

```yaml
# .secrets/prod.yaml
GLOBAL_CF_ACME_API_TOKEN_PRIVATE: <token>
```

Reference it in the stack's `secrets.yml` with the lowercased name:

```yaml
# secrets.yml
secrets:
  cf_token:
    name: global_cf_acme_api_token_private_${DEPLOY_VERSION}
    external: true
```

Mount it in compose:

```yaml
# compose.yml
environment:
  - CF_TOKEN_FILE=/run/secrets/cf_token
secrets:
  - cf_token
```

The deploy pipeline finds `GLOBAL_CF_ACME_API_TOKEN_PRIVATE` in the environment, creates `global_cf_acme_api_token_private_<version>` as a Docker secret, and Swarm mounts it at `/run/secrets/cf_token`.

#### Validation

`validate_required_secrets()` checks that all names referenced in `secrets.yml` exist as either a key in `secrets.env` or an env var (uppercased). Missing secrets fail the deploy before any Docker operations.

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

Stacks needing external resources use `init-` prefixed sidecar services. These run idempotent setup (DB roles, LDAP users) and exit cleanly. The `*deploy-init` anchor (`condition: on-failure`, `failure_action: continue`, `monitor: 0s`) lets Swarm treat exit 0 as "done" without restart loops or false rollbacks. Provisioner credentials come from shared SOPS secrets (env var injection).

## Python Library

Task logic lives in the `swarm` Python package at `.mise/lib/swarm/`, invoked by mise tasks as `python3 -m swarm.<module>`. `PYTHONPATH` is set in mise `[env]` to `.mise/lib`.

### Internal modules (prefixed `_`)

| Module | Purpose |
|--------|---------|
| `_compose` | Compose config preprocessing (anchor concatenation + docker compose config + stack deploy fixups) |
| `_docker` | Docker CLI subprocess wrappers — all docker calls go through here |
| `_ssh` | SSH execution helpers for remote node commands |
| `_output` | Logging and output formatting (data to stdout, diagnostics to stderr) |
| `_sops` | SOPS decryption — calls sops binary, handles `_B64` suffix |
| `_stack` | Stack name resolution (`NN_` prefix stripping), path resolution, and directory discovery |

### Public modules (CLI entry points)

| Module | Task | Purpose |
|--------|------|---------|
| `deploy` | `swarm:deploy` | Deploy preparation: secrets, builds, env exports |
| `convergence` | `swarm:deploy` | Post-deploy convergence polling and health checks |
| `remove` | `swarm:remove` | Stack removal with drain wait |
| `status` | `status` | Cluster node and stack health display |
| `validate` | `swarm:validate` | Compose validation and bind mount path checks |
| `cleanup` | `swarm:cleanup` | Removes orphaned versioned secrets/configs, runs `docker system prune --all --volumes --force` on every node |
| `networks` | `swarm:init-networks` | Overlay network discovery and creation. `SWARM_INTERNAL_NETWORKS` (space-separated) controls which get `--internal`. `SWARM_OVERLAY_MTU` sets the VXLAN MTU at creation time |
| `nodes` | (library) | Swarm node discovery and placement constraint matching |
| `secrets` | (library) | Secret parsing, validation, and versioned creation |
| `site` | `site:deploy-infra`, `site:deploy-apps`, `site:drain` | Site-wide orchestration |
| `registry_auth` | `site:registry` | Registry login across swarm nodes |

### Testing

Tests live at `.mise/tests/`, configured via `.config/pyproject.toml`. All Docker/SSH calls are mocked at the subprocess boundary — no live cluster required.

```bash
pytest          # run via mise env
mise run validate   # includes pytest via pre-commit hook
```

### Error handling

All modules use a `SwarmError` exception hierarchy (`DockerError`, `SSHError`, `SopsError`, `SecretError`, `ValidationError`). CLI entry points catch `SwarmError` for clean error messages; unexpected exceptions produce full tracebacks.

## Validation & Pre-commit

Pre-commit hooks run on every commit (`.config/pre-commit.yaml`):

| Hook | Scope | Action |
|------|-------|--------|
| `check-yaml` | YAML (excl `.secrets/`) | Syntax validation |
| `check-json` | JSON | Syntax validation (Grafana dashboards) |
| `check-case-conflict` | All files | Case-insensitive filesystem collision detection |
| `yamllint` | YAML (excl `.secrets/`) | Style linting |
| `markdownlint-cli2` | Markdown | Documentation linting |
| `taplo-lint` | TOML | TOML linting |
| `ruff` | Python | Linting (unused imports, bugs, style) |
| `pytest` | Always | Python test suite |
| `check-secrets-encrypted` | `secrets.env`, `.secrets/*.yaml` | Verify SOPS markers present |
| `compose-validate` | `compose.yml`, `anchors.yml` | Full Swarm compatibility via `swarm:validate` |
| `gitleaks` | All files | Secret detection |

`compose-validate` runs the full pipeline (anchors + compose config + fixups + `docker stack config`) and checks bind mount paths on target nodes.

## Adding a New Stack

1. Create `stacks/<namespace>/<stack-name>/compose.yml`
2. Follow compose conventions (see existing stacks as reference)
3. Add stack-specific secrets to `secrets.env` → `mise run sops:encrypt`
4. For secrets already in SOPS globals (shared or per-env), reference them directly in `secrets.yml` using the lowercased env var name
5. Add `secrets.yml` / `configs.yml` if using versioned secrets/configs
6. For infra: prefix folder with `NN_` for deploy ordering
7. For Postgres consumers: add `init-db` sidecar (see `stacks/infra/60_accounts/compose.yml`)
8. Validate: `mise run validate`

App stacks are auto-discovered. A `.nodeploy` file opts out of bulk `site:deploy-apps`.
