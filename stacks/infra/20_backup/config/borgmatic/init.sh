#!/bin/sh
set -e

echo "Waiting for PostgreSQL..."
until pg_isready -h postgres -p 5432 -q 2>/dev/null; do
  sleep 2
done

# Auto-create borg repository if it doesn't exist (skips if already initialized).
# Borgmatic resolves the encryption passphrase from config.yaml via the
# {credential container borg_passphrase} reference -- no env var needed here.
echo "Ensuring borg repository exists..."
borgmatic repo-create --encryption repokey-blake2 --verbosity 1 2>&1 || true

# Hand off to the stock entrypoint (s6-overlay + crond)
exec /init
