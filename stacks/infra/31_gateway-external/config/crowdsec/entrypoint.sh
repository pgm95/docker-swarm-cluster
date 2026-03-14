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

exec /bin/bash /docker_start.sh "$@"
