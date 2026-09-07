#!/bin/sh
set -e

sed -e "s/\${DOMAIN_PUBLIC}/${DOMAIN_PUBLIC}/g" \
    -e "s/\${DOMAIN_PRIVATE}/${DOMAIN_PRIVATE}/g" /tmp/config.yaml.tpl > "${FILEBROWSER_CONFIG}"

# Credentials are env only; export from Docker secrets.
FILEBROWSER_ADMIN_PASSWORD="$(cat /run/secrets/quantum_admin_password)"
FILEBROWSER_JWT_TOKEN_SECRET="$(cat /run/secrets/quantum_jwt_secret_key)"
FILEBROWSER_OIDC_CLIENT_SECRET="$(cat /run/secrets/oidc_quantum_client_secret)"
export FILEBROWSER_ADMIN_PASSWORD FILEBROWSER_JWT_TOKEN_SECRET FILEBROWSER_OIDC_CLIENT_SECRET

exec ./filebrowser "$@"
