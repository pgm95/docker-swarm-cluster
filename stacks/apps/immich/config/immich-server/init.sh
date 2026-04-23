#!/bin/sh
set -e

OWNER="${IMMICH_OWNER:-1000:1000}"

if [ ! -f /data/.volume-init ]; then
    chown -R "${OWNER}" /data
    touch /data/.volume-init
    echo "init: chowned /data to ${OWNER}"
fi

exec setpriv --reuid="${OWNER%%:*}" --regid="${OWNER##*:}" --clear-groups \
    tini -- /usr/src/app/server/bin/start.sh "$@"
