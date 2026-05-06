#!/bin/sh
set -e

echo "Waiting for PostgreSQL..."
until pg_isready -h postgres -p 5432 -q 2>/dev/null; do
  sleep 2
done

# Auto-create borg repository if it doesn't exist (skips if already initialized).
# borgmatic iterates every config in /etc/borgmatic.d/; all of them point at
# /mnt/borg-repository, so the first invocation creates the repo and the rest
# log "Repository already exists. Skipping creation." Encryption passphrase
# resolves from {credential container borg_passphrase} in common.yaml
echo "Ensuring borg repository exists..."
borgmatic repo-create --encryption repokey-blake2 --verbosity 1 2>&1 || true

# Hand off to the stock entrypoint (s6-overlay + crond)
exec /init
