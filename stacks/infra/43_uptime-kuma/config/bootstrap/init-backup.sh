#!/bin/sh
set -e

MYSQL_HOST="mariadb"
MYSQL_PORT="3306"
MYSQL_PWD="$(cat /run/secrets/mariadb_root_password)"
BACKUP_PASSWORD="$(cat /run/secrets/uptime_kuma_backup_db_password)"
export MYSQL_PWD

echo "Waiting for MariaDB..."
until mariadb-admin ping -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u root --silent 2>/dev/null; do
  sleep 2
done

# Single quotes inside the password are escaped by SQL doubling.
ESCAPED_PASSWORD=$(printf '%s' "${BACKUP_PASSWORD}" | sed "s/'/''/g")

mariadb -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u root <<-EOSQL
    CREATE USER IF NOT EXISTS 'backup'@'%' IDENTIFIED BY '${ESCAPED_PASSWORD}';
    ALTER USER 'backup'@'%' IDENTIFIED BY '${ESCAPED_PASSWORD}';
    GRANT SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER, RELOAD, PROCESS, REPLICATION CLIENT ON *.* TO 'backup'@'%';
    FLUSH PRIVILEGES;
EOSQL

echo "MariaDB backup user provisioning complete."
