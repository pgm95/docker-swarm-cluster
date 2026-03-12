#!/bin/sh
set -e

mkdir -p /tmp/bootstrap/user-configs /tmp/bootstrap/group-configs

cat > /tmp/bootstrap/group-configs/app_admin.json <<EOF
{"name": "${GLOBAL_LDAP_ADMIN_GROUP}"}
EOF

cat > /tmp/bootstrap/user-configs/admin.json <<EOF
{
  "id": "${LLDAP_ADMIN_USERNAME}",
  "email": "${LLDAP_ADMIN_USERNAME}@${DOMAIN_PUBLIC}",
  "displayName": "${LLDAP_ADMIN_USERNAME}",
  "password_file": "/run/secrets/lldap_ldap_user_pass",
  "groups": ["${GLOBAL_LDAP_ADMIN_GROUP}", "lldap_password_manager"]
}
EOF

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

echo "LDAP bootstrap complete."
