# Borgmatic Backup Orchestrator

## Architecture

### Services

| Service | Image | Placement | Purpose |
|---------|-------|-----------|---------|
| `borgmatic` | `ghcr.io/borgmatic-collective/borgmatic:2` | `*place-storage` | Scheduled pg_dump + borg deduplication + encryption |
| `init-db` | `postgres:17-alpine` | `*place-vm` | Creates `backup` role via provisioner (sidecar pattern) |

### Volumes

| Volume | Path | Purpose |
|--------|------|---------|
| `borgmatic-repo` | `/mnt/borg-repository` | Local borg repository (deduplicated, encrypted) |
| `borgmatic-state` | `/root/.local/state/borgmatic` | Borgmatic runtime state |
| `borg-config` | `/root/.config/borg` | Borg keys and security data |
| `borg-cache` | `/root/.cache/borg` | Borg chunk index cache (critical for dedup performance) |

### Credentials

**Dedicated backup role** — read-only, scoped to borgmatic only:

| Role | Privileges | Used by |
|------|-----------|---------|
| `provisioner` | `CREATEDB CREATEROLE pg_maintain pg_read_all_data(admin)` | All init-db sidecars |
| `backup` | `pg_read_all_data LOGIN` | Borgmatic (dumps only) |

The provisioner has `pg_read_all_data WITH ADMIN OPTION` to delegate read access to the backup role. The backup role is created by the init-db sidecar using the provisioner credentials from `GLOBAL_SECRETS`. Backup-specific credentials (`BACKUP_DB_USER`, `BACKUP_DB_PASSWORD`, `BORG_PASSPHRASE`) live in the stack's `secrets.env`.

### Backup behavior

`name: all` auto-discovers all non-template databases and dumps each individually via `pg_dump` in custom format. Dumps stream directly to borg via named pipe — no intermediate disk usage. pg_dump compression is disabled (`compression: none`) — borg handles compression with `zstd,3`. Uncompressed dumps deduplicate significantly better across daily archives.

Credentials (`PGUSER`, `PGPASSWORD`) come from the container environment (compose `environment:` block) via libpq env vars. The config file stays credential-free.

### Schedule and retention

Backup scheduling is configured in `config/borgmatic/crontab.txt`.
Retention is set in `config/borgmatic/config.yaml`.

### Borg passphrase delivery

The `FILE__BORG_PASSPHRASE` env var points to the Docker secret mount at `/run/secrets/borg_passphrase`. The image's s6-overlay `init-envfile` service reads the file and exports `BORG_PASSPHRASE` into the s6 container environment, making it available to cron-triggered borgmatic runs.

**`docker exec` caveat:** The `FILE__` mechanism only works for s6-managed services. Manual `docker exec` commands must load the passphrase explicitly:

```sh
docker exec <borgmatic> /bin/sh -c \
  'export BORG_PASSPHRASE=$(cat /run/s6/container_environment/BORG_PASSPHRASE) && borgmatic <command>'
```

**All examples below use `borgmatic-exec` as shorthand for this pattern.**

### Repository initialization

The init script (`config/borgmatic/init.sh`) wraps the stock entrypoint: waits for postgres, reads `BORG_PASSPHRASE` from the secret file (before s6-overlay resolves `FILE__` vars), runs `borgmatic repo-create --encryption repokey-blake2` (idempotent — skips if already initialized), then execs `/init` (s6-overlay + crond).

### Deploy ordering

`site:deploy-infra` deploys backup immediately after postgres. `site:drain` removes backup before postgres (reverse order).

## Restore Procedures

### Restore credentials

The backup role is read-only (`pg_read_all_data`) — sufficient for dumps but not for restores. `pg_restore --clean` issues DDL (`DROP TABLE`, `CREATE TABLE`, `ALTER TABLE`) which requires object ownership or superuser. Restores use the postgres superuser via borgmatic's `--username`/`--password` CLI flags — no superuser credentials are stored in the backup stack.

Get the postgres superuser password from the postgres stack's secrets.env

### Single database restore

```sh
borgmatic-exec restore --archive latest \
  --data-source <dbname> \
  --original-port 5432 \
  --username postgres --password <postgres-superuser-password>
```

`--original-port 5432` is required due to a [borgmatic bug](#borgmatic-port-matching-bug).

### Restore all databases

```sh
borgmatic-exec restore --archive latest \
  --username postgres --password <postgres-superuser-password>
```

Without `--data-source`, borgmatic restores every dump in the archive. No `--original-port` needed for this path.

### Restore from specific archive

```sh
borgmatic-exec repo-list
borgmatic-exec restore --archive <archive-name> \
  --username postgres --password <postgres-superuser-password>
```

### Full cluster restore (volume lost)

1. Redeploy postgres — fresh volume, `init.sh` creates provisioner with `pg_read_all_data` admin
2. Deploy all stacks (`site:deploy-infra` + `site:deploy-apps`) — init-db sidecars create roles and empty databases. Applications auto-initialize their schemas on first startup (authelia, lldap, grafana, crowdsec, immich all run migrations against empty databases)
3. Restore all databases — `pg_restore --clean` drops the auto-initialized schemas and replaces them with backup data:

```sh
borgmatic-exec restore --archive latest \
  --username postgres --password <postgres-superuser-password>
```

1. Force-update services that exhausted restart attempts during step 2:
   `docker service update --force <service>`

**Databases must exist before restore.** Individual `pg_dump` dumps don't include `CREATE DATABASE` statements. The consumer init-db sidecars (deployed in step 2) create the empty databases that borgmatic restores into.

### List and inspect backups

```sh
borgmatic-exec repo-list        # List all archives
borgmatic-exec repo-info        # Repository size, encryption info
borgmatic-exec check            # Verify backup integrity
```

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
- **Volume backup service** — for non-Postgres Docker named volumes.
