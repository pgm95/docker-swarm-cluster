# Tooling Workflow

## Architecture Overview

The project uses a layered tooling approach: **mise** for task orchestration, **pre-commit** for validation gates, and **SOPS** for secrets management. All tools share common patterns and integrate through shell scripts.

## Task Categories

| Category | Tasks | Purpose |
|----------|-------|------------|
| Site | `site:deploy`, `site:deploy-infra`, `site:deploy-apps`, `site:reset` | Full-cluster deploy/teardown |
| Deployment | `swarm:deploy`, `swarm:remove` | Single-stack deploy/remove |
| Status | `swarm:status` | Cluster nodes, stack health, and node placement |
| Bootstrap | `swarm:init-networks` (hidden) | Overlay network creation (auto-run by `site:deploy-infra`) |
| Cleanup | `swarm:cleanup` | Remove unused versioned secrets/configs (manager), prune containers and images (all nodes via SSH) |
| Registry | `registry:auth` | Login all swarm nodes to private registry |
| Validation | `swarm:validate` (hidden) | Compose + Swarm compatibility validation |
| SOPS | `sops:init`, `sops:encrypt`, `sops:edit`, `sops:status` | Encryption management |

## Shared Scripts

| Script | Function | Used by |
|--------|----------|---------|
| `compose-config.sh` | `compose_config <file> [args...]` — concatenates shared anchors, sets `--project-name` (strips `NN_` prefix) | `swarm:deploy`, `swarm:validate` |
| `content-hash.sh` | `compute_content_hash <dir>` — 12-char SHA-256 of build context | `swarm:deploy`, `swarm:validate` |
| `deploy-convergence.sh` | `wait_for_convergence()`, `check_replica_health()` — stack convergence and health | `swarm:deploy` |
| `deploy-secrets.sh` | `validate_required_secrets()`, `create_versioned_secrets()`, `validate_config_files()` | `swarm:deploy` |
| `find-secret-files.sh` | `find_secret_files()` — discover SOPS-managed files | `sops:encrypt`, `sops:status` |
| `resolve-stack.sh` | `stack_name()` strips `NN_` folder prefix, `find_stacks()` ordered directory discovery | `swarm:deploy`, `swarm:remove`, `swarm:status`, `site:deploy-infra`, `site:deploy-apps`, `site:reset` |
| `resolve-networks.sh` | `get_infra_networks()`, `is_internal_network()` — dynamic overlay network discovery from compose files | `swarm:init-networks`, `site:reset` |
| `resolve-nodes.sh` | `get_swarm_nodes()`, `get_service_node()`, `ssh_node()`, `ssh_node_stdin()` — dynamic node discovery from swarm API | `swarm:validate`, `swarm:cleanup`, `registry:auth`, `site:reset` |
| `sops-decrypt.sh` | `sops_decrypt <file>` — decrypt SOPS file, output key=value lines | `sops-export.sh`, `swarm:deploy` |
| `sops-export.sh` | `sops_export <file>` — decrypt + export as env vars (handles `_B64` suffix) | `swarm:deploy` |

`sops-export.sh` auto-sources `sops-decrypt.sh` via `BASH_SOURCE` relative path.

Shared scripts are pure function libraries with no hardcoded configuration. Operational knobs (timeouts, network flags) are set via task-level `env` and read from env vars in the scripts.

## Pre-commit Integration

Location: `.config/pre-commit.yaml`

| Hook | Trigger | Action |
|------|---------|--------|
| `compose-validate` | `^stacks/(.*/compose\.yml\|_shared/anchors\.yml)$` | Calls `mise run swarm:validate` |
| `check-secrets-encrypted` | `^secrets\.yaml$\|secrets\.env$` | Verifies SOPS marker present: `sops_` (dotenv) or `sops:` (YAML) |
| `yamllint` | All YAML (excl `.secrets/`) | Syntax/style validation; `forbid-undeclared-aliases: false` allows centralized anchor references |
| `markdownlint-cli2` | All Markdown | Documentation linting |
| `taplo-lint` | All TOML | TOML linting (ComPWA/taplo-pre-commit v0.9.3) |
| `gitleaks` | All files | Secret detection |

### Validation Pipeline

Pre-commit runs `swarm:validate` which:

1. Runs `compose_config()` to concatenate shared anchors, resolve includes, and interpolate variables
2. Strips `name:` line (not supported by `docker stack config`)
3. Converts string ports to integers (`published: "443"` → `published: 443`)
4. Pipes to `docker stack config` for Swarm compatibility validation
5. Checks bind mount paths exist on target nodes (warns, does not fail)

## Config Files

- `.config/miserc.toml` — sets `MISE_ENV`, loaded first
- `.mise/config.toml` — base config (shared vars, tools, tasks, shared `_.file`)
- `.mise/config.dev.toml` — dev profile (env `_.file` + plaintext node vars + derived OCI registry)
- `.mise/config.prod.toml` — prod profile (same structure, placeholder values)

## Processing Order

mise processes base config `[env]` BEFORE profile `[env]`. This means:

- Tera templates in base config CANNOT reference vars from profile configs
- `GLOBAL_SWARM_OCI_REGISTRY = "registry.{{ env.DOMAIN_PRIVATE }}"` must live in each profile file (not base) because `DOMAIN_PRIVATE` comes from the profile's `_.file`
- Both `_.file` directives (base + profile) are processed additively — vars from both sources coexist

## Variable Mapping

| Variable | Source | Location |
|----------|--------|----------|
| `DOMAIN_PUBLIC`, `DOMAIN_PRIVATE`, `GLOBAL_OIDC_URL`, `GLOBAL_LDAP_BASE_DN` | SOPS | `PROJECT_SECRETS_DIR/{env}.yaml` |
| `SWARM_NODE_DEFAULT`, `DOCKER_HOST`, `SWARM_SSH_USER` | Plaintext | `.mise/config.{env}.toml` |
| `GLOBAL_SWARM_OCI_REGISTRY` | Derived from `DOMAIN_PRIVATE` | `.mise/config.{env}.toml` |
| `GLOBAL_SMTP_*`, `REGISTRY_*`, `GLOBAL_LDAP_ADDRESS` | SOPS | `PROJECT_SECRETS_DIR/shared.yaml` |
| `GLOBAL_TZ`, `GLOBAL_NONROOT_*`, etc. | Plaintext | `.mise/config.toml` (base) |

## Key Decisions

1. **Preprocessing over native support**: `compose_config() | docker stack deploy -c -` enables full Compose spec features
2. **Centralized anchors**: All `x-logging`, `x-place-*`, `x-deploy*`, `x-resources-*` anchors in `stacks/_shared/anchors.yml`, concatenated via `compose_config()`
3. **Single validation task**: `swarm:validate` is the single source of truth, called by both mise and pre-commit
4. **Versioned secrets**: Immutable Swarm secrets with timestamp suffix enable zero-downtime rotation
5. **Dynamic stack discovery**: Both `site:deploy-infra` and `site:deploy-apps` use bash loops with `find_stacks()` for auto-discovery. Infra order is encoded in `NN_` folder prefixes; `stack_name()` strips the prefix for Swarm
6. **Sourced functions over task decomposition**: Deploy phases that share env vars (secrets, `DEPLOY_VERSION`, `OCI_TAG_*`) stay in one process via sourced function libraries. Mise task references run in separate processes and can't share env.
7. **Discovery over duplication**: Overlay networks discovered from compose files (`resolve-networks.sh`), swarm nodes discovered from API (`resolve-nodes.sh`). No parallel hardcoded lists to maintain.
8. **Configuration via task-level `env`**: Operational knobs (timeouts, network flags) are task-level `env` defaults, not hardcoded in scripts. Overridable via environment.
9. **Fail-fast deployment**: SOPS errors abort immediately; deploy verifies convergence (configurable timeout) before returning success
10. **Site-level automation**: `site:deploy` and `site:reset` encode the full deployment order — no manual sequencing. `site:reset` requires `confirm` before proceeding.

## Reliability Patterns

Deploy task features:

- Global secrets auto-injected by mise `_.file` SOPS integration (no manual decrypt)
- Compose preprocessing via `compose_config()` — concatenates shared anchors with stack compose file
- Auto-build: detects `build/*/` dirs, content-hash tags, builds+pushes if image is new
- Pre-flight secret validation
- SOPS decryption errors abort with captured error message
- Post-deploy convergence wait (180s timeout)
- Failed task detection with actionable output
