---
description: Patterns for writing mise task definitions
# paths:
#   - '.mise/**/*.toml'
---

# Mise Task Patterns

## Tera Escaping

`run` scripts are parsed by Tera. Wrap `{{` patterns in raw blocks:

```toml
run = '''{% raw %}
docker service ls --format "{{.Name}}\t{{.Replicas}}"
{% endraw %}'''
```

Needed for: Docker `--format`, bash `${#array[@]}`, `${var//pattern/replace}`. The `#}` in `${#array[@]}` is parsed as a Tera comment end tag.

Moving Go template strings into sourced `.sh` scripts avoids Tera entirely — sourced files are read at bash runtime, not at Tera template time.

## Usage Spec Syntax

Args become `${usage_<name>}` env vars in scripts.

```toml
usage = 'arg "<path>" help="Description"'
usage = '''
arg "<name>" help="Required arg"
arg "[name]" help="Optional" default="value"
'''
```

## Template Scoping

`{{config_root}}`, `{{env.VAR}}` work in `dir` field only. Use `${VAR}` inside `run` scripts.

## Orchestration vs Shell

Mise task references (`run = [{ task = "..." }]`) run in separate processes — env exports don't carry between them.

| Pattern | Use When |
|---------|----------|
| `run = [{ task = "...", args = [...] }]` | Independent sequential steps, no shared state |
| Bash loop with `mise run` | Imperative flow: conditional skipping, soft-failure collection, summary reporting |
| Sourced function libraries | Phases that share runtime env vars (secrets, computed tags, deploy versions) |

**Key constraint**: `swarm:deploy` phases share env vars (`DEPLOY_VERSION`, secrets, `OCI_TAG_*`), so they must stay in one shell process via sourced functions.

## Discovery over Duplication

Derive operational data from the source of truth instead of maintaining parallel lists:

- **Infra stack order**: `NN_` folder prefix, discovered via `find_stacks()`
- **Stack names**: `stack_name()` strips the `NN_` prefix
- **Overlay networks**: discovered from `infra_*: external: true` in compose files
- **Swarm nodes**: discovered from `docker node inspect`

## Configurable Defaults

Use task-level `env` for operational knobs:

```toml
["swarm:deploy"]
env.CONVERGE_TIMEOUT = "180"
```

## Safety Gates

Destructive tasks use mise's `confirm` property:

```toml
["site:reset"]
confirm = "This will remove ALL stacks, secrets, configs, and networks. Continue?"
```

Use for hard-to-reverse operations affecting shared state. Don't use for routine operations like `swarm:cleanup`.

## Organization

- **Hidden helpers**: `hide = true` for internal tasks (e.g., `swarm:validate`, `swarm:init-networks`)
- **Shared function libraries**: `.mise/tasks/scripts/`. Tasks source only what they need. Scripts contain no hardcoded configuration.
- **Task files**: `tasks/swarm.toml` (stack operations + `registry:auth`), `tasks/site.toml` (cluster-wide), `tasks/sops.toml` (encryption)
- **`.nodeploy` opt-out**: File in stack root opts it out of `site:deploy-apps`

## Deploy Versioning

Stacks with `${DEPLOY_VERSION}` in any yml file trigger automatic secret/config creation:

- SOPS var `AUTHELIA_JWT_SECRET` → swarm secret `authelia_jwt_secret_<sha>_<ts>`
- Config file `./config/authelia/configuration.yml` → swarm config `authelia_config_<sha>_<ts>`
- `swarm:deploy` loads stack secrets, creates versioned secrets, deploys with `--with-registry-auth`
