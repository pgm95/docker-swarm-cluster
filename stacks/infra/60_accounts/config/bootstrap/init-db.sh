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
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authentik') THEN
            EXECUTE format('CREATE ROLE authentik LOGIN PASSWORD %L', '${AUTHENTIK_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT authentik TO ${PROVISIONER_USER};
    SELECT 'CREATE DATABASE authentik OWNER authentik'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'authentik')\gexec
EOSQL

echo "Database provisioning complete."
