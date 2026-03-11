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

Needed for: Docker `--format`, bash `${#array[@]}`, `${var//pattern/replace}`. The `#}` in `${#array[@]}` is parsed as a Tera comment end tag, causing cryptic parse errors (`expected a comment end`).

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

## Portability

Prefer pure bash over sed for in-place edits:

```bash
content=$(<file); content="${content//old/new}"; printf '%s' "$content" > file
```

## Orchestration vs Shell

Mise task references (`run = [{ task = "..." }]`) run in separate processes — env exports don't carry between them. Choose the right tool:

| Pattern | Use When |
|---------|----------|
| `run = [{ task = "...", args = [...] }]` | Independent sequential steps, no shared state between them |
| Bash loop with `mise run` | Imperative flow needed: conditional skipping, soft-failure collection, summary reporting |
| Sourced function libraries | Phases that share runtime env vars (secrets, computed tags, deploy versions) |

**Key constraint**: `swarm:deploy` phases share env vars (`DEPLOY_VERSION`, secrets, `OCI_TAG_*`), so they must stay in one shell process via sourced functions. `site:deploy-infra` invocations are independent, so they use task list orchestration.

## Discovery over Duplication

Derive operational data from the source of truth instead of maintaining parallel lists:

- **Overlay networks**: discovered from `infra_*: external: true` declarations in compose files (`resolve-networks.sh`), not hardcoded in tasks
- **Swarm nodes**: discovered from `docker node inspect` (`resolve-nodes.sh`), not per-node env vars

When a new `infra_*` network appears in any compose file, `swarm:init-networks` and `site:reset` pick it up automatically.

## Configurable Defaults

Use task-level `env` for operational knobs. This makes them visible in task definitions and overridable via environment without editing code:

```toml
["swarm:deploy"]
env.CONVERGE_TIMEOUT = "180"
```

Script references `${CONVERGE_TIMEOUT}`, not a hardcoded `180`. Security-critical flags (like which networks get `--internal`) also use task-level `env` — the value is explicit and auditable in the task definition.

## Safety Gates

Destructive tasks use mise's `confirm` property:

```toml
["site:reset"]
confirm = "This will remove ALL stacks, secrets, configs, and networks. Continue?"
```

Use `confirm` for operations that are hard to reverse and affect shared state. Don't use it for routine operations like `swarm:cleanup` that only remove stale/unused resources.

## Organization

- **Hidden helpers**: `hide = true` for internal tasks (e.g., `swarm:validate`, `swarm:init-networks`)
- **Shared function libraries**: Reusable bash functions in `.mise/tasks/scripts/`. Tasks source only what they need. Sourced scripts run in-process and can `export` vars; separate tasks run in subshells and cannot. Function libraries contain no hardcoded configuration — they read from env vars set by their calling tasks.
- **Task files**: `tasks/swarm.toml` (stack operations + `registry:auth`), `tasks/site.toml` (cluster-wide operations), `tasks/sops.toml` (encryption)
- **`.nodeploy` opt-out**: A `.nodeploy` file in a stack root opts it out of `site:deploy-apps`. Stack remains validated and deployable via manual `swarm:deploy`.

## Deploy Versioning

Stacks with `${DEPLOY_VERSION}` in any yml file trigger automatic secret/config creation:

- SOPS var `AUTHELIA_JWT_SECRET` → swarm secret `authelia_jwt_secret_<sha>_<ts>`
- Config file `./config/authelia/configuration.yml` → swarm config `authelia_config_<sha>_<ts>`
- `swarm:deploy` loads stack secrets, creates versioned secrets, deploys with `--with-registry-auth`
