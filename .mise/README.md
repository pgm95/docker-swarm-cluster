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
    _*.py                 #   Internal: cli helper, docker CLI, SSH, SOPS, compose, output, stack resolution
    *.py                  #   User-facing: deploy, convergence, status, validate, cleanup, nodes, etc.
  tests/                  # Pytest suite (mocked Docker/SSH, no live cluster needed)
```

Three layers split the work cleanly:

- **mise** owns env loading (SOPS, tool versions), task entry points, `depends`/`depends_post` ordering, and `confirm` gates.
- **bash inside task `run` blocks** owns per-stack iteration (over positional args or filesystem globs) and failure collection.
- **The Python lib** owns per-stack work (`deploy_stack`, `remove_stack`) and cluster-wide ops (`cleanup`, `status`, `networks`, `registry_auth`, `validate`).

Python only ever operates on **one stack at a time**. Multi-stack iteration lives in mise tasks' bash, never in Python. `site.py` no longer exists — `site:*` tasks are pure mise+bash that shell out to single-stack Python invocations and report which stacks failed.

The Python package centralizes Docker CLI and SSH calls through `_docker.py` and `_ssh.py`, making the logic testable without a live cluster. Mise provides the environment (`PYTHONPATH`, SOPS keys, Docker host) and the task interface (`mise run swarm:deploy ...`).

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
| `SWARM_STACKS_DIR`, `SWARM_ANCHORS_FILE` | Plaintext | `.mise/config.toml` (base) |

### Tunable Knobs

Operational defaults set on individual tasks via `env.<NAME>`:

| Variable | Where set | Default | Purpose |
|----------|-----------|---------|---------|
| `SWARM_STACKS_DIR` | `[env]` (base) | `<project>/stacks` | Root of the stacks tree. The lib enumerates immediate subdirs as namespaces. |
| `SWARM_ANCHORS_FILE` | `[env]` (base) | `<SWARM_STACKS_DIR>/_shared/anchors.yml` | Optional shared YAML anchors file concatenated before each stack's `compose.yml`. Missing file = no anchor concatenation (silent skip). |
| `CONVERGE_TIMEOUT` | `swarm:deploy` task | `180` | Seconds to wait for stack convergence before failing the deploy |
| `CONVERGE_MAX_INTERVAL` | `swarm:deploy` task | `15` | Cap on the polling interval. Polling starts at 2s and grows 1.5× per iteration up to this cap. Larger = fewer docker calls during slow deploys, longer worst-case detection latency |
| `STATUS_SERVICE_WRAP` | `status` task | `2` | Max words per line in the SERVICES column of `mise run status` |
| `SWARM_INTERNAL_NETWORKS` | `swarm:init-networks` task | `infra_socket` | Space-separated overlay networks to create with `--internal` |
| `SWARM_OVERLAY_MTU` | `swarm:init-networks` task | `1280` | VXLAN MTU for newly created overlay networks (set lower than path MTU to account for VXLAN encapsulation; 1280 fits inside Tailscale's 1280-byte underlay) |

## Stacks Tree Contract

The lib operates on a stacks tree of shape:

```text
<SWARM_STACKS_DIR>/
  <namespace>/                # Any non-underscore-prefixed subdir
    <stack>/                  # Optional NN_ numeric prefix is stripped from the Swarm stack name
      compose.yml             # REQUIRED
      secrets.env             # OPTIONAL — SOPS-encrypted env file for stack-local secrets
      build/<service>/        # OPTIONAL — Dockerfile context, content-hash tagged automatically
    ...
  _shared/                    # Underscore-prefixed dirs are skipped during enumeration
    anchors.yml               # Default location for SWARM_ANCHORS_FILE
```

The lib has no awareness of which namespace is which (no special-casing for `infra` vs `apps`). Deploy ordering across stacks is the caller's responsibility — `site:deploy-infra` and `site:deploy-apps` simply iterate filesystem globs over their respective directories.

**Per-stack file vocabulary the lib touches:** `compose.yml`, `secrets.env`, `build/`. Nothing else. How a stack organizes its compose document — inlined, split via compose's `include:`, anchors, multi-`-f` — is the user's choice. The lib only ever sees the rendered output of `docker compose config`.

## Compose Preprocessing

Docker Swarm doesn't natively support cross-file YAML anchors. `compose_config()` in `_compose.py` bridges this by concatenating an optional shared anchors file with the stack's `compose.yml` and piping the result through `docker compose config` on stdin:

```text
[SWARM_ANCHORS_FILE if present] + <stack>/compose.yml
    → pipe combined YAML to: docker compose --project-directory <stack-dir>
                                            --project-name <name>
                                            -f - config
    → fixup: strip 'name:', unquote stringified integers
    → consumed by docker stack deploy and by all discovery (secrets, configs, networks)
```

`--project-name` uses the folder name with `NN_` prefix stripped, so default network names match the Swarm stack name. The anchors file is read once per process (cached via `@functools.cache`) since `validate` and bulk-deploy paths render compose 20-30 times.

`docker compose config` stringifies certain integer fields that `docker stack deploy` requires as raw integers. `_fixup_config()` in `_compose.py` corrects this automatically before returning output. Currently fixes:

- `name:` property at root level (rejected by stack deploy)
- `published: "443"` → `published: 443` (port numbers)
- `size: "10485760"` → `size: 10485760` (tmpfs size)

**Compose `include:` is fully supported.** The `docker compose config` invocation resolves any `include:` directives the stack uses, so a compose document split across `compose.yml`, `secrets.yml`, `configs.yml`, etc. is merged before anything reaches the lib.

**Docker Config note:** `docker compose config` resolves `file:` paths to absolute local paths but does NOT inline contents. `docker stack deploy` reads files from local disk at deploy time. Config file contents cannot be modified by sed/envsubst in the piped output — preprocessing must happen on source files before `docker compose config` runs.

## Deploy Pipeline

`mise run swarm:deploy <stack> [<stack> ...] [--update]` accepts one or more stacks. The bash wrapper iterates and calls `python3 -m swarm.deploy <stack>` per item, collecting which stacks failed.

The `<stack>` argument accepts a bare stack name (`metrics`), a directory name (`40_metrics`), or a full path. `resolve_stack_path()` walks `find_namespaces()` (alphabetical order) looking for a match.

A single Python invocation runs the full per-stack pipeline in-process:

1. **Prepare** — sets `STACK_NAME`/`STACK_PATH`/`DEPLOY_VERSION`, decrypts `secrets.env` once into env vars, discovers `build/<service>/` directories and builds+pushes images.
2. **Render** — concatenates the shared anchors file (if any) with the stack's `compose.yml` and runs `docker compose config` to produce both the YAML form (for `docker stack deploy -c -`) and the JSON form (for discovery).
3. **Discover from rendered JSON** — walks the document for:
    - `secrets.<x>.name` ending in `_<DEPLOY_VERSION>` → versioned Docker secrets to create
    - `configs.<x>.file` paths → must exist on disk
4. **Stack deploy** — `docker stack deploy --detach --prune --with-registry-auth --resolve-image changed -c -`. The `changed` mode only re-resolves digests when the image string in compose actually changes; unrelated stack edits leave the previously pinned digest alone. Pass `--update` to switch to `--resolve-image always` for intentional upgrades of floating tags (`latest`, `release`).
5. **Convergence verify** — polls until services converge (default 180s, configurable via `CONVERGE_TIMEOUT`), then reports any unhealthy services. Polling sleep starts at 2s and grows 1.5× per iteration up to `CONVERGE_MAX_INTERVAL` (default 15s) — fast deploys stay responsive, slow ones avoid flooding the Swarm manager.

`deploy_stack()` returns 0 on success and 1 on any failure. When something goes wrong, the failing phase emits a clear `error()` line describing the cause (`Convergence timeout after 180s`, `docker stack deploy returned non-zero`, `Missing required secrets: AUTHENTIK_SECRET_KEY`, etc.) before returning. The bash wrapper just collects the failed stack names.

## Strict I/O Contract

- **stdout**: machine-parseable data only. `swarm.status`, `swarm.networks list`, and `swarm.nodes list` are the canonical stdout emitters.
- **stderr**: everything humans read (deploy progress, build/push streams, summaries, warnings, errors).

The Python lib's `_output.info()`/`warn()`/`error()` route to stderr via `logging.StreamHandler(sys.stderr)`. Subprocess streams (`docker build`, `docker push`, `docker stack deploy`) get `stdout=sys.stderr` so their progress doesn't pollute the data channel.

For `docker stack deploy` specifically, the lib enables a line-prefixed mode (`stream(line_prefixed=True)` in `_docker.py`) that reads the subprocess output line by line and prepends `_output.get_stack_prefix()`. This makes Docker's own progress messages (`Creating service X`, `Updating config Y (id: ...)`) carry the `[stackname]` prefix, matching our own `info()` calls and keeping multi-stack output cleanly attributed. Build/push streams stay in raw byte-pass-through mode so progress bars (`\r`-overwriting) keep their dynamic refresh.

The prefix itself lives in a `contextvars.ContextVar`, so it's properly context-local rather than a process-wide mutable global. Each Python invocation handles one stack, so context isolation isn't load-bearing today, but the shape is right for any future concurrency.

Every `echo` inside a mise task `run` block redirects to `>&2` unless the task is intentionally producing pipeable data. The full rule lives in `.claude/rules/mise-tasks.md`.

Practical effect:

```bash
mise run swarm:deploy mealie 2>&1 | tee deploy.log   # captures all human output
mise run status > snapshot.txt                       # captures just the table
mise run swarm:deploy mealie | jq                    # empty pipe (correct; deploy has no machine output)
```

### Secrets Pipeline

Secrets reach containers in two modes: **versioned Swarm secrets** (at `/run/secrets/`) or **env var injection** (compose interpolation). The mode is determined by whether the rendered compose document references `${DEPLOY_VERSION}` in any `secrets.<x>.name` field.

#### Env var injection (no `${DEPLOY_VERSION}` in rendered compose)

Mise decrypts all SOPS files into env vars before any task runs. Compose `${VAR}` references resolve against this environment. No Docker secrets are created.

#### Versioned Swarm secrets

The deploy task creates immutable Docker secrets named `<key>_<deploy_version>`. Discovery walks the **rendered compose JSON** for `secrets.<x>.name` fields ending with `_<DEPLOY_VERSION>` — wherever those entries originate (inlined in `compose.yml`, brought in via `include:`, anchored from a shared file) the lib only sees the merged result.

Values are resolved from two sources in priority order:

1. **`secrets.env`** (stack-local) — SOPS-decrypted at deploy time. Use for secrets scoped to a single stack.
2. **Environment variables** (global) — already loaded by mise from `shared.yaml` + `{env}.yaml`. Use for secrets shared across stacks or that differ per environment.

Stack-local secrets always take precedence over global env vars when both have the same name.

#### Example: global secret as versioned Docker secret

Add the secret to a SOPS secrets file loaded by mise (shared or per-env):

```yaml
# .secrets/prod.yaml
GLOBAL_CF_ACME_API_TOKEN_PRIVATE: <token>
```

Reference it as a versioned Docker secret. You can put this in `compose.yml` directly, or in any included file:

```yaml
# compose.yml — or split into a sibling file pulled in via `include:`
secrets:
  cf_token:
    name: global_cf_acme_api_token_private_${DEPLOY_VERSION}
    external: true

services:
  myapp:
    environment:
      - CF_TOKEN_FILE=/run/secrets/cf_token
    secrets:
      - cf_token
```

The deploy pipeline renders the compose, finds `cf_token` named `global_cf_acme_api_token_private_<version>`, looks up `GLOBAL_CF_ACME_API_TOKEN_PRIVATE` in the environment, creates the Docker secret, and Swarm mounts it at `/run/secrets/cf_token`.

#### Validation

`validate_required_secrets()` walks the rendered compose for versioned secret names and confirms each base name resolves to a value in `secrets.env` or an env var (uppercased). Missing secrets fail the deploy before any Docker operations.

### Custom Image Builds

Stacks with `build/<service>/` directories trigger automatic builds. Tags are content-based (12-char SHA-256 of build context, excluding `.md` files). The hash inputs are each file's relative path, contents, and `st_mode` — so an `entrypoint.sh` getting `chmod +x` produces a different tag than the same content without the exec bit, avoiding false cache hits.

```text
Image:    ${GLOBAL_SWARM_OCI_REGISTRY}/<stack>/<service>:<content-hash>
Env var:  OCI_TAG_<SERVICE>=<content-hash>
```

The deploy task checks the registry first (`docker manifest inspect`); existing images skip the build.

### Deployment Order

The Python lib has no notion of deployment order across stacks; it acts on whatever stack is given. Order is the calling task's choice:

- `site:deploy-infra` iterates `<SWARM_STACKS_DIR>/infra/*/` alphabetically (the `NN_` numeric prefix on stack folder names is the convention used to express order in this project).
- `site:deploy-apps` iterates `<SWARM_STACKS_DIR>/apps/*/` alphabetically, skipping any directory containing a `.nodeploy` marker file.

Other namespace conventions (`platform/`, `infra/`, `services/`, etc.) are valid; create a corresponding `site:deploy-<ns>` task wrapping the same `failed=()` loop pattern.

### Init Sidecars

Stacks needing external resources use `init-` prefixed sidecar services. These run idempotent setup (DB roles, LDAP users) and exit cleanly. The `*deploy-init` anchor (`condition: on-failure`, `failure_action: continue`, `monitor: 0s`) lets Swarm treat exit 0 as "done" without restart loops or false rollbacks. Provisioner credentials come from shared SOPS secrets (env var injection).

## Python Library

Task logic lives in the `swarm` Python package at `.mise/lib/swarm/`, invoked by mise tasks as `python3 -m swarm.<module>`. `PYTHONPATH` is set in mise `[env]` to `.mise/lib`.

### Internal modules (prefixed `_`)

| Module | Purpose |
|--------|---------|
| `_cli` | Shared `cli_main(work)` wrapper for module CLI entry points: `setup()` logging + `SwarmError` formatting in one place |
| `_compose` | Compose config preprocessing. `compose_config(path)` returns rendered YAML (anchors concatenation + `docker compose config` via stdin + stack-deploy fixups). `compose_json(path)` returns the same render parsed to a dict — canonical entry point for any code that inspects the rendered compose structurally (secret/config/network discovery, bind-mount extraction, placement constraints). Anchors content cached per resolved path via `@functools.cache`. |
| `_docker` | Docker CLI subprocess wrappers — all docker calls go through here. Resource helpers (`secret_*`, `config_*`, `network_*`) follow a `list()` / `rm() -> bool` pattern. `stream(line_prefixed=True)` adds `[stackname]` to each line of subprocess output (used for `docker stack deploy`); on non-zero exit, the captured tail of output is surfaced through `DockerError.stderr`. Includes `task_name_to_service()` and `parse_replicas()` helpers |
| `_ssh` | SSH execution helpers for remote node commands. `parallel_run(items, fn)` is the canonical fan-out helper — bounded ThreadPoolExecutor, returns a per-item result dict preserving identity regardless of completion order. Used by `cleanup`, `registry_auth`, and `validate` for cluster-wide SSH workloads |
| `_output` | Logging and output formatting (strict I/O contract: data to stdout, diagnostics to stderr). Stack-name prefix held in a `ContextVar`; set via `init_stack_prefix(name)`, read via `get_stack_prefix()` |
| `_sops` | SOPS decryption — calls sops binary, handles `_B64` suffix |
| `_stack` | Stacks-tree discovery: `stacks_root()`, `find_namespaces()` (excludes `_*` dirs), `find_stacks()`, `resolve_stack_path()`, `stack_name()` (`NN_` prefix stripping). `oci_tag_var(service)` is the single source of truth for the `OCI_TAG_<SERVICE>` env-var formula used by both `deploy.discover_build_dirs` and `validate._set_oci_tags` |

### Public modules (CLI entry points)

| Module | Task | Purpose |
|--------|------|---------|
| `deploy` | `swarm:deploy` | Self-contained per-stack deploy: prepare → render → discover → deploy → converge. Discovery (versioned secrets, configs to validate) walks the rendered compose JSON. |
| `convergence` | (library + CLI) | Convergence polling + replica-health verification in one call. Polling uses exponential backoff (2s initial → `max_interval`, 1.5x ramp). CLI: `python3 -m swarm.convergence <stack> [--timeout N] [--max-interval N]` |
| `remove` | `swarm:remove` | Stack removal with drain wait |
| `status` | `status` | Cluster node and stack health display. Walks `find_namespaces()` for stack discovery; O(N) service-stack matching via `partition("_")` (assumes no underscores in stack names) |
| `validate` | `swarm:validate` | Compose validation, config-file existence check (from rendered JSON), and bind-mount path checks (parallel SSH; YAML+JSON cached per file) |
| `cleanup` | `swarm:cleanup` | Three phases: (1) prune unused versioned secrets/configs, (2) prune orphaned swarm-scoped overlay networks (excludes `ingress`), (3) `docker system prune --all --volumes --force` per node (parallel SSH). All phases use Docker's "in use" check as the safety net — items currently attached to running services are skipped. |
| `networks` | `swarm:init-networks` | Walks every stack's rendered compose for `networks.*.external == true` entries and creates them on the cluster. `SWARM_INTERNAL_NETWORKS` (space-separated) controls which get `--internal`. `SWARM_OVERLAY_MTU` sets the VXLAN MTU at creation time |
| `nodes` | (library) | Swarm node discovery and placement constraint matching |
| `secrets` | (library) | Compose-JSON-driven secret/config discovery: `required_versioned_secrets()`, `validate_required_secrets()`, `create_versioned_secrets()`, `validate_config_files()`, `referenced_config_files()`. Accepts pre-decrypted `(key, value)` pairs to avoid double SOPS calls. |
| `registry_auth` | `site:registry` | Registry login across swarm nodes (parallel SSH) |

Site-wide tasks (`site:deploy-infra`, `site:deploy-apps`, `site:drain`) are pure mise+bash — they iterate the stacks directory and call `python3 -m swarm.deploy`/`python3 -m swarm.remove` per stack. There is no `site` Python module.

### Testing

Tests live at `.mise/tests/`, configured via `.config/pyproject.toml`. All Docker/SSH calls are mocked at the subprocess boundary — no live cluster required.

```bash
pytest          # run via mise env
mise run validate   # includes pytest via pre-commit hook
```

### Error handling

All modules use a `SwarmError` exception hierarchy (`DockerError`, `SSHError`, `SopsError`, `SecretError`, `ValidationError`). The `_cli.cli_main(work)` wrapper catches `SwarmError` from any module's CLI entry point and routes the message through `_output.error()` (stderr, non-zero exit). Unexpected exceptions produce full tracebacks.

```python
# Every public module's main() follows this shape:
def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(...)
        args = parser.parse_args()
        return do_thing(args)
    return cli_main(run)
```

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

1. Create `<SWARM_STACKS_DIR>/<namespace>/<stack-name>/compose.yml`
2. Follow compose conventions (see existing stacks as reference)
3. Add stack-specific secrets to `secrets.env` → `mise run sops:encrypt`
4. Define secrets/configs/networks however your compose document is organized — inlined in `compose.yml`, split via compose's `include:` directive, or anchored from a shared file. The lib only sees the rendered output, so any compose-spec-native composition works.
5. For SOPS globals (shared or per-env), reference them directly in your secrets block using the lowercased env-var name suffixed with `_${DEPLOY_VERSION}`
6. Apply ordering by prefixing folder with `NN_` if your iteration loop sorts alphabetically
7. For Postgres consumers: add an `init-db` sidecar (project-internal pattern, see existing infra stacks)
8. Validate: `mise run validate`

App stacks are auto-discovered. A `.nodeploy` file opts out of bulk `site:deploy-apps`.
