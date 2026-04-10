# Accounts Stack

Authentication, identity management, and OIDC provider via Authentik.

## Blueprint Extension

Only `*.yaml` files are discovered. Authentik's `blueprints_find()` uses `rglob("**/*.yaml")` --
files with `.yml` extension are silently ignored.

## Resource Requirements

Server and worker each need `*resources-huge` (1024M). Authentik is a full Django application --
`*resources-medium` (256M) causes OOMKill, `*resources-large` (512M) is borderline.

## LDAP Outpost Token Pre-Sharing

Authentik normally auto-generates a service account + token when creating an outpost, and grants
object-level permissions via `Outpost.save()`. When providing an external service account via
`token_identifier`, the outpost skips auto-generation but also skips permission grants.

The `ldap.yaml` blueprint solves this by creating the full permission chain:

1. RBAC role with global permissions (`view_user`, `view_group`, `add_event`) in `attrs.permissions`
2. Group assigned that role
3. Service account in that group
4. Token with pre-shared key via `!File /run/secrets/...`
5. Outpost with `token_identifier` pointing to our token
6. Object-level permissions on the outpost and LDAP provider entries (`permissions` field with `user` ref)

The ldap-outpost container receives the same token value via `AUTHENTIK_TOKEN` env var from SOPS.
Entry order matters -- all entries are in one atomic transaction.

## LDAP Authentication Flow

The LDAP provider uses a dedicated authentication flow (`ldap-authentication-flow`) instead of the
default. LDAP clients send plain username+password binds and cannot handle MFA challenges. The
default flow includes an `AuthenticatorValidateStage` that would cause bind failures for any user
with MFA enabled. The custom flow has only identification (with inline password) and login stages.

A `ldapservice` user in the `ldapsearch` group provides a dedicated bind account for LDAP consumers
(e.g., Jellyfin). The `search_full_directory` permission (replacing the deprecated `search_group`
provider field removed in 2024.8) is granted to the `ldapsearch` group via an RBAC role in the
blueprint.

## Deploy-Triggered Restart

The worker and LDAP outpost have `DEPLOY_VERSION` in their environment. This forces Swarm to
recreate both containers on every deploy. The worker restart triggers fresh blueprint discovery
(the inotify file watcher misses Docker Config atomic swaps). The outpost restart picks up
permission and provider config changes that aren't pushed via websocket.

## WebFinger Bare-Domain Redirect

WebFinger spec requires `/.well-known/webfinger` on the account's domain (`DOMAIN_PUBLIC`), not
the auth subdomain (`auth.DOMAIN_PUBLIC`). A Traefik router on `DOMAIN_PUBLIC` redirects
WebFinger requests to `auth.DOMAIN_PUBLIC` so Authentik constructs the OIDC issuer URL with
the correct domain. Without the redirect, Authentik uses the request Host (`DOMAIN_PUBLIC`)
in the `href`, but OIDC endpoints are only served at `auth.DOMAIN_PUBLIC`.

The brand's `default_application` is the Tailscale app -- Authentik's per-application OIDC
issuers mean WebFinger returns the default app's slug in the issuer path.

## Blueprint Ordering

Blueprints use `NN_` prefixes to enforce processing order via Authentik's alphabetical
discovery. Cross-blueprint `!Find` references require the target entity to exist first.

| Prefix | Blueprint | Depends on |
|--------|-----------|------------|
| `10_` | directory | -- |
| `20_` | scope-mappings | -- |
| `30_` | providers | scope-mappings (custom scopes) |
| `40_` | ldap | -- |
| `50_` | brand | providers (Tailscale app for WebFinger) |

## Bootstrap Limitations

`AUTHENTIK_BOOTSTRAP_PASSWORD`, `AUTHENTIK_BOOTSTRAP_TOKEN`, and `AUTHENTIK_BOOTSTRAP_EMAIL`
cannot use `file://` syntax -- they must be plain env vars. Only read on first startup; subsequent
deploys ignore them. The `directory.yaml` blueprint creates the real admin user and deactivates
the default `akadmin` account.

The LDAP outpost's `AUTHENTIK_TOKEN` env var also cannot use `file://` (Go binary limitation).
