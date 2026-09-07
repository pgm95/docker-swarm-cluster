#!/bin/sh
set -e

# Seerr reads DB_PASS from the environment only; the image has no ENTRYPOINT and CMD is npm start.
DB_PASS="$(cat /run/secrets/servarr_seerr_db_password)"
export DB_PASS

exec npm start
