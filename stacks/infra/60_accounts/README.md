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

## Secret Delivery

Three subsystems read secrets three different ways. A single credential value often needs to be
delivered twice because consumers have incompatible interfaces.

| Mode | Consumed by | Form in compose |
|------|-------------|-----------------|
| Python config loader (`file://`) | Select `AUTHENTIK_*` keys (secret key, Postgres password, SMTP password) | `KEY=file:///run/secrets/<name>` plus Docker secret mount |
| Plain env var | Go bootstrap code (`AUTHENTIK_BOOTSTRAP_*`) and Go outpost binaries (`AUTHENTIK_TOKEN`) | `KEY=${VAR}` with value from SOPS-decrypted env |
| Blueprint `!File` tag | Worker at blueprint apply time (OIDC client secrets, admin passwords, outpost tokens referenced from YAML) | Docker secret mount read by YAML loader |

`GLOBAL_PASSWORD` is the canonical dual-delivery example: it's injected as `AUTHENTIK_BOOTSTRAP_PASSWORD`
via plain env (Go bootstrap cannot resolve `file://`) and separately mounted as the
`global_password` secret so the directory blueprint can read it via `!File`. Same value, two
consumers, two delivery paths.

Bootstrap vars are only read on first startup. Subsequent deploys ignore them; the directory
blueprint owns the real admin user after that and deactivates the default `akadmin` account.
