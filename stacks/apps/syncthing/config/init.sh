#!/bin/sh
set -e

# STGUIAPIKEY is env only. The stock entrypoint still drops to PUID/PGID via su-exec.
STGUIAPIKEY="$(cat /run/secrets/widget_syncthing_api_key)"
export STGUIAPIKEY

exec /bin/entrypoint.sh /bin/syncthing
