#!/bin/bash
# Wait for Postgres to be reachable before starting CrowdSec.
# The stock entrypoint runs database operations immediately (cscli machines list/add),
# which fail if the overlay network hasn't resolved the postgres hostname yet.

PGHOST="postgres"
PGPORT="5432"
TIMEOUT=60
elapsed=0

echo "Waiting for PostgreSQL at ${PGHOST}:${PGPORT}..."
while ! nc -z "${PGHOST}" "${PGPORT}" 2>/dev/null; do
    if [ "${elapsed}" -ge "${TIMEOUT}" ]; then
        echo "ERROR: PostgreSQL not reachable after ${TIMEOUT}s"
        exit 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done
echo "PostgreSQL reachable (${elapsed}s)"

# Credentials arrive as Docker secrets; the stock entrypoint and config.yaml.local read env vars.
BOUNCER_KEY_TRAEFIK="$(cat /run/secrets/gateway_external_bouncer_key)"
GATEWAY_EXTERNAL_CROWDSEC_CTI_KEY="$(cat /run/secrets/gateway_external_crowdsec_cti_key)"
GATEWAY_EXTERNAL_CROWDSEC_DB_PASSWORD="$(cat /run/secrets/gateway_external_crowdsec_db_password)"
AGENT_PASSWORD="$(cat /run/secrets/widget_crowdsec_password)"
export BOUNCER_KEY_TRAEFIK GATEWAY_EXTERNAL_CROWDSEC_CTI_KEY GATEWAY_EXTERNAL_CROWDSEC_DB_PASSWORD AGENT_PASSWORD

exec /bin/bash /docker_start.sh "$@"
