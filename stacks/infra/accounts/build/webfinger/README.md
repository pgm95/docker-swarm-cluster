# Carpal WebFinger with Environment Variables

Custom image that processes config templates with environment variables at startup.

## How it Works

1. `swarm:deploy` auto-builds and pushes the image (content-hash tag from build context)
2. Container starts with `entrypoint.sh` which reads templates from `/config/` (Docker Configs)
3. Script expands environment variables from SOPS-encrypted secrets via sed
4. Writes processed files to `/etc/carpal/` inside the container
5. Starts carpal with processed configs

## Build Details

The Dockerfile copies `entrypoint.sh` into the image and fixes `/etc/carpal` permissions for the non-root user at build time (`chown -R 1000:1000`). The compose service sets `user: ${GLOBAL_NONROOT_DOCKER}` to run as non-root.

## Required Environment Variables

Set in stack `secrets.env` (SOPS-encrypted):

- `CARPAL_LDAP_BIND_USER` - LDAP bind username
- `CARPAL_LDAP_BIND_PASS` - LDAP bind password

Set in `GLOBAL_SECRETS` (SOPS-encrypted):

- `GLOBAL_LDAP_BASE_DN` - LDAP base DN
- `GLOBAL_OIDC_URL` - OpenID Connect issuer URL
