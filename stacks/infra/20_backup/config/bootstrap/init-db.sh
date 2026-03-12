#!/bin/sh
set -e

PGHOST="postgres"
PGPORT="5432"
PGUSER="${PROVISIONER_USER}"
PGDATABASE="postgres"
PGPASSWORD="${PROVISIONER_PASSWORD}"
export PGHOST PGPORT PGUSER PGDATABASE PGPASSWORD

echo "Waiting for PostgreSQL..."
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -q; do
  sleep 2
done

psql -v ON_ERROR_STOP=1 <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${BACKUP_DB_USER}') THEN
            EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '${BACKUP_DB_USER}', '${BACKUP_DB_PASSWORD}');
        ELSE
            EXECUTE format('ALTER ROLE %I PASSWORD %L', '${BACKUP_DB_USER}', '${BACKUP_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT pg_read_all_data TO ${BACKUP_DB_USER};
EOSQL

echo "Backup role provisioning complete."
