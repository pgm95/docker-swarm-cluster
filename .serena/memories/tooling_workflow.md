# Tooling Workflow

## Architecture Overview

The project uses a layered tooling approach: **mise** for task orchestration, **pre-commit** for validation gates, and **SOPS** for secrets management. All tools share common patterns and integrate through shell scripts.

## Task Categories

| Category | Tasks | Purpose |
|----------|-------|------------|
| Site | `site:deploy`, `site:deploy-infra`, `site:deploy-apps`, `site:reset` | Full-cluster deploy/teardown |
| Deployment | `swarm:deploy`, `swarm:remove` | Single-stack deploy/remove |
| Status | `swarm:status` | Cluster nodes, stack health, and node placement |
| Bootstrap | `swarm:init-networks` (hidden), `swarm:init-volumes` (hidden) | Cluster initialization (auto-run by `site:deploy-infra`) |
| Cleanup | `swarm:cleanup` | Remove unused versioned secrets/configs (manager), prune containers and images (all nodes via SSH) |
| Registry | `registry:auth` | Login all onprem nodes to private registry (cloud node excluded — no DNS resolution) |
| Validation | `swarm:validate` (hidden) | Compose + Swarm compatibility validation |
| SOPS | `sops:init`, `sops:encrypt`, `sops:edit`, `sops:status` | Encryption management |

## Shared Scripts

| Script | Function | Used by |
|--------|----------|---------|
| `compose-config.sh` | `compose_config <file> [args...]` — concatenates shared anchors with compose file | `swarm:deploy`, `swarm:validate`, `swarm:init-volumes` |
| `content-hash.sh` | `compute_content_hash <dir>` — 12-char SHA-256 of build context | `swarm:deploy`, `swarm:validate` |
| `sops-decrypt.sh` | `sops_decrypt <file>` — decrypt SOPS file, output key=value lines | `sops-export.sh`, `swarm:deploy` |
| `sops-export.sh` | `sops_export <file>` — decrypt + export as env vars (handles `_B64` suffix) | `swarm:deploy` |

`sops-export.sh` auto-sources `sops-decrypt.sh` via `BASH_SOURCE` relative path.

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
| `SWARM_NODE_*`, `DOCKER_HOST` | Plaintext | `.mise/config.{env}.toml` |
| `GLOBAL_SWARM_OCI_REGISTRY` | Derived from `DOMAIN_PRIVATE` | `.mise/config.{env}.toml` |
| `GLOBAL_SMTP_*`, `REGISTRY_*`, `GLOBAL_LDAP_ADDRESS` | SOPS | `PROJECT_SECRETS_DIR/shared.yaml` |
| `GLOBAL_TZ`, `GLOBAL_NONROOT_*`, etc. | Plaintext | `.mise/config.toml` (base) |

## Key Decisions

1. **Preprocessing over native support**: `compose_config() | docker stack deploy -c -` enables full Compose spec features
2. **Centralized anchors**: All `x-logging`, `x-place-*`, `x-deploy*`, `x-resources-*` anchors in `stacks/_shared/anchors.yml`, concatenated via `compose_config()`
3. **Single validation task**: `swarm:validate` is the single source of truth, called by both mise and pre-commit
4. **Versioned secrets**: Immutable Swarm secrets with timestamp suffix enable zero-downtime rotation
5. **Shared scripts over hidden tasks**: Sourced bash functions run in-process (can `export` vars), vs hidden tasks that spawn subshells
6. **Fail-fast deployment**: SOPS errors abort immediately; deploy verifies convergence (180s timeout) before returning success
7. **Site-level automation**: `site:deploy` and `site:reset` encode the full deployment order — no manual sequencing

## Reliability Patterns

Deploy task features:

- Global secrets auto-injected by mise `_.file` SOPS integration (no manual decrypt)
- Compose preprocessing via `compose_config()` — concatenates shared anchors with stack compose file
- Auto-build: detects `build/*/` dirs, content-hash tags, builds+pushes if image is new
- Pre-flight secret validation
- SOPS decryption errors abort with captured error message
- Post-deploy convergence wait (180s timeout)
- Failed task detection with actionable output
