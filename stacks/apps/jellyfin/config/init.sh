#!/bin/sh
set -e

OWNER="${JELLYFIN_OWNER:-1000:1000}"

for dir in /etc/jellyfin /var/lib/jellyfin /var/log/jellyfin /var/cache/jellyfin; do
    if [ ! -f "${dir}/.volume-init" ]; then
        chown -R "${OWNER}" "${dir}"
        touch "${dir}/.volume-init"
        echo "init: chowned ${dir} to ${OWNER}"
    fi
done

exec setpriv --reuid="${OWNER%%:*}" --regid="${OWNER##*:}" --clear-groups \
    /jellyfin/jellyfin "$@"
