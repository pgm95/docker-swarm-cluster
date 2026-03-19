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
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'radarr') THEN
            EXECUTE format('CREATE ROLE radarr LOGIN PASSWORD %L', '${RADARR_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT radarr TO ${PROVISIONER_USER};
    SELECT 'CREATE DATABASE "radarr-main" OWNER radarr'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'radarr-main')\gexec
    SELECT 'CREATE DATABASE "radarr-log" OWNER radarr'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'radarr-log')\gexec

    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sonarr') THEN
            EXECUTE format('CREATE ROLE sonarr LOGIN PASSWORD %L', '${SONARR_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT sonarr TO ${PROVISIONER_USER};
    SELECT 'CREATE DATABASE "sonarr-main" OWNER sonarr'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sonarr-main')\gexec
    SELECT 'CREATE DATABASE "sonarr-log" OWNER sonarr'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sonarr-log')\gexec

    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'prowlarr') THEN
            EXECUTE format('CREATE ROLE prowlarr LOGIN PASSWORD %L', '${PROWLARR_DB_PASSWORD}');
        END IF;
    END
    \$\$;
    GRANT prowlarr TO ${PROVISIONER_USER};
    SELECT 'CREATE DATABASE "prowlarr-main" OWNER prowlarr'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'prowlarr-main')\gexec
    SELECT 'CREATE DATABASE "prowlarr-log" OWNER prowlarr'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'prowlarr-log')\gexec
EOSQL

echo "Database provisioning complete."
