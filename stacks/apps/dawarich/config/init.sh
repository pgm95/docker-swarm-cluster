#!/bin/sh
set -e

# Dawarich reads every credential from the environment only. Export from Docker secrets,
# then hand over to the stock script given as the compose command.
SECRET_KEY_BASE="$(cat /run/secrets/dawarich_secret_key_base)"
PHOTON_API_KEY="$(cat /run/secrets/dawarich_photon_api_key)"
DATABASE_PASSWORD="$(cat /run/secrets/dawarich_db_password)"
OIDC_CLIENT_SECRET="$(cat /run/secrets/oidc_dawarich_client_secret)"
OTP_ENCRYPTION_PRIMARY_KEY="$(cat /run/secrets/dawarich_otp_primary_key)"
OTP_ENCRYPTION_DETERMINISTIC_KEY="$(cat /run/secrets/dawarich_otp_deterministic_key)"
OTP_ENCRYPTION_KEY_DERIVATION_SALT="$(cat /run/secrets/dawarich_otp_key_derivation_salt)"
export SECRET_KEY_BASE PHOTON_API_KEY DATABASE_PASSWORD OIDC_CLIENT_SECRET OTP_ENCRYPTION_PRIMARY_KEY OTP_ENCRYPTION_DETERMINISTIC_KEY OTP_ENCRYPTION_KEY_DERIVATION_SALT

exec "$@"
