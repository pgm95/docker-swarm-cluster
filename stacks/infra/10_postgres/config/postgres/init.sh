#!/bin/sh
set -e

GLOBAL_DB_PROVISIONER_PASSWORD="$(cat /run/secrets/global_db_provisioner_password)"

psql -v ON_ERROR_STOP=1 --username postgres <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${GLOBAL_DB_PROVISIONER_USER}') THEN
            EXECUTE format('CREATE ROLE %I LOGIN CREATEDB CREATEROLE PASSWORD %L', '${GLOBAL_DB_PROVISIONER_USER}', '${GLOBAL_DB_PROVISIONER_PASSWORD}');
            EXECUTE format('GRANT pg_maintain TO %I WITH ADMIN OPTION', '${GLOBAL_DB_PROVISIONER_USER}');
            EXECUTE format('GRANT pg_read_all_data TO %I WITH ADMIN OPTION', '${GLOBAL_DB_PROVISIONER_USER}');
        END IF;
    END
    \$\$;
EOSQL

echo "PostgreSQL init complete: ${GLOBAL_DB_PROVISIONER_USER} role created."
