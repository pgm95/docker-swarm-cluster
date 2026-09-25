#!/bin/sh
set -e

mkdir -p "${USER_CONFIGS_DIR}" "${GROUP_CONFIGS_DIR}"

cat > "${GROUP_CONFIGS_DIR}/${GLOBAL_ADMIN_GROUP}.json" <<EOF
{"name": "${GLOBAL_ADMIN_GROUP}"}
EOF

cat > "${GROUP_CONFIGS_DIR}/${GLOBAL_USER_GROUP}.json" <<EOF
{"name": "${GLOBAL_USER_GROUP}"}
EOF

cat > "${GROUP_CONFIGS_DIR}/lldap_service.json" <<EOF
{"name": "lldap_service"}
EOF

# lldap's bootstrap.sh sends every profile field it is not given as an empty string,
# any existing values are overwritten, so the admin's profile is declared here in full
cat > "${USER_CONFIGS_DIR}/${LLDAP_ADMIN_USERNAME}.json" <<EOF
{
  "id": "${LLDAP_ADMIN_USERNAME}",
  "email": "${LLDAP_ADMIN_EMAIL}",
  "displayName": "${LLDAP_ADMIN_USERNAME}",
  "groups": ["lldap_admin", "${GLOBAL_ADMIN_GROUP}", "${GLOBAL_USER_GROUP}"]
}
EOF

cat > "${USER_CONFIGS_DIR}/${AUTHENTIK_BIND_USER}.json" <<EOF
{
  "id": "${AUTHENTIK_BIND_USER}",
  "email": "authentik@service.internal",
  "password_file": "${AUTHENTIK_BIND_PASSWORD_FILE}",
  "groups": ["lldap_password_manager", "lldap_service"]
}
EOF

cat > "${USER_CONFIGS_DIR}/${JELLYFIN_BIND_USER}.json" <<EOF
{
  "id": "${JELLYFIN_BIND_USER}",
  "email": "jellyfin@service.internal",
  "password_file": "${JELLYFIN_BIND_PASSWORD_FILE}",
  "groups": ["lldap_password_manager", "lldap_service"]
}
EOF

cat > "${USER_CONFIGS_DIR}/${TEST_USER}.json" <<EOF
{
  "id": "${TEST_USER}",
  "email": "test@service.internal",
  "password_file": "${TEST_USER_PASSWORD_FILE}",
  "groups": ["${GLOBAL_USER_GROUP}", "lldap_service"]
}
EOF

/app/bootstrap.sh

echo "LDAP bootstrap complete."
