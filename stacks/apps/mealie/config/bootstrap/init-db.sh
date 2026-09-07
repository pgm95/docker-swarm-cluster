#!/bin/sh
set -e

PGHOST="postgres"
PGPORT="5432"
PGUSER="${GLOBAL_DB_PROVISIONER_USER}"
PGDATABASE="postgres"
PGPASSWORD="$(cat /run/secrets/global_db_provisioner_password)"
MEALIE_DB_PASSWORD="$(cat /run/secrets/mealie_db_password)"
export PGHOST PGPORT PGUSER PGDATABASE PGPASSWORD

echo "Waiting for PostgreSQL..."
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -q; do
  sleep 2
done

psql -v ON_ERROR_STOP=1 <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mealie') THEN
            EXECUTE format('CREATE ROLE mealie LOGIN PASSWORD %L', '${MEALIE_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT mealie TO ${GLOBAL_DB_PROVISIONER_USER};
    SELECT 'CREATE DATABASE mealie OWNER mealie'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mealie')\gexec
EOSQL

echo "Database provisioning complete."
