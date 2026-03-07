---
description: Patterns for Swarm secrets and encrypted config files
# paths:
#   - '**/secrets.env'
#   - '**/secrets.yml'
---

# Secrets Patterns

## `_B64` Suffix Convention

Multi-line values (PEM keys, certificates) can't be stored in `.env` files. Base64-encode them and use a `_B64` suffix in `secrets.env`:

```
AUTHELIA_OIDC_JWKS_KEY_B64=LS0tLS1CRUdJTi...
```

The deploy task automatically base64-decodes the value and creates the Docker secret under the name without `_B64` (e.g., `authelia_oidc_jwks_key_<version>`).

## `$$` Escaping

Docker secrets store literal values — they are NOT compose-interpolated. Do not use `$$` doubling in `secrets.env` for values that become Docker secrets. `$$` is only needed for values that go through compose interpolation (env var injection mode).

## Shared Secrets Limitation

Versioned secrets cannot be shared across stacks — each `swarm:deploy` generates a unique `DEPLOY_VERSION` (`<git-sha>_<epoch>`).

Shared credentials (SMTP, registry, LDAP) stay in `GLOBAL_SECRETS`, auto-injected as env vars by mise `_.file` SOPS integration. No manual decryption needed in tasks.

## Secret Modes

| Mode | Trigger | Delivery |
|------|---------|----------|
| **Versioned** | `${DEPLOY_VERSION}` in `secrets.yml` | Docker secrets at `/run/secrets/` |
| **Env Var Injection** | No `DEPLOY_VERSION` | SOPS decrypt → compose interpolation |
