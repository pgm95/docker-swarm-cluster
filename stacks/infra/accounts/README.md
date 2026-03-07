# Accounts Stack

Authentication and identity management services.

## Purpose

Provides centralized authentication (Authelia), identity directory (LLDAP), and WebFinger discovery for the entire cluster. Other services integrate via OIDC or forward auth.

## Services

| Service | Purpose | Port |
|---------|---------|------|
| redis | Session storage for Authelia | 6379 |
| lldap | LDAP directory (user/group management) | 389 |
| authelia | Authentication server + OIDC provider | 9091 |
| webfinger | OpenID Connect discovery endpoint | 8008 |
| init-db | Provisions Postgres roles and databases | — |
| init-ldap | Seeds LDAP bind users for Authelia and WebFinger | — |

## Convergence

All services start concurrently — Swarm has no `depends_on`. Services that need unready dependencies crash and retry via the deploy restart policy (`max_attempts: 3`, `window: 120s`). Typical first-deploy convergence:

1. **redis** starts immediately (no external deps)
2. **init-db** polls postgres, provisions databases within seconds
3. **lldap** crashes once (database not yet provisioned), succeeds on retry
4. **authelia** crashes once or twice (redis/lldap/postgres not ready), succeeds on retry
5. **init-ldap** polls lldap, seeds bind users once it responds
6. **webfinger** starts once lldap is reachable

## Prerequisites

- `infra/postgres` deployed (provides `infra_postgres` overlay)
- `infra/gateway-external` deployed (provides `infra_gw-external`)
- `infra/gateway-internal` deployed (provides `infra_gw-internal`)
- `infra/registry` deployed (for WebFinger image)

## Init Sidecars

### init-db (Postgres provisioning)

Connects to `infra/postgres` as the provisioner role and idempotently creates application-specific roles and databases. Passwords come from `secrets.env` via compose interpolation — single source of truth, no duplication with the postgres stack.

1. Waits for Postgres via `pg_isready`
2. Creates roles (`authelia`, `lldap`) with passwords from env vars
3. Creates databases owned by their respective roles
4. Sleeps indefinitely (keeps Swarm convergence at 1/1)

### init-ldap (LDAP bootstrapping)

On fresh deployments, LLDAP starts with only the admin user. Authelia and WebFinger need dedicated bind users, creating a chicken-and-egg failure. The `init-ldap` sidecar solves this:

1. Generates user config JSON from compose-interpolated env vars
2. Polls LLDAP's HTTP API until ready
3. Creates bind users via GraphQL API and sets passwords via `lldap_set_password`
4. Sleeps indefinitely (keeps Swarm convergence at 1/1)

Both sidecars are fully idempotent — safe to re-run on every deploy.

| Bind User | Group | Consumer |
|-----------|-------|----------|
| `AUTHELIA_LDAP_BIND_USER` | `lldap_password_manager` | Authelia (needs password reset access) |
| `CARPAL_LDAP_BIND_USER` | `lldap_strict_readonly` | WebFinger (read-only queries) |
