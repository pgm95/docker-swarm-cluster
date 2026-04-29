#!/bin/sh
set -eu

DB="/data/data/jellyfin.db"
USERNAME="${CLEANUP_USERNAME:?CLEANUP_USERNAME is required}"
INTERVAL="${CLEANUP_INTERVAL:-86400}"

trap 'echo "[cleanup] signal received, exiting"; exit 0' TERM INT

while [ ! -f "${DB}" ]; do
    echo "[cleanup] waiting for ${DB} to appear..."
    sleep 30
done

while true; do
    deleted=$(sqlite3 "${DB}" "
        DELETE FROM ActivityLogs
        WHERE UserId = (SELECT Id FROM Users WHERE Username = '${USERNAME}');
        SELECT changes();
    " 2>&1) || {
        echo "[cleanup] $(date -u +%FT%TZ) sqlite error: ${deleted}"
        sleep 60
        continue
    }
    echo "[cleanup] $(date -u +%FT%TZ) deleted ${deleted} rows for ${USERNAME}"
    sleep "${INTERVAL}"
done
