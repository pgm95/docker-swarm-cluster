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

Needed for: Docker `--format`, bash `${#array[@]}`, `${var//pattern/replace}`.

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

## Organization

- **Hidden helpers**: `hide = true` for internal tasks (e.g., `swarm:validate`, `swarm:init-networks`, `swarm:init-volumes`)
- **Shared scripts**: Reusable bash functions in `.mise/tasks/scripts/`. Tasks source only what they need. Sourced scripts run in-process and can `export` vars; hidden tasks spawn subshells and cannot.
- **Task files**: `tasks/swarm.toml` (stack operations), `tasks/site.toml` (cluster-wide operations), `tasks/sops.toml` (encryption)
- **`.nodeploy` opt-out**: A `.nodeploy` file in a stack root opts it out of `site:deploy-apps`. Stack remains validated and deployable via manual `swarm:deploy`.

## Deploy Versioning

Stacks with `${DEPLOY_VERSION}` in any yml file trigger automatic secret/config creation:

- SOPS var `AUTHELIA_JWT_SECRET` → swarm secret `authelia_jwt_secret_<sha>_<ts>`
- Config file `./config/authelia/configuration.yml` → swarm config `authelia_config_<sha>_<ts>`
- `swarm:deploy` loads stack secrets, creates versioned secrets, deploys with `--with-registry-auth`
