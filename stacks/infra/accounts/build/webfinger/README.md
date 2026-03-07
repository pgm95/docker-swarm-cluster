# Carpal WebFinger with Environment Variables

This setup allows carpal to use environment variables for configuration while keeping sensitive data out of git.

## How it Works

1. Build custom image: `mise run oci:build stacks/infra/accounts/build/webfinger v1`
2. Container starts with `entrypoint.sh` as entrypoint
3. Script reads templates from `/config/` (bind-mounted from `config/webfinger/`)
4. Expands environment variables from SOPS-encrypted secrets
5. Writes processed files to `/etc/carpal/` inside container
6. Starts carpal with processed configs

## Required Environment Variables

Set in stack `secrets.env` (SOPS-encrypted):

- `CARPAL_LDAP_BIND_USER` - LDAP bind username
- `CARPAL_LDAP_BIND_PASS` - LDAP bind password

Set in `GLOBAL_SECRETS` (SOPS-encrypted):

- `GLOBAL_LDAP_BASE_DN` - LDAP base DN
- `GLOBAL_OIDC_URL` - OpenID Connect issuer URL

## Notes

- Original config files remain unchanged (templates with placeholders)
- Secrets managed via SOPS encryption
- Config volume mounted read-only (`:ro`) for security
- Runs as user 1000:1000 (non-root)
- Custom Dockerfile fixes `/etc/carpal` permissions for user 1000:1000
- Entrypoint processes templates at startup before starting carpal server
