# Accounts Stack

Authentication and identity management.

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

All services start concurrently — Swarm has no `depends_on`. Services crash and retry via
deploy restart policy (`max_attempts: 3`, `window: 120s`). Typical first-deploy order:

1. **redis** — starts immediately
2. **init-db** — polls postgres, provisions databases
3. **lldap** — crashes once (DB not ready), succeeds on retry
4. **authelia** — crashes once/twice (redis/lldap/postgres), succeeds on retry
5. **init-ldap** — polls lldap, seeds bind users
6. **webfinger** — starts once lldap is reachable

## Init Sidecars

### init-db (Postgres)

Connects as provisioner role, idempotently creates `authelia` and `lldap` roles and databases.
Passwords from `secrets.env` — single source of truth, no duplication with the postgres stack.

### init-ldap (LDAP)

Seeds bind users needed by Authelia and WebFinger using LLDAP's built-in `/app/bootstrap.sh`.
Generates user config JSON from compose env vars at runtime, writes to `/tmp/bootstrap/user-configs`
(`USER_CONFIGS_DIR` env var). Creates users via GraphQL API, sets passwords via `lldap_set_password`
(OPAQUE protocol). Fully idempotent — skips existing users, updates changed, re-syncs passwords
on every deploy.

| Bind User | Group | Consumer |
|-----------|-------|----------|
| `AUTHELIA_LDAP_BIND_USER` | `lldap_password_manager` | Authelia (password reset access) |
| `CARPAL_LDAP_BIND_USER` | `lldap_strict_readonly` | WebFinger (read-only queries) |

## WebFinger Custom Image

Custom build in `build/webfinger/` — `swarm:deploy` auto-builds with content-hash tags.

The Dockerfile copies `entrypoint.sh` into the image and fixes `/etc/carpal` permissions for
non-root (UID 1000). At startup, the entrypoint reads config templates from `/config/` (Docker
Configs), expands environment variables via sed, writes processed files to `/etc/carpal/`, then
starts carpal. Runs as non-root (`user: ${GLOBAL_NONROOT_DOCKER}`).

Required env vars from `secrets.env`: `CARPAL_LDAP_BIND_USER`, `CARPAL_LDAP_BIND_PASS`.
Required from `GLOBAL_SECRETS`: `GLOBAL_LDAP_BASE_DN`, `GLOBAL_OIDC_URL`.
