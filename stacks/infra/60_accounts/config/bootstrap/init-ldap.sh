#!/bin/sh
set -e

mkdir -p "${USER_CONFIGS_DIR}" "${GROUP_CONFIGS_DIR}"

cat > "${GROUP_CONFIGS_DIR}/${GLOBAL_ADMIN_GROUP}.json" <<EOF
{"name": "${GLOBAL_ADMIN_GROUP}"}
EOF

cat > "${USER_CONFIGS_DIR}/${AUTHENTIK_BIND_USER}.json" <<EOF
{
  "id": "${AUTHENTIK_BIND_USER}",
  "email": "authentik@service.internal",
  "password_file": "${AUTHENTIK_BIND_PASSWORD_FILE}",
  "groups": ["lldap_password_manager"]
}
EOF

cat > "${USER_CONFIGS_DIR}/${JELLYFIN_BIND_USER}.json" <<EOF
{
  "id": "${JELLYFIN_BIND_USER}",
  "email": "jellyfin@service.internal",
  "password_file": "${JELLYFIN_BIND_PASSWORD_FILE}",
  "groups": ["lldap_password_manager"]
}
EOF

/app/bootstrap.sh

echo "LDAP bootstrap complete."
