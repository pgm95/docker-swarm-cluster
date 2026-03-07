#!/bin/sh
set -e

mkdir -p /tmp/bootstrap/user-configs

cat > /tmp/bootstrap/user-configs/authelia_bind.json <<EOF
{
  "id": "${BOOTSTRAP_AUTHELIA_ID}",
  "email": "${BOOTSTRAP_AUTHELIA_ID}@service.internal",
  "displayName": "bind-authelia",
  "password_file": "/run/secrets/authelia_ldap_bind_pass",
  "groups": ["lldap_password_manager"]
}
EOF

cat > /tmp/bootstrap/user-configs/carpal_bind.json <<EOF
{
  "id": "${BOOTSTRAP_CARPAL_ID}",
  "email": "${BOOTSTRAP_CARPAL_ID}@service.internal",
  "displayName": "bind-carpal",
  "password_file": "/run/secrets/carpal_ldap_bind_pass",
  "groups": ["lldap_strict_readonly"]
}
EOF

/app/bootstrap.sh

exec sleep infinity
