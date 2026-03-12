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
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crowdsec') THEN
            EXECUTE format('CREATE ROLE crowdsec LOGIN PASSWORD %L', '${CROWDSEC_POSTGRES_PASSWORD}');
        END IF;
    END
    \$\$;
    ALTER ROLE crowdsec PASSWORD '${CROWDSEC_POSTGRES_PASSWORD}';
    GRANT crowdsec TO ${PROVISIONER_USER};
    SELECT 'CREATE DATABASE crowdsec OWNER crowdsec'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'crowdsec')\gexec
EOSQL

psql -v ON_ERROR_STOP=1 -d crowdsec <<-EOSQL
    GRANT CREATE ON SCHEMA public TO crowdsec;
EOSQL

echo "Database provisioning complete."
