# Central Postgres — Service Compatibility Report

Research for consolidating container embedded databases onto a shared `infra/postgres` stack.

## Services With Native Postgres Support

### Authelia (infra/accounts) [COMPLETED AND TESTED]

**Current:** SQLite at `/config/db.sqlite3`, config via Docker Config with Go template secrets.

**Postgres config:** Replace `storage.local` with `storage.postgres` in `configuration.yml`. Fields: `address` (`tcp://host:port`), `database`, `username`, `password`. Additional optional fields: `schema` (default `public`), `timeout`, `tls`.

**Secret delivery — two options:**

- **`_FILE` env vars (recommended for Swarm):** `AUTHELIA_STORAGE_POSTGRES_PASSWORD_FILE`, `AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE` — native support, no config filter needed
- **Go template in config:** `{{ secret "/run/secrets/..." }}` — works but requires `X_AUTHELIA_CONFIG_FILTERS=template` env var. Note: `| trim` is redundant — `secret` already strips trailing newlines

**Quirks:**

- Schema migrations run automatically on startup
- `encryption_key` encrypts data inside the DB (TOTP, WebAuthn) — already configured, unrelated to Postgres. `config.xml` fungible — API key set via env var key must carry over from SQLite
- Redis is optional for single-replica deployments (memory sessions work). Only required for multi-replica HA
- **Databases:** 1

### LLDAP (infra/accounts) [COMPLETED AND TESTED]

**Current:** SQLite at `/data/users.db`, configured via `lldap_config.toml`.

**Postgres config:** `LLDAP_DATABASE_URL=postgres://user:password@host/dbname` env var or `database_url` in toml.

**Quirks:**

- Auto-creates all tables on startup — no migration step needed for fresh Postgres
- Bootstrap sidecar unaffected — uses GraphQL API, not the database
- `/data` volume can be **removed entirely** with Postgres — the SQLite DB moves to Postgres, private key is derived from `LLDAP_KEY_SEED` (deterministic, no file written to disk), LDAPS is disabled, and config is a Docker Config mount. Official docs confirm: "you can remove this step if you use a different DB and configure with environment variables only"
- **Databases:** 1

### CrowdSec (infra/gateway-external) [COMPLETED AND TESTED]

**Current:** SQLite, configured in `config/crowdsec/config.yaml`.

**Postgres config:** Replace `db_config` block in `config.yaml`. Use `type: pgx` (canonical). `postgresql` and `postgres` also work but emit deprecation warnings. Fields: `host`, `port`, `user`, `password`, `db_name`, `sslmode`.

**Quirks:**

- Config parser natively supports `${VAR}` substitution via `StrictExpand` — compose interpolation works but is not the only option. No `_FILE` or Go template support
- No DB env vars in entrypoint — only `USE_WAL` touches `db_config`. DB config must come from `config.yaml` (via Docker Config)
- **Agent** creds re-registered with `--force` every boot — no persistence needed
- **Bouncers** use idempotent name-check — with persistent Postgres, they survive restarts and are NOT re-registered
- **Databases:** 1

#### Volume Strategy

- **`crowdsec-db` — remove.** SQLite moves to Postgres. GeoIP/hub data files are re-downloadable. Requires `CROWDSEC_BYPASS_DB_VOLUME_CHECK=true` env var (entrypoint hard-exits without it)
- **`crowdsec-app` — keep.** Holds hub index/content, CAPI creds, installed parsers/scenarios, and LAPI credentials. Cheap (~20MB), avoids complexity

### Mealie (apps/mealie)

**Current:** SQLite with `SQLITE_MIGRATE_JOURNAL_WAL=true`.

**Postgres config:** Env vars: `DB_ENGINE=postgres`, `POSTGRES_SERVER`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`. Names unchanged across v1/v2.

**Quirks:**

- Native `_FILE` support since v2.7.0 — implemented in upstream `entry.sh`, no custom wrapper needed. Confirm running v2.7.0+
- Auto-creates schema on startup via Alembic, including `pg_trgm` extension for fuzzy search. Central Postgres user needs `CREATE EXTENSION` privilege or `pg_trgm` must be pre-provisioned
- `SQLITE_MIGRATE_JOURNAL_WAL` is SQLite-specific — silently ignored on Postgres, safe to remove
- **Databases:** 1

### Radarr, Sonarr, Prowlarr (apps/servarr)

All three share the NzbDrone framework origin with structurally identical Postgres plumbing, though each maintains its own fork with app-specific section names and defaults.

**Current:** SQLite (default, no explicit config).

**Postgres config:** Env vars using `{APP}__POSTGRES__{FIELD}` convention (double underscore maps to config sections). Fields: `HOST`, `PORT`, `USER`, `PASSWORD`, `MAINDB`, `LOGDB`. Where `{APP}` = `RADARR`/`SONARR`/`PROWLARR`.

**Quirks:**

- **Two databases required per app** (main + log) — hardcoded in `ConnectionStringFactory.cs`. Count is fixed at 2, but names are configurable via `__MAINDB`/`__LOGDB`. Defaults: `{app}-main`, `{app}-log`
- Does NOT auto-create databases — init script must pre-create all 6 databases + roles
- No `_FILE` support — passwords via compose interpolation (SOPS env vars)
- `VACUUM` runs during housekeeping — source code runs plain `VACUUM` (only needs table ownership), but Prowlarr's official wiki explicitly states superuser is required. Radarr/Sonarr wikis don't mention this. Safest to grant database-owner privileges
- API keys set via `{APP}__AUTH__APIKEY` env vars — `config.xml` is fully fungible, no named volumes needed
- **Databases per app:** 2 (main + log), **total:** 6

## Statelessness After Postgres Migration

With DB on central Postgres, some services require less/no named volumes

| Service | Stateless? | Notes |
|---------|:---:|-------|
| **LLDAP** | Yes | Volume removable — DB gone, key from seed, no LDAPS, config via Docker Config |
| **Authelia** | Yes | `/config` only held SQLite + notification state (non-critical, duplicates at worst) |
| **CrowdSec** | Partial | Drop `crowdsec-db` (SQLite → Postgres). Keep `crowdsec-app` |
| **Radarr/Sonarr/Prowlarr** | Yes | `config.xml` fungible — API key set via env var. Radarr/Sonarr need external bind mounts for media |
| **Mealie** | **No** | User-uploaded recipe media in `/app/data` |

## Cross-Cutting Concerns

| Concern | Services Affected | Resolution |
|---------|-------------------|------------|
| **DB/role creation** | All 7 | Infra: postgres init script. Apps: client-side sidecars via provisioner role |
| **`pg_trgm` extension** | Mealie | Mealie sidecar creates it (trusted extension in PG 17, no superuser needed) |
| **PG 15+ schema grant** | CrowdSec | Init script: `GRANT CREATE ON SCHEMA public TO crowdsec` |
| **VACUUM privileges** | Prowlarr | Sidecar grants `pg_maintain` (provisioner has it with admin option) |
| **No `_FILE` support** | Radarr, Sonarr, Prowlarr | Passwords via compose interpolation (SOPS env vars) |
| **Cross-stack networking** | All 7 | New `infra_postgres` overlay network |

## Totals

- **11 databases** across 8 services (5 services need 1 DB each, 3 servarr apps need 2 each)
- **8 roles** (one per service, database-owner privileges recommended for servarr apps)

## Postgres Stack Design

### Image

**`postgres:17`** — intersects all services' supported/tested ranges. Mealie's official example pins to 17, Sonarr CI tests 16/17/18, CrowdSec CI tests 16. All drivers (pgx/v4, pgx/v5, sqlx-postgres, psycopg2, Npgsql 9.x/10.x) support PG 17. Upstream support through November 2029. Provides `pg_maintain` predefined role (PG 16+) as least-privilege alternative to superuser for Prowlarr's VACUUM.

No extensions required beyond `pg_trgm` (for Mealie). `pg_trgm` is a trusted extension in PG 17 — installable by any user with `CREATE` privilege on the database. PG 15+ revokes default `CREATE` on public schema, so CrowdSec's role needs an explicit `GRANT CREATE ON SCHEMA public`.

### Bootstrapping Strategy

**Unified client-side sidecars for all consumers (infra and apps).**

Every stack that needs postgres owns an `init-db` sidecar service that bootstraps its own database resources on deploy. The postgres init script is minimal — it only creates the superuser and a provisioner role. No app-specific roles, databases, or passwords exist in the postgres stack.

Sidecar naming convention: `init-<target>` (e.g., `init-db` for postgres, `init-ldap` for LLDAP bootstrapping). The `init-` prefix groups sidecars in service listings; the target suffix describes what they initialize.

This eliminates password duplication: each password lives in exactly one `secrets.env` (the consuming stack's). Previously, infra stacks used server-side bootstrapping in the postgres init script, which required duplicating passwords across `postgres/secrets.env` and each consumer's `secrets.env`. This duplication caused maintenance friction — password rotation or regeneration required coordinated updates across multiple files.

#### Postgres Init Script (Server-Side)

Runs once on empty data directory via Docker Config at `/docker-entrypoint-initdb.d/init.sh`.

**Creates only:**

- **Superuser** (`postgres`) — password from versioned Docker secret
- **Provisioner role** — `CREATEDB`, `CREATEROLE`, `LOGIN`, `pg_maintain WITH ADMIN OPTION`. Username from `GLOBAL_POSTGRES_PROVISIONER_USER`, password from `GLOBAL_POSTGRES_PROVISIONER_PASSWORD` (both in `GLOBAL_SECRETS`)

Nothing else. No app-specific roles, databases, or grants.

#### Client-Side Sidecars (All Consumers)

Each consuming stack (infra or apps) includes a lightweight sidecar service that:

1. Authenticates as the provisioner role (credentials from `GLOBAL_SECRETS` via compose interpolation)
2. Idempotently creates its own role with password (from stack-level `secrets.env`)
3. Idempotently creates its own database(s) with correct ownership
4. Applies any required extensions or grants
5. Runs `exec sleep infinity` for Swarm convergence (1/1 replicas)

The sidecar init script is delivered as a Docker Config. Uses `psql` from a lightweight postgres client image (e.g., `postgres:17-alpine`).

**Each consuming stack owns:**

- Its sidecar service definition in compose
- Its sidecar init script as a Docker Config
- Its own role password in stack-level `secrets.env` (single source of truth — no duplication)
- Knowledge of its own database names, extensions, and privilege needs

**Why unified client-side:**

- Zero password duplication — each password lives in exactly one file
- Self-contained stacks — adding a new consumer requires zero changes to the postgres stack
- Consistent pattern — infra and apps work identically
- Sidecars run every deploy (idempotent) — works on existing volumes, unlike init script which only runs on empty data dir
- Follows the existing LLDAP `init-ldap` sidecar pattern already proven in the codebase
- Adding/removing a postgres consumer is a single-stack change

#### Provisioner Role

A dedicated role that all consumer sidecars authenticate as. Not superuser — scoped to database and role administration only.

**Privileges:** `CREATEDB`, `CREATEROLE`, `LOGIN`, `pg_maintain WITH ADMIN OPTION`, `pg_read_all_data WITH ADMIN OPTION`. Can create databases and roles, set passwords, grant ownership. The `pg_read_all_data` admin grant enables delegating read-all access to the backup role for borgmatic pg_dump backups. Cannot read app data, modify schemas, or bypass row-level security. Each created database is owned by the app's role, not the provisioner — the provisioner loses access after handoff.

**Credential delivery:** Username and password stored in `GLOBAL_SECRETS` (SOPS-encrypted shared secrets). Auto-injected as env vars to all stacks via mise base config `_.file`. Sidecars access them through compose interpolation like any other global secret. Neither the username nor the password is hardcoded in any script — both come from env vars.

**`pg_maintain` grant:** The provisioner has `CREATEROLE` but granting `pg_maintain` to another role requires the provisioner itself to be a member of `pg_maintain`. The postgres init script grants `pg_maintain TO provisioner WITH ADMIN OPTION` so the provisioner can delegate it to Prowlarr's sidecar.

#### PG 17 Sidecar Quirks

- **Default database:** `psql` connects to a database named after `PGUSER` by default. Sidecars must set `PGDATABASE=postgres` explicitly — there is no database named after the provisioner role.
- **`CREATE DATABASE ... OWNER`:** Creating a role does not grant implicit `SET ROLE` on it. Sidecars must `GRANT <role> TO <provisioner>` after creating each role, before `CREATE DATABASE ... OWNER <role>`.
- **Identifier quoting:** The postgres init script uses `%I` (identifier format) in `format()` for the provisioner role name since it comes from an env var. Sidecar scripts use `%L` (literal format) for passwords only — role names like `authelia`/`lldap` are inline constants.

#### Deployment Order

Postgres must converge before any consumer stack. Position in `site:deploy-infra`:

1. `infra/socket`
2. **`infra/postgres`** — must converge and pass healthcheck before proceeding
3. `infra/gateway-internal`
4. `infra/gateway-external` (CrowdSec sidecar connects to postgres)
5. `infra/metrics` (Grafana sidecar connects to postgres)
6. `infra/registry`
7. `infra/accounts` (Authelia + LLDAP sidecars connect to postgres)

App stacks (`site:deploy-apps`) run after infra. Each app's sidecar handles its own postgres bootstrapping on deploy.

#### Re-Initialization

- **New consumer stack:** Sidecar handles everything — no postgres changes needed
- **Password rotation:** Sidecar re-runs `ALTER ROLE ... PASSWORD` on each deploy (idempotent)
- **Volume recreation:** Init script recreates superuser + provisioner; sidecars recreate all app roles/DBs on next deploy

### Networking

New overlay network `infra_postgres` for cross-stack database access. All 7 client services join this network. Postgres service on the storage node uses `endpoint_mode: dnsrr` (required for LXC — IPVS is broken in unprivileged containers).

## Stateful Services With NO Postgres Support

| Service | Stack | Reason |
|---------|-------|--------|
| Jellyfin | apps/jellyfin | Embedded SQLite only |
| Syncthing | apps/syncthing | Embedded SQLite only |
| Pinchflat | apps/pinchflat | Embedded SQLite only |
| Portainer | apps/portainer | Embedded BoltDB only |
| FileBrowser | apps/quantum | Embedded BoltDB only |
| Stirling PDF | apps/stirling | Postgres requires enterprise license |
| Immich | apps/immich | Requires custom Postgres image with VectorChord + pgvector |
