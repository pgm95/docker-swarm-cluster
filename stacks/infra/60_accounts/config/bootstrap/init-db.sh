#!/bin/sh
set -e

PGHOST="postgres"
PGPORT="5432"
PGUSER="${GLOBAL_DB_PROVISIONER_USER}"
PGDATABASE="postgres"
PGPASSWORD="$(cat /run/secrets/global_db_provisioner_password)"
AUTHENTIK_DB_PASSWORD="$(cat /run/secrets/accounts_authentik_db_password)"
LLDAP_DB_PASSWORD="$(cat /run/secrets/accounts_lldap_db_password)"
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
    GRANT authentik TO ${GLOBAL_DB_PROVISIONER_USER};
    SELECT 'CREATE DATABASE authentik OWNER authentik'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'authentik')\gexec

    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lldap') THEN
            EXECUTE format('CREATE ROLE lldap LOGIN PASSWORD %L', '${LLDAP_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT lldap TO ${GLOBAL_DB_PROVISIONER_USER};
    SELECT 'CREATE DATABASE lldap OWNER lldap'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'lldap')\gexec
EOSQL

echo "Database provisioning complete."
