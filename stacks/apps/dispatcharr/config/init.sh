#!/bin/bash
set -e

# POSTGRES_PASSWORD is env only. Export from the Docker secret, then run the stock entrypoint
# given as the compose command (web or celery).
POSTGRES_PASSWORD="$(cat /run/secrets/dispatcharr_db_password)"
export POSTGRES_PASSWORD

exec "$@"
