---
description: Traefik routing and configuration patterns for Swarm stacks
# paths:
#   - '**/compose.yml'
#   - 'stacks/**/config/traefik/*'
---

# Traefik Patterns

## Labels Placement

Traefik labels MUST be under `deploy.labels`, not at service level. Swarm ignores service-level labels.

```yaml
services:
  myservice:
    deploy:
      labels:
        - "traefik.enable=true"
        - "traefik.http.routers..."
```

**`traefik.docker.network` labels not needed** — each gateway's CLI config sets a provider-level `network` default (`infra_gw-external` / `infra_gw-internal`). Dual-gateway services work because each gateway resolves backends via its own default.

## Static Config

CLI flags in compose `command:` section — NOT a config file. Traefik v3 static config sources are mutually exclusive (file/CLI/env). When `--configFile` is set, all CLI flags and env vars are silently ignored. CLI flags allow Docker Compose `${VAR}` interpolation for domain variables.

## Dynamic Config

File provider with Docker Configs. Go template `{{ env "VAR" }}` syntax works in dynamic config files.

## Provider

Use `providers.swarm` (not `providers.docker` with `swarmMode: true`).

## Routing

Dual Traefik gateways — external (`infra/gateway-external`, `DOMAIN_PUBLIC`) and internal (`infra/gateway-internal`, `DOMAIN_PRIVATE`). Both use unified `websecure` entrypoint on `:443` — routing correctness via Host rules and DNS, not entrypoint names.

Dashboard: internal gateway only at `traefik.DOMAIN_PRIVATE`. Disabled on external gateway.

## Caveats

**Middleware chain failures are silent.** If a middleware in an entrypoint's default chain fails to initialize (missing database, broken config), Traefik cannot create ANY routers on that entrypoint. Symptom: 404 for all routes, not an error page. Check `docker service logs` for the actual error.

**Custom entrypoint wrappers must preserve stock entrypoint.** Chain via `exec /entrypoint.sh "$@"`, not `exec traefik "$@"`. The stock entrypoint normalizes CLI arguments.
