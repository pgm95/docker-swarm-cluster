#!/bin/sh
set -e

OWNER="${QUANTUM_OWNER:-1000:1000}"

for dir in /quantum; do
    if [ ! -f "${dir}/.volume-init" ]; then
        chown -R "${OWNER}" "${dir}"
        touch "${dir}/.volume-init"
        echo "init: chowned ${dir} to ${OWNER}"
    fi
done

exec setpriv --reuid="${OWNER%%:*}" --regid="${OWNER##*:}" --clear-groups \
    ./filebrowser "$@"
