#!/bin/sh
set -e

OWNER="${VM_OWNER:-1000:1000}"
UID_VAL="${OWNER%%:*}"
GID_VAL="${OWNER##*:}"

# BusyBox su requires a passwd entry (unlike setpriv which takes raw UIDs)
getent group "${GID_VAL}" >/dev/null 2>&1 || addgroup -g "${GID_VAL}" -S app
getent passwd "${UID_VAL}" >/dev/null 2>&1 || adduser -u "${UID_VAL}" -G "$(getent group "${GID_VAL}" | cut -d: -f1)" -S -D -H app
APP_USER="$(getent passwd "${UID_VAL}" | cut -d: -f1)"

for dir in /storage; do
    if [ ! -f "${dir}/.volume-init" ]; then
        chown -R "${OWNER}" "${dir}"
        touch "${dir}/.volume-init"
        echo "init: chowned ${dir} to ${OWNER}"
    fi
done

# Alpine/BusyBox setpriv only handles capabilities, not --reuid/--regid — use su instead
exec su -s /bin/sh "${APP_USER}" -c 'exec /victoria-metrics-prod "$@"' -- sh "$@"
