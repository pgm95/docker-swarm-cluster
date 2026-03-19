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
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mealie') THEN
            EXECUTE format('CREATE ROLE mealie LOGIN PASSWORD %L', '${MEALIE_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT mealie TO ${PROVISIONER_USER};
    SELECT 'CREATE DATABASE mealie OWNER mealie'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mealie')\gexec
EOSQL

echo "Database provisioning complete."
