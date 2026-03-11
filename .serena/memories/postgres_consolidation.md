# Postgres Consolidation

Service compatibility research for central Postgres.

## Services With Postgres Support

### Authelia (infra/accounts) — DEPLOYED

- Config: `storage.postgres` in `configuration.yml` — fields: `address` (`tcp://host:port`), `database`, `username`, `password`
- `_FILE` env vars: `AUTHELIA_STORAGE_POSTGRES_PASSWORD_FILE`, `AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE`
- Alternative: Go template `{{ secret "/run/secrets/..." }}` in config (requires `X_AUTHELIA_CONFIG_FILTERS=template`)
- Auto-migrates schema on startup
- `encryption_key` encrypts TOTP/WebAuthn data inside DB — unrelated to Postgres connection
- 1 database

### LLDAP (infra/accounts) — DEPLOYED

- Config: `LLDAP_DATABASE_URL=postgres://user:password@host/dbname` env var or `database_url` in toml
- Auto-creates tables on startup
- `/data` volume removable with Postgres — key derived from `LLDAP_KEY_SEED` (deterministic), no LDAPS, config via Docker Config
- 1 database

### CrowdSec (infra/gateway-external) — DEPLOYED

- Config: `db_config` block in `config.yaml` with `type: pgx` (canonical; `postgresql`/`postgres` emit deprecation warnings)
- Config parser supports native `${VAR}` substitution. No `_FILE` or Go template support
- Agent creds re-registered with `--force` every boot. Bouncers use idempotent name-check — survive restarts with persistent Postgres
- Requires `GRANT CREATE ON SCHEMA public` (PG 15+ revocation)
- `crowdsec-db` volume removable. Needs `CROWDSEC_BYPASS_DB_VOLUME_CHECK=true`
- `crowdsec-app` volume kept (~20MB: hub index, CAPI creds, installed parsers/scenarios, LAPI credentials)
- 1 database

### Grafana (infra/metrics) — DEPLOYED

- Config via `grafana.ini` database section
- Auto-migrates on startup
- 1 database

### Mealie (apps/mealie) — NOT YET MIGRATED

- Config: `DB_ENGINE=postgres`, `POSTGRES_SERVER`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
- Native `_FILE` support since v2.7.0 (upstream `entry.sh`)
- Auto-creates schema via Alembic, including `pg_trgm` extension (trusted in PG 17, no superuser needed)
- `SQLITE_MIGRATE_JOURNAL_WAL` silently ignored on Postgres — safe to leave
- 1 database

### Radarr, Sonarr, Prowlarr (apps/servarr) — NOT YET MIGRATED

- Config: `{APP}__POSTGRES__{FIELD}` env vars (double underscore). Fields: `HOST`, `PORT`, `USER`, `PASSWORD`, `MAINDB`, `LOGDB`
- Does NOT auto-create databases — init script must pre-create all 6
- No `_FILE` support — passwords via compose interpolation
- API keys via `{APP}__AUTH__APIKEY` env vars — `config.xml` fungible, no named volumes needed
- Prowlarr needs `pg_maintain` for VACUUM (Prowlarr wiki explicitly states superuser required; `pg_maintain` is least-privilege alternative)
- 2 databases per app (main + log), 6 total

## Statelessness After Migration

| Service | Stateless? | Notes |
|---------|:---:|-------|
| LLDAP | Yes | DB gone, key from seed, config via Docker Config |
| Authelia | Yes | /config only held SQLite + notification state (non-critical) |
| CrowdSec | Partial | Drop crowdsec-db, keep crowdsec-app |
| Radarr/Sonarr/Prowlarr | Yes | config.xml fungible via env vars |
| Mealie | No | User-uploaded recipe media in /app/data |

## Services Without Postgres Support

| Service | Reason |
|---------|--------|
| Jellyfin | Embedded SQLite only |
| Syncthing | Embedded SQLite only |
| Pinchflat | Embedded SQLite only |
| Portainer | Embedded BoltDB only |
| FileBrowser (quantum) | Embedded BoltDB only |
| Stirling PDF | Postgres requires enterprise license |
| Immich | Requires custom Postgres with VectorChord + pgvector — cannot use central instance |

## Cross-Cutting Concerns

| Concern | Affected | Resolution |
|---------|----------|------------|
| DB/role creation | All | Client-side init-db sidecars via provisioner role |
| `pg_trgm` extension | Mealie | Sidecar creates it (trusted in PG 17) |
| PG 15+ schema grant | CrowdSec | `GRANT CREATE ON SCHEMA public` |
| VACUUM privileges | Prowlarr | Sidecar grants `pg_maintain` (provisioner has it with admin option) |
| No `_FILE` support | Servarr apps | Passwords via compose interpolation |

## Totals

- 11 databases across 8 services (5x1 + 3x2)
- 8 roles (one per service, database-owner privileges)
