#!/bin/sh
set -e

# Stirling reads credentials from env only; export them from the Docker secrets,
# then run the stock entrypoint (tini with the image's init script).
SECURITY_OAUTH2_CLIENTSECRET="$(cat /run/secrets/oidc_stirling_client_secret)"
SECURITY_INITIALLOGIN_PASSWORD="$(cat /run/secrets/global_admin_password)"
export SECURITY_OAUTH2_CLIENTSECRET SECURITY_INITIALLOGIN_PASSWORD

exec tini -- /scripts/init.sh "$@"
