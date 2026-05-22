#!/bin/sh
set -e

OWNER="${IMMICH_OWNER:-1000:1000}"

if [ ! -f /data/.volume-init ]; then
    chown -R "${OWNER}" /data
    touch /data/.volume-init
    echo "init: chowned /data to ${OWNER}"
fi

DRI_GIDS=$(stat -c '%g' /dev/dri/* 2>/dev/null | sort -un | grep -v '^0$' | paste -sd, -)

if [ -n "${DRI_GIDS}" ]; then
    echo "init: granting supplementary GIDs ${DRI_GIDS} for /dev/dri"
    exec setpriv --reuid="${OWNER%%:*}" --regid="${OWNER##*:}" --groups "${DRI_GIDS}" \
        tini -- /usr/src/app/server/bin/start.sh "$@"
else
    exec setpriv --reuid="${OWNER%%:*}" --regid="${OWNER##*:}" --clear-groups \
        tini -- /usr/src/app/server/bin/start.sh "$@"
fi
