#!/bin/sh
set -e

echo "Waiting for PostgreSQL..."
until pg_isready -h postgres -p 5432 -U "${PGUSER}" -q 2>/dev/null; do
  sleep 2
done

# Resolve BORG_PASSPHRASE from Docker secret before s6-overlay starts.
# The FILE__ mechanism (init-envfile s6 service) only runs after exec /init,
# so we must read the secret file manually for repo-create.
BORG_PASSPHRASE="$(cat "$FILE__BORG_PASSPHRASE")"
export BORG_PASSPHRASE

# Auto-create borg repository if it doesn't exist (skips if already initialized)
echo "Ensuring borg repository exists..."
borgmatic repo-create --encryption repokey-blake2 --verbosity 1 2>&1 || true

# Hand off to the stock entrypoint (s6-overlay + crond)
exec /init
