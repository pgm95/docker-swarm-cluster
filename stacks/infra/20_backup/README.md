# Borgmatic Backup Orchestrator

## Architecture

### Services

| Service | Image | Placement | Purpose |
|---------|-------|-----------|---------|
| `borgmatic` | `ghcr.io/borgmatic-collective/borgmatic:2` | `*place-vm` | Scheduled pg_dump / mariadb-dump + borg deduplication + encryption |
| `init-backup` | `postgres:17-alpine` | `*place-vm` | Creates the `backup` role on the central Postgres (prepares the DB for borgmatic dumps; does not initialize databases) |
| `borgmatic-exporter` | `busybox:latest` | `*place-vm` | Sidecar httpd serving Prometheus metrics emitted by the borgmatic post-action hook |

### Volumes

| Volume | Path | Purpose |
|--------|------|---------|
| `borgmatic-repo` | `/mnt/borg-repository` | Local borg repository |
| `borgmatic-state` | `/root/.local/state/borgmatic` | Borgmatic runtime state |
| `borg-config` | `/root/.config/borg` | Borg keys and security data |
| `borg-cache` | `/root/.cache/borg` | Borg chunk index cache (critical for dedup performance) |
| `borgmatic-metrics` | `/shared` | Prometheus exposition file written by the post-action hook; mounted read-only into the exporter sidecar |

### Backup targets

Borgmatic dumps from several databases, each reached via the `infra_backup` overlay.
Per-instance `init-backup` sidecars (in their owning stacks) create a dedicated `backup` role with read-only privileges and preps existing databases for borgmatic access.

| Role / User | Privileges | Used by |
|-------------|------------|---------|
| `provisioner` (central Postgres) | `CREATEDB CREATEROLE pg_maintain pg_read_all_data(admin)` | All init-db and init-backup sidecars targeting central Postgres |
| `backup` (external targets) | `pg_read_all_data LOGIN` | Borgmatic dumps |

- For the central Postgres, the provisioner has `pg_read_all_data WITH ADMIN OPTION` to delegate read access.
- For the standalone Postgres instances (immich, dawarich), there is no provisioner role: the local DB superuser creates the `backup` role directly.
  The same credentials must appear in both the backup stack's secrets and the target stack's secrets. Alternatively, promote them to global-scope secrets.

### Backup behavior

Each target is its own borgmatic config file in `/etc/borgmatic.d/`, sharing options via `<<: !include shared/common.yaml`. The cron entry invokes `borgmatic` without `--config`, so it iterates every config and processes them independently — a failure in one target's create action doesn't affect the others. Per-target retention is scoped via `match_archives: "sh:swarm-cluster-{target}-*"`.

`name: all` auto-discovers non-template databases per target and dumps each individually — pg_dump in `--format=custom`. Dumps stream directly to borg via named pipe — no intermediate disk usage. pg_dump compression is disabled (`compression: none`); borg handles compression with `zstd,3`. `no_owner: true` is set so dumps restore portably without requiring the original owner role on the target.

### Prometheus metrics

Borgmatic 2.x has no native Prometheus integration, so this stack ships a self-contained busybox httpd sidecar exporter.
The `commands:` hook fires `exporter.py` after every `create` action with `--target {target}` and `--config {configuration_filename}`, scoping its `borgmatic info`/`list` lookups to the firing config.
Separate entries handle the `finish` and `fail` states (hook context doesn't expose success/failure on `after: action`).
The script formats Prometheus exposition text and atomic-renames into a shared volume; counters persist across runs via prior-file read.

A Prometheus recording rule (`borgmatic:backup_last_success_age_seconds` in `40_metrics/config/prometheus/node_rules.yml`) materializes the age-since-last-success so the dashboard reads a stable gauge instead of computing `time() - timestamp` at panel-render time (which depends on Grafana's render scheduling and produces inconsistent values across time-range zooms).

Metrics emitted (per-target carry a `target` label; repo-wide carry only `repository_label`):

| Category | Examples |
|----------|----------|
| Run state (per-target) | `borgmatic_backup_last_run_timestamp_seconds`, `borgmatic_backup_last_success_timestamp_seconds`, `borgmatic_backup_success` |
| Counters (per-target) | `borgmatic_backup_runs_total`, `borgmatic_backup_successes_total`, `borgmatic_backup_failures_total` |
| Latest archive (per-target) | `borgmatic_last_archive_{original,compressed,deduplicated}_bytes`, `borgmatic_last_archive_files`, `borgmatic_last_archive_duration_seconds`, `borgmatic_archives_count` |
| Repository (repo-wide) | `borgmatic_repository_{original,compressed,deduplicated}_bytes`, `borgmatic_repository_{total,unique}_chunks` |

### Repository initialization

The init script (`config/borgmatic/init.sh`) wraps the stock entrypoint: waits for the central postgres to be reachable, runs `borgmatic repo-create --encryption repokey-blake2` (which iterates every config in `/etc/borgmatic.d/` against the shared repo path — first creates, rest skip as already initialized), then execs `/init` (s6-overlay + crond). The passphrase resolves from the secret file via borgmatic's credential loader; the wrapper itself doesn't touch it.

## Restore Procedures

The `backup` role is read-only and insufficient for restores. `pg_restore --clean` (and `mariadb-dump` restores) issue DDL which requires object ownership or superuser.
Restores use the local superuser of the target instance via borgmatic's `--username`/`--password` CLI flags.
No superuser credentials are stored in the backup stack.

### Per-host restore syntax

Borgmatic identifies dumps by the `hostname` set in the per-target config at backup time.
To restore from a specific target, pass `--hostname` (or `--config /etc/borgmatic.d/pg-<target>.yaml` to scope to one config).

```sh
# Restore everything in the archive (all targets, all databases):
docker exec <borgmatic> borgmatic restore --archive latest

# Restore only the central Postgres:
docker exec <borgmatic> borgmatic restore --archive latest --hostname postgres \
  --username postgres --password <central-superuser-password>

# Restore only the Immich database:
docker exec <borgmatic> borgmatic restore --archive latest --hostname immich_database \
  --username postgres --password <immich-superuser-password>

# Restore a single database from a specific target:
# `--original-port 5432` is required here
docker exec <borgmatic> borgmatic restore --archive latest \
  --hostname dawarich_database \
  --data-source dawarich_development \
  --original-port 5432 \
  --username postgres --password <dawarich-superuser-password>
```

**Databases must exist before restore.** Individual `pg_dump` dumps don't include `CREATE DATABASE` statements. The targets' init-db sidecars create the empty databases that borgmatic restores into.

## Known Limitations

### Borg 1.x only

The `:2` tag is borgmatic 2.x, not Borg 2.x. The image pins Borg 1.4.x via pip. Borg 2.x is [not yet supported](https://github.com/borgmatic-collective/docker-borgmatic/issues/132) by the image maintainers.

**Affects:** Encryption uses `repokey-blake2`. Native S3/B2 repository support requires Borg 2.x; offsite backups currently need rclone or SSH/SFTP targets.

### Provisioner grant on existing volumes

The `pg_read_all_data WITH ADMIN OPTION` grant in `postgres/init.sh` only runs on fresh data directories (`docker-entrypoint-initdb.d`). Existing deployments need a one-time manual grant:

```sql
GRANT pg_read_all_data TO <provisioner> WITH ADMIN OPTION;
```

## Future Expansion

- **Offsite borg repository** — borgmatic supports multiple repositories natively. Add a second entry in `shared/common.yaml` for SSH/SFTP or NAS. S3/B2 requires rclone until the image adopts Borg 2.x.
- **Volume backup service** — for non-DB Docker named volumes (SQLite, BoltDB, file state).
