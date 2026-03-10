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
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana') THEN
            EXECUTE format('CREATE ROLE grafana LOGIN PASSWORD %L', '${GRAFANA_POSTGRES_PASSWORD}');
        END IF;
    END
    \$\$;
    ALTER ROLE grafana PASSWORD '${GRAFANA_POSTGRES_PASSWORD}';
    GRANT grafana TO ${PROVISIONER_USER};
    SELECT 'CREATE DATABASE grafana OWNER grafana'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'grafana')\gexec
EOSQL

echo "Database provisioning complete."
exec sleep infinity
