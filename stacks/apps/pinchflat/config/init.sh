#!/bin/sh
set -e

OWNER="${PINCHFLAT_OWNER:-1000:1000}"

for dir in /config; do
    if [ ! -f "${dir}/.volume-init" ]; then
        chown -R "${OWNER}" "${dir}"
        touch "${dir}/.volume-init"
        echo "init: chowned ${dir} to ${OWNER}"
    fi
done

exec setpriv --reuid="${OWNER%%:*}" --regid="${OWNER##*:}" --clear-groups \
    /app/bin/docker_start "$@"
