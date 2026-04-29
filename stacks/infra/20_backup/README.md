# Borgmatic Backup Orchestrator

## Architecture

### Services

| Service | Image | Placement | Purpose |
|---------|-------|-----------|---------|
| `borgmatic` | `ghcr.io/borgmatic-collective/borgmatic:2` | `*place-vm` | Scheduled pg_dump / mariadb-dump + borg deduplication + encryption |
| `init-backup` | `postgres:17-alpine` | `*place-vm` | Creates the `backup` role on the central Postgres (prepares the DB for borgmatic dumps; does not initialize databases) |

### Backup targets

Borgmatic dumps from four database instances, each reached via the `infra_backup` overlay. Per-instance `init-backup` sidecars (in their owning stacks) create a dedicated `backup` role with read-only privileges. These are distinct from the cluster's `init-db` sidecars (mealie, authentik, etc.) which actually provision databases and application roles — `init-backup` only preps an existing database for borgmatic access.

| Target | Engine | Hostname (Swarm DNS) | init-backup sidecar lives in |
|--------|--------|----------------------|------------------------------|
| Central | Postgres 17 | `postgres` (via `infra_postgres`) | `infra/20_backup` (this stack) |
| Immich | Postgres 14 + vchord + pgvector | `immich_database` | `apps/immich` |
| Dawarich | Postgres 17 + PostGIS 3.5 | `dawarich_database` | `apps/dawarich` |

The `backup` role on each instance is locally scoped — it doesn't see other instances' data, only its own.

### Volumes

| Volume | Path | Purpose |
|--------|------|---------|
| `borgmatic-repo` | `/mnt/borg-repository` | Local borg repository (deduplicated, encrypted) |
| `borgmatic-state` | `/root/.local/state/borgmatic` | Borgmatic runtime state |
| `borg-config` | `/root/.config/borg` | Borg keys and security data |
| `borg-cache` | `/root/.cache/borg` | Borg chunk index cache (critical for dedup performance) |

### Credentials

**Dedicated backup role per instance** — read-only, scoped to borgmatic only:

| Role / User | Privileges | Used by |
|-------------|------------|---------|
| `provisioner` (central) | `CREATEDB CREATEROLE pg_maintain pg_read_all_data(admin)` | All init-db and init-backup sidecars targeting central Postgres |
| `backup` (Postgres targets) | `pg_read_all_data LOGIN` | Borgmatic dumps |
| `backup` (MariaDB target) | `SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER, RELOAD, PROCESS, REPLICATION CLIENT ON *.*` | Borgmatic mariadb-dump |

For the central Postgres, the provisioner has `pg_read_all_data WITH ADMIN OPTION` to delegate read access. For the standalone Postgres instances (immich, dawarich) and the MariaDB instance, the local DB superuser creates the `backup` role directly — there is no provisioner role on those instances.

**Required `secrets.env` entries** (user manages):

| File | Variable | Purpose |
|------|----------|---------|
| `infra/20_backup/secrets.env` | `BORG_PASSPHRASE` | Borg repo encryption passphrase |
| `infra/20_backup/secrets.env` | `POSTGRES_BACKUP_DB_PASSWORD` | Borgmatic auth as `backup` against central Postgres; init-backup sidecar in this stack provisions the matching role |
| `infra/20_backup/secrets.env` | `IMMICH_BACKUP_DB_PASSWORD` | Borgmatic auth as `backup` against immich DB |
| `infra/20_backup/secrets.env` | `DAWARICH_BACKUP_DB_PASSWORD` | Borgmatic auth as `backup` against dawarich DB |
| `apps/immich/secrets.env` | `IMMICH_BACKUP_DB_PASSWORD` | init-backup sidecar provisions matching role |
| `apps/dawarich/secrets.env` | `DAWARICH_BACKUP_DB_PASSWORD` | init-backup sidecar provisions matching role |

For the per-target passwords (immich, dawarich,), the same value must appear in both the backup stack's secrets and the target stack's secrets. Alternatively, promote them to `GLOBAL_SECRETS` to keep one source of truth.

### Backup behavior

`name: all` per target auto-discovers non-template databases and dumps each individually — pg_dump for Postgres targets in `--format=custom`, mariadb-dump for MariaDB with `--single-transaction --skip-lock-tables` (online InnoDB-safe). Dumps stream directly to borg via named pipe — no intermediate disk usage. pg_dump compression is disabled (`compression: none`); borg handles compression with `zstd,3`. Uncompressed dumps deduplicate significantly better across daily archives. `no_owner: true` is set on every Postgres target so dumps restore portably without requiring the original owner role to exist on the target instance.

The central Postgres entry uses libpq env vars (`PGUSER`, `PGPASSWORD`) from compose, since the central instance pre-dates this stack's broader credential refactor. Per-instance backup passwords (immich, dawarich) are delivered as Docker secrets and referenced in `config.yaml` via borgmatic's native `{credential container <name>}` syntax — no env-var leakage in the borgmatic container's environment, and password values stay file-resident at `/run/secrets/`.

Targets are dumped serially. A failure on one target (e.g., a stack that hasn't deployed yet) doesn't abort the run — borgmatic logs the failure and continues to the next target. The borg archive still gets created with whatever dumps succeeded. `retries: 3` with `retry_wait: 30` smooths over transient cross-overlay network blips.

### Integrity checks

Borgmatic runs scheduled consistency checks on the borg repo (independent from the daily backup run, gated by per-check frequency):

| Check | Frequency | Purpose |
|-------|-----------|---------|
| `repository` | Weekly | Segment metadata + index consistency (cheap) |
| `archives` | Bi-weekly | Walks each archive's chunks (medium cost) |
| `extract` | Monthly (Sundays only) | Full extraction dry-run of latest archive |

`data` (full SHA-256 verify) is intentionally omitted as too expensive at our cadence — the `repository + archives` combination catches structural and reference corruption without the I/O cost.

### Output and observability

`statistics: true` adds per-archive size and dedup figures to every run's stdout. Container stdout is captured by Alloy and shipped to Loki — the existing log pipeline. No native borgmatic Loki push hook is configured; Alloy capture covers the same ground without adding a runtime dependency on Loki being reachable from borgmatic at backup time.

### Schedule and retention

Backup scheduling is configured in `config/borgmatic/crontab.txt`.
Retention is set in `config/borgmatic/config.yaml`.

### Credential delivery

Every credential the borgmatic container needs is mounted as a Docker secret at `/run/secrets/<name>`. Nothing sensitive lives in the container's environment — `printenv` shows only `TZ` and Docker-injected metadata. The flow:

| Credential | Secret file | Consumed by |
|------------|-------------|-------------|
| Borg encryption passphrase | `/run/secrets/borg_passphrase` | `encryption_passphrase: "{credential container borg_passphrase}"` in `config.yaml`; borgmatic injects `BORG_PASSPHRASE` per-subprocess when invoking borg |
| Central Postgres `backup` role | `/run/secrets/postgres_backup_db_password` | `password: "{credential container postgres_backup_db_password}"` on the central entry; same file is read by the init-backup sidecar's role-creation script |
| Immich Postgres `backup` role | `/run/secrets/immich_backup_db_password` | Same pattern, immich entry |
| Dawarich Postgres `backup` role | `/run/secrets/dawarich_backup_db_password` | Same pattern, dawarich entry |

### Repository initialization

The init script (`config/borgmatic/init.sh`) wraps the stock entrypoint: waits for the central postgres to be reachable, runs `borgmatic repo-create --encryption repokey-blake2` (idempotent — skips if already initialized), then execs `/init` (s6-overlay + crond). The passphrase is resolved from the secret file by borgmatic's credential loader; the wrapper itself doesn't touch it.

### Deploy ordering

`site:deploy-infra` deploys backup immediately after postgres. `site:drain` removes backup before postgres (reverse order).

## Restore Procedures

### Restore credentials

The `backup` role is read-only — sufficient for dumps but not for restores. `pg_restore --clean` (and `mariadb-dump` restores) issue DDL which requires object ownership or superuser. Restores use the local superuser of the target instance via borgmatic's `--username`/`--password` CLI flags — no superuser credentials are stored in the backup stack.

Get each instance's superuser password from its owning stack's `secrets.env`:

| Target | Superuser | Password source |
|--------|-----------|-----------------|
| Central Postgres | `postgres` | `infra/10_postgres/secrets.env` |
| Immich Postgres | `postgres` | `apps/immich/secrets.env` (DB_PASSWORD) |
| Dawarich Postgres | `postgres` | `apps/dawarich/secrets.env` (DAWARICH_DB_PASSWORD) |

### Per-host restore syntax

Borgmatic identifies dumps by the `hostname` in the archive — the same value that was set in `config.yaml` at backup time. To restore from a specific target, pass `--hostname`. Manual operations against the container use plain `docker exec` — no env-var workaround needed because borgmatic resolves the passphrase from the secret file directly:

```sh
# Restore everything in the archive (all four targets, all databases):
docker exec <borgmatic> borgmatic restore --archive latest

# Restore only the central Postgres (omitting --hostname defaults to first match):
docker exec <borgmatic> borgmatic restore --archive latest --hostname postgres \
  --username postgres --password <central-superuser-password>

# Restore only the Immich database:
docker exec <borgmatic> borgmatic restore --archive latest --hostname immich_database \
  --username postgres --password <immich-superuser-password>

# Restore a single database from a specific target:
docker exec <borgmatic> borgmatic restore --archive latest \
  --hostname dawarich_database \
  --data-source dawarich_development \
  --original-port 5432 \
  --username postgres --password <dawarich-superuser-password>

```

`--original-port 5432` is required for single-database Postgres restores due to a [borgmatic bug](#borgmatic-port-matching-bug). Not needed when restoring all databases or for MariaDB.

### Restore prerequisites for vchord and PostGIS

Postgres restores into a target instance fail if required extensions aren't present.

**Immich (vchord, vector, etc.)** — must restore into a postgres image that ships VectorChord and pgvector at the same versions the source had at dump time. The official `ghcr.io/immich-app/postgres:14-vectorchord*-pgvectors*` image satisfies this. The image must also have `vchord.so` in `shared_preload_libraries` (the Immich image sets this by default). Restoring an Immich dump into vanilla `postgres:14` will fail at the `CREATE EXTENSION vchord` step.

**Dawarich (PostGIS)** — must restore into a `postgis/postgis:17-3.5*` image (or any image with PostGIS 3.5 installed at the OS level). pg_restore will run `CREATE EXTENSION postgis` itself; if PostGIS isn't installed in the image, restore aborts.

**Cross-major-version restore** — pg_dump 17 (bundled in the borgmatic image) can dump from PG 14 (Immich) and PG 17, and the resulting custom-format dump can be restored into either major. Future Immich PG14 → PG17 migrations can use the same dumps.

### List and inspect backups

```sh
docker exec <borgmatic> borgmatic repo-list   # List all archives
docker exec <borgmatic> borgmatic repo-info   # Repository size, encryption info
docker exec <borgmatic> borgmatic check       # Verify backup integrity
```

### Full cluster restore (volume lost)

1. Redeploy postgres — fresh volume, `init.sh` creates provisioner with `pg_read_all_data` admin.
2. Deploy all stacks (`site:deploy-infra` + `site:deploy-apps`) — init-db sidecars create application roles + empty databases on central Postgres; init-backup sidecars create the per-instance `backup` roles on every target. Applications auto-initialize their schemas on first startup against the empty databases.
3. Restore each target as needed via the per-host syntax above. `pg_restore --clean` drops the auto-initialized schemas and replaces them with backup data; `mariadb-dump` restores append/overwrite.
4. Force-update any services that exhausted restart attempts during step 2: `docker service update --force <service>`.

**Databases must exist before restore.** Individual `pg_dump` dumps don't include `CREATE DATABASE` statements. The init-db sidecars (deployed in step 2) create the empty databases that borgmatic restores into.

## Known Limitations

### Borg 1.x only

The `:2` tag is borgmatic 2.x, not Borg 2.x. The image pins Borg 1.4.x via pip. Borg 2.x is [not yet supported](https://github.com/borgmatic-collective/docker-borgmatic/issues/132) by the image maintainers.

**Affects:** Encryption uses `repokey-blake2`. Native S3/B2 repository support requires Borg 2.x; offsite backups currently need rclone or SSH/SFTP targets.

### Borgmatic port matching bug

Borgmatic 2.1.3 seems to have a bug in `restore.py:get_dumps_to_restore()` — it calls `dumps_match()` without passing `default_port`. When the config specifies `port: 5432` explicitly, archive dumps are tagged with `port: 5432`. The CLI request has `port: None` (no flag). Without the default port hint, `None != 5432` and the match fails with "missing from archive".

**Affects:** Single-database restores via `--data-source <name>`. Does NOT affect restoring all databases (that path bypasses matching entirely).

**Workaround:** Pass `--original-port 5432` on single-database restore commands.

### Provisioner grant on existing volumes

The `pg_read_all_data WITH ADMIN OPTION` grant in `postgres/init.sh` only runs on fresh data directories (`docker-entrypoint-initdb.d`). Existing deployments need a one-time manual grant:

```sql
GRANT pg_read_all_data TO <provisioner> WITH ADMIN OPTION;
```

## Future Expansion

- **Offsite borg repository** — borgmatic supports multiple repositories natively. Add a second entry in `config.yaml` for SSH/SFTP or NAS. S3/B2 requires rclone until the image adopts Borg 2.x.
- **Additional DB targets** — extend `postgresql_databases` / `mariadb_databases` in `config.yaml`, attach the new DB service to the `infra_backup` overlay, add an init-backup sidecar in its stack to provision the `backup` role.
- **Volume backup service** — for non-DB Docker named volumes (SQLite, BoltDB, application config).
