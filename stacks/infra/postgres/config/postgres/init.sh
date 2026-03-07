#!/bin/sh
set -e

psql -v ON_ERROR_STOP=1 --username postgres <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${PROVISIONER_DB_USER}') THEN
            EXECUTE format('CREATE ROLE %I LOGIN CREATEDB CREATEROLE PASSWORD %L', '${PROVISIONER_DB_USER}', '${PROVISIONER_DB_PASSWORD}');
            EXECUTE format('GRANT pg_maintain TO %I WITH ADMIN OPTION', '${PROVISIONER_DB_USER}');
        END IF;
    END
    \$\$;
EOSQL

echo "PostgreSQL init complete: ${PROVISIONER_DB_USER} role created."
