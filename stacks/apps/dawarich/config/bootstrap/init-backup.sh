#!/bin/sh
set -e

PGHOST="database"
PGPORT="5432"
PGUSER="postgres"
PGDATABASE="postgres"
PGPASSWORD="$(cat /run/secrets/dawarich_db_password)"
BACKUP_PASSWORD="$(cat /run/secrets/dawarich_backup_db_password)"
export PGHOST PGPORT PGUSER PGDATABASE PGPASSWORD

echo "Waiting for PostgreSQL..."
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -q; do
  sleep 2
done

psql -v ON_ERROR_STOP=1 <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'backup') THEN
            EXECUTE format('CREATE ROLE backup LOGIN PASSWORD %L', '${BACKUP_PASSWORD}');
        ELSE
            EXECUTE format('ALTER ROLE backup PASSWORD %L', '${BACKUP_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT pg_read_all_data TO backup;
EOSQL

echo "Backup role provisioning complete."
