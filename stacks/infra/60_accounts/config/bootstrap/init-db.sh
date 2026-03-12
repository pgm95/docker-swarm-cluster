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
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authelia') THEN
            EXECUTE format('CREATE ROLE authelia LOGIN PASSWORD %L', '${AUTHELIA_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT authelia TO ${PROVISIONER_USER};
    SELECT 'CREATE DATABASE authelia OWNER authelia'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'authelia')\gexec

    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lldap') THEN
            EXECUTE format('CREATE ROLE lldap LOGIN PASSWORD %L', '${LLDAP_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT lldap TO ${PROVISIONER_USER};
    SELECT 'CREATE DATABASE lldap OWNER lldap'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'lldap')\gexec
EOSQL

echo "Database provisioning complete."
