# Borgmatic Backup Orchestrator

## Architecture

### Services

| Service | Image | Placement | Purpose |
|---------|-------|-----------|---------|
| `borgmatic` | `ghcr.io/borgmatic-collective/borgmatic:2` | `*place-main` | Scheduled pg_dump / mariadb-dump + borg deduplication + encryption |
| `init-backup` | `postgres:17-alpine` | `*place-main` | Creates the `backup` role on the central Postgres (prepares the DB for borgmatic dumps; does not initialize databases) |
| `borgmatic-exporter` | `busybox:latest` | `*place-main` | Sidecar httpd serving Prometheus metrics emitted by the borgmatic post-action hook |

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
  Each backup role password is a pairing defined once in the `backup` topic file; the owning stack's `init-backup` sidecar and borgmatic both mount that one key, and borgmatic resolves it through its `credential container` syntax against the mounted secret name.

### Backup behavior

Each target is its own borgmatic config file in `/etc/borgmatic.d/`, sharing options via `<<: !include shared/common.yaml`. The cron entry invokes `borgmatic` without `--config`, so it iterates every config and processes them independently; a failure in one target's create action doesn't affect the others. Per-target retention is scoped via `match_archives: "sh:swarm-cluster-{target}-*"`.

`name: all` auto-discovers non-template databases per target and dumps each individually: pg_dump in `--format=custom`. Dumps stream directly to borg via named pipe with no intermediate disk usage. pg_dump compression is disabled (`compression: none`); borg handles compression with `zstd,3`. `no_owner: true` is set so dumps restore portably without requiring the original owner role on the target.

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

The init script (`config/borgmatic/init.sh`) wraps the stock entrypoint: waits for the central postgres to be reachable, runs `borgmatic repo-create --encryption repokey-blake2` (which iterates every config in `/etc/borgmatic.d/` against the shared repo path; the first creates, the rest skip as already initialized), then execs `/init` (s6-overlay + crond). The passphrase is a stack-local secret that borgmatic resolves through its `credential container` syntax against the mounted secret name; the wrapper itself doesn't touch it.

## Restore Procedures

The `backup` role is read-only and cannot restore: `pg_restore --clean` issues DDL, which needs object ownership or superuser. No restore credentials are stored in the backup stack; they are passed to borgmatic's `--username`/`--password` flags at restore time.

**Restore as the role the application connects as.** Dumps are taken with `no_owner: true`, so every restored object belongs to the role that runs the restore. Restore a central Postgres database as its owning role (the role its stack's init-db creates, which the application logs in as). Restoring it as the superuser leaves the tables owned by `postgres`, and the application can no longer read them. The standalone instances (Immich, Dawarich) connect as their own superuser, so their superuser is the right role there.

**The database must exist.** Individual `pg_dump` dumps contain no `CREATE DATABASE`. Create the empty database owned by the application role, the same statement the stack's init-db runs.

### Runbook: restore one database

1. Stop the consumer so nothing writes to or reconnects to the database: `swarm:remove <stack>`.
2. Drop the damaged database if it still exists: `DROP DATABASE <db> WITH (FORCE);`
3. Create it empty, owned by the application role: `CREATE DATABASE <db> OWNER <role>;`
4. Restore it as that role. Pass the password on stdin so it is kept out of shell history and the docker exec command:

   ```sh
   printf '%s\n' "$ROLE_PASSWORD" | docker exec -i <borgmatic> sh -c '
     read -r P
     borgmatic restore --config /etc/borgmatic.d/pg-<target>.yaml --archive latest \
       --hostname <target-host> --data-source <db> --original-port 5432 \
       --username <role> --password "$P"'
   ```

   `--archive latest` with `--config` resolves to the newest archive of that target; list them with `borgmatic --config /etc/borgmatic.d/pg-<target>.yaml repo-list` to pick an older one. Warnings of the form `permission denied to analyze "pg_..."` come from borgmatic's post-restore `ANALYZE` of shared catalogs and are expected for a non-superuser.
5. Check ownership and content: `SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public';` returns only the application role, and a known row is present.
6. Redeploy the consumer: `swarm:deploy <stack>`. Its init-db leaves the existing database alone.

Consumer specifics:

- **lldap** only starts if `ACCOUNTS_LLDAP_KEY_SEED` is the seed that produced the dump's password hashes. Authentik keeps matching users and groups by the restored `entryUUID`; if Authentik's database is lost as well, rebuild it with the purge runbook in the accounts stack README.

### Restoring several databases or a whole target

Borgmatic identifies dumps by the `hostname` set in the per-target config at backup time. Pass `--hostname` or `--config /etc/borgmatic.d/pg-<target>.yaml` to scope a restore; without `--data-source` it restores every database of that target, all as the one role given, so it only suits targets whose databases share an owner.

```sh
# Every database of the Immich instance (its application connects as the superuser):
docker exec -i <borgmatic> borgmatic restore --archive latest --hostname immich_database \
  --username postgres --password <immich-superuser-password>
```

### Running the scheduled backup by hand

Run exactly what cron runs, so a manual run behaves like the scheduled one:

```sh
docker exec <borgmatic> sh -c 'PATH=$PATH:/usr/local/bin /usr/local/bin/borgmatic --verbosity 1 2>&1'
```

It processes every target in turn. A target whose host cannot be reached is retried three times with 30, 60 and 90 second pauses, so an unreachable target stretches the run by several minutes. An archive is complete once borgmatic renames it from `<name>.checkpoint`; stopping the run after that point keeps it. borgmatic ignores `SIGTERM` from its hooks and `pkill -f` does not match its interpreter-wrapped processes, so stop a run by PID (`ps -eo pid,args` inside the container, then `kill -9`).

## Known Limitations

### Borg 1.x only

The `:2` tag is borgmatic 2.x, not Borg 2.x. The image pins Borg 1.4.x via pip. Borg 2.x is [not yet supported](https://github.com/borgmatic-collective/docker-borgmatic/issues/132) by the image maintainers.

**Affects:** Encryption uses `repokey-blake2`. Native S3/B2 repository support requires Borg 2.x; offsite backups currently need rclone or SSH/SFTP targets.

### Provisioner grant on existing volumes

The `pg_read_all_data WITH ADMIN OPTION` grant in `postgres/init.sh` only runs on fresh data directories (`docker-entrypoint-initdb.d`). Existing deployments need a one-time manual grant:

```sql
GRANT pg_read_all_data TO <provisioner> WITH ADMIN OPTION;
```

### Exporter lookups wait on the repository lock

After a failed create action, the exporter hook's `borgmatic info` waits on the repository lock that the still running parent job holds, until its 120 second timeout. Every failed target therefore adds about two minutes to the run, and its metrics for that run are incomplete.

## Future Expansion

- **Offsite borg repository** borgmatic supports multiple repositories natively. Add a second entry in `shared/common.yaml` for SSH/SFTP or NAS. S3/B2 requires rclone until the image adopts Borg 2.x.
- **Volume backup service** for non-DB Docker named volumes (SQLite, BoltDB, file state).
