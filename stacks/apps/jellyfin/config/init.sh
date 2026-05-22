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

DRI_GIDS=$(stat -c '%g' /dev/dri/* 2>/dev/null | sort -un | grep -v '^0$' | paste -sd, -)

if [ -n "${DRI_GIDS}" ]; then
    echo "init: granting supplementary GIDs ${DRI_GIDS} for /dev/dri"
    exec setpriv --reuid="${OWNER%%:*}" --regid="${OWNER##*:}" --groups "${DRI_GIDS}" \
        /jellyfin/jellyfin "$@"
else
    exec setpriv --reuid="${OWNER%%:*}" --regid="${OWNER##*:}" --clear-groups \
        /jellyfin/jellyfin "$@"
fi
