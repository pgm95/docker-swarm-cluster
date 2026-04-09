# Mise Tasks & Tooling

Task orchestration, deployment pipeline, and development tooling for swarm-cluster.

## Structure

```text
.mise/
  config.toml             # Base env vars, tool versions, task includes
  config.{dev,prod}.toml  # Per-environment secrets, nodes, domains
  tasks/                  # Task definitions (TOML) — what to run
    swarm.toml            #   Stack operations: deploy, remove, cleanup, validate
    site.toml             #   Cluster-wide: deploy-infra, deploy-apps, reset, registry:auth
    sops.toml             #   Secrets: init, encrypt, edit, status
  lib/swarm/              # Python package — how tasks work
    _*.py                 #   Internal: docker CLI, SSH, SOPS, compose, output, stack resolution
    *.py                  #   User-facing: deploy, convergence, status, validate, cleanup, nodes, etc.
  tests/                  # Pytest suite (mocked Docker/SSH, no live cluster needed)
```

Mise tasks are thin TOML definitions that delegate to the `swarm` Python package via `python3 -m swarm.<module>`. The one exception is `swarm:deploy`, which uses a short bash wrapper to `eval` Python's export output and pipe compose config through `sed` into `docker stack deploy`.

The Python package centralizes all Docker CLI and SSH calls through `_docker.py` and `_ssh.py`, making the logic testable without a live cluster. Mise provides the environment (`PYTHONPATH`, SOPS keys, Docker host) and the task interface (`mise run swarm:deploy ...`).

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
| `GLOBAL_CIFS_HOST`, `GLOBAL_CIFS_USERNAME`, `GLOBAL_CIFS_PASSWORD` | SOPS | `PROJECT_SECRETS_DIR/shared.yaml` |
| `GLOBAL_TZ`, `GLOBAL_NONROOT_*` | Plaintext | `.mise/config.toml` (base) |

## Compose Preprocessing

Docker Swarm doesn't natively support `include:` or cross-file YAML anchors. `compose_config()` in `_compose.py` bridges this:

```text
stacks/_shared/anchors.yml + <stack>/compose.yml
    → concatenate into temp file
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

`mise run swarm:deploy stacks/<ns>/<stack> [--update]` stages:

1. **Prepare** (`swarm.deploy`) — resolves stack name, detects versioning, decrypts secrets, validates, creates versioned Docker secrets/configs, builds+pushes custom images. Outputs shell exports for the bash wrapper.
2. **Compose preprocessing** (`swarm._compose`) — anchor concatenation + `docker compose config` + sed transforms
3. **Stack deploy** — `docker stack deploy --detach --with-registry-auth -c -`. With `--update`, adds `--resolve-image always` to force Swarm to re-pull mutable tags (`latest`, `release`, etc.)
4. **Convergence wait** (`swarm.convergence`) — polls until replicas running (default 180s, configurable via `CONVERGE_TIMEOUT`)

The prepare step runs as a Python subprocess. Its stdout contains `export KEY=VALUE` statements that the bash wrapper `eval`s, making decrypted secrets and computed values (STACK_NAME, DEPLOY_VERSION, OCI_TAG_*) available for compose interpolation and the deploy command.

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

Stacks needing external resources use `init-` prefixed sidecar services. These run idempotent setup (DB roles, LDAP users) and exit cleanly. The `*deploy-init` anchor (`condition: on-failure`, `failure_action: continue`, `monitor: 0s`) lets Swarm treat exit 0 as "done" without restart loops or false rollbacks. Provisioner credentials come from `GLOBAL_SECRETS`.

## Python Library

Task logic lives in the `swarm` Python package at `.mise/lib/swarm/`, invoked by mise tasks as `python3 -m swarm.<module>`. `PYTHONPATH` is set in mise `[env]` to `.mise/lib`.

### Internal modules (prefixed `_`)

| Module | Purpose |
|--------|---------|
| `_compose` | Compose config preprocessing (anchor concatenation + docker compose config) |
| `_docker` | Docker CLI subprocess wrappers — all docker calls go through here |
| `_ssh` | SSH execution helpers for remote node commands |
| `_output` | Logging and output formatting (data to stdout, diagnostics to stderr) |
| `_sops` | SOPS decryption — calls sops binary, handles `_B64` suffix |
| `_stack` | Stack name resolution (`NN_` prefix stripping) and directory discovery |

### Public modules (CLI entry points)

| Module | Task | Purpose |
|--------|------|---------|
| `deploy` | `swarm:deploy` | Deploy preparation: secrets, builds, env exports |
| `convergence` | `swarm:deploy` | Post-deploy convergence polling and health checks |
| `remove` | `swarm:remove` | Stack removal with drain wait |
| `status` | `status` | Cluster node and stack health display |
| `validate` | `swarm:validate` | Compose validation and bind mount path checks |
| `cleanup` | `swarm:cleanup` | Orphaned secret/config removal, node pruning |
| `networks` | `swarm:init-networks` | Overlay network discovery and creation. `SWARM_INTERNAL_NETWORKS` (space-separated) controls which get `--internal` |
| `nodes` | (library) | Swarm node discovery and placement constraint matching |
| `secrets` | (library) | Secret parsing, validation, and versioned creation |
| `site` | `site:deploy-infra`, `site:deploy-apps`, `site:reset` | Site-wide orchestration |
| `registry_auth` | `registry:auth` | Registry login across swarm nodes |

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

`compose-validate` runs the full pipeline (anchors + compose config + sed + `docker stack config`) and checks bind mount paths on target nodes.

## Adding a New Stack

1. Create `stacks/<namespace>/<stack-name>/compose.yml`
2. Follow compose conventions (see existing stacks as reference)
3. Add `secrets.env` if needed → `mise run sops:encrypt`
4. Add `secrets.yml` / `configs.yml` if using versioned secrets/configs
5. For infra: prefix folder with `NN_` for deploy ordering
6. For Postgres consumers: add `init-db` sidecar (see `stacks/infra/60_accounts/compose.yml`)
7. Validate: `mise run validate`

App stacks are auto-discovered. A `.nodeploy` file opts out of bulk `site:deploy-apps`.
