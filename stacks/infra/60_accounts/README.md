# Accounts Stack

Identity, authentication, and OIDC provider for the cluster. Authentik (server +
worker) plus an embedded lldap directory it consumes as an LDAP Source. Init
sidecars provision the Postgres roles/databases and seed lldap service accounts.

## LDAP Source

Authentik consumes lldap as an `LDAPSource` (see `25_ldap-source.yaml`). Sync
runs every 2 hours plus on-save plus on hourly connectivity check.

The bind identity is `uid=_bind_authentik,ou=people,${GLOBAL_LDAP_BASE_DN}`,
seeded by `init-ldap` into `lldap_password_manager`. Authentik's worker reaches
lldap intra-stack at `lldap:389` over the stack-default overlay; the alias on
`infra_ldap` exists for cross-stack consumers (Jellyfin), not for Authentik.

### What syncs which way

| Change | Direction | When |
|---|---|---|
| User / group create, delete, attribute edit in lldap | lldap to Authentik | scheduled sync (`5 */2 * * *`) or `LDAPSource.save()` |
| Password change in lldap | lldap to Authentik (per user) | next successful Authentik web login by that user; cached hash rewritten via bind delegation |
| Password change in Authentik UI | Authentik to lldap | instant RFC 3062 modify; rejected for `lldap_admin` members |
| Anything else edited in Authentik UI on a sourced user / group (name, email, avatar, membership, deletion) | not propagated | Authentik DB only; clobbered on next sync if the field is mapped from lldap |
| Authentik-only users / groups (created in UI or by blueprint) | not propagated | live alongside lldap-sourced ones; sync ignores them |

Operating model: edit identities (users, groups, memberships, avatars) in lldap;
edit Authentik-only resources (apps, OIDC providers, application groups) in
Authentik. The two scopes don't overlap.

### Authentication paths

Three paths exist; they interact subtly.

1. **Pull sync (lldap to Authentik)**: the worker iterates `(objectClass=person)`
   and `(objectClass=groupOfUniqueNames)` under `ou=people` / `ou=groups`,
   matching by `entryUUID`. Property mappings populate `name`, `email`,
   `attributes.givenName`, `attributes.sn`, and `attributes.avatar` (as a
   `data:image/jpeg;base64,...` URL inline in JSON). lldap does not expose
   `userPassword` over LDAP, so sync never touches passwords.
2. **Bind delegation on Authentik web login**: `LDAPBackend` opens a fresh
   ldap3 bind to `lldap:389` as the user's DN with their candidate password.
   On success, `password_login_update_internal_password=true` writes the
   hash into Authentik's local DB via `User.set_password()`.
3. **Writeback on Authentik password change**: `password_changed` Django
   signal fires `LDAPPasswordChanger.change_password`, which tries the AD
   `unicodePwd` modify (rejected by lldap) then falls back to RFC 3062
   password modify, accepted by lldap.

### Sync matching is identifier-based

The sync code path uses `User.update_or_create_attributes({attributes__ldap_uniq:
<entryUUID>}, defaults)`, NOT the source's `user_matching_mode` setting. Matching
modes only apply to web-flow login enrollment.

Practical consequence: an Authentik user that already exists with the same
username but no `attributes.ldap_uniq` (e.g., the bootstrap admin from
`10_directory.yaml`) will collide on the unique `username` constraint at sync
time and log a `configuration_error` event. Set the existing user's
`attributes.ldap_uniq` to the matching lldap `entryUUID` once via `ak shell`,
then the next sync adopts cleanly. One-time per environment.

### Writeback limitation: admin

`bind_authentik` is in `lldap_password_manager`. lldap's permission model
rejects password modifications targeting users in `lldap_admin`. Authentik UI
password changes for the directory admin (`pggm95`) therefore do not
propagate to lldap; family users are unaffected. Use the lldap admin UI
directly for the admin's password.

### Stale-hash window

The cached hash is rewritten only on a successful Authentik web login with the
new password. Between an lldap-side password change and the next Authentik
login by that user, the OLD password still works against Authentik because
Django's `InbuiltBackend` is tried before `LDAPBackend` and the cache is not
invalidated. Bounded; mitigated by routing password changes through Authentik
when possible.

## Avatars

Property mapping `lldap-user-avatar` reads the raw `jpegPhoto` bytes from
lldap, base64-encodes them, and stores `attributes.avatar = data:image/jpeg;base64,<...>`
on the User row. Frontend renders the value verbatim as `<img src=...>`.

Resolution chain is set on the default tenant via `AUTHENTIK_AVATARS` env on
both server and worker. The env var is bootstrap-only: it seeds
`tenant.avatars` on first run, after which the tenant DB row is authoritative.
The `authentik_tenants.tenant` model is not blueprintable in 2026.2, so the
env var is the only declarative surface for this setting.

## WebFinger bare-domain redirect

WebFinger requires `/.well-known/webfinger` on the bare domain
(`DOMAIN_PUBLIC`), not on `auth.DOMAIN_PUBLIC`. A Traefik router on
`DOMAIN_PUBLIC` redirects WebFinger requests to the auth subdomain so
Authentik constructs the OIDC issuer URL with the correct domain. Without it,
Authentik uses the request Host in the `href`, but OIDC endpoints only exist
at `auth.DOMAIN_PUBLIC`.

The brand's `default_application` is the Tailscale app. Authentik's
per-application OIDC issuers mean WebFinger returns the default app's slug
in the issuer path.

## Blueprint ordering

Blueprints use `NN_` prefixes to enforce processing order via Authentik's
alphabetical discovery. Cross-blueprint `!Find` references require the target
to exist first.

| Prefix | Blueprint | Depends on |
|---|---|---|
| `10_` | directory | none |
| `20_` | scope-mappings | none |
| `25_` | ldap-source | none (uses built-in `system/sources-ldap.yaml` mappings via `!Find`) |
| `30_` | providers | scope-mappings (custom scopes) |
| `50_` | brand | providers (Tailscale app for WebFinger) |

Authentik's `blueprints_find()` uses `rglob("**/*.yaml")`. Files with `.yml`
extension are silently ignored.

## Brand entry

The default brand entry in `50_brand.yaml` requires `domain: authentik-default`
explicitly. Authentik 2026.2's brand serializer rejects entries without it
even when matched by `default: true` identifier.

## Secret delivery

Three subsystems read secrets in three incompatible ways. A single credential
value sometimes ships twice because the consumers cannot agree on a format.

| Mode | Consumed by | Compose form |
|---|---|---|
| Python config loader (`file://`) | Select `AUTHENTIK_*` keys (secret key, Postgres password, SMTP password) | `KEY=file:///run/secrets/<name>` plus Docker secret mount |
| Plain env var | Go bootstrap (`AUTHENTIK_BOOTSTRAP_*`), Postgres URI interpolation (`LLDAP_DATABASE_URL`) | `KEY=${VAR}` with value from SOPS env |
| Blueprint `!File` tag | Worker at blueprint apply time (OIDC client secrets, admin password, LDAP bind password) | Docker secret mount read at YAML parse time |

`GLOBAL_PASSWORD` is the canonical dual-delivery example. It feeds:

1. lldap admin via `LLDAP_LDAP_USER_PASS_FILE`
2. lldap bootstrap admin via `LLDAP_ADMIN_PASSWORD_FILE` (init-ldap)
3. Authentik primary admin via `!File /run/secrets/global_password` in
   `10_directory.yaml`

All three consumers mount the same versioned `global_password` secret
(remapped from `GLOBAL_PASSWORD` via the `name:` indirection in `secrets.yml`).

`AUTHENTIK_BOOTSTRAP_PASSWORD` and friends are commented out in compose.
They are only read on first startup; the directory blueprint owns the admin
user thereafter and deactivates the default `akadmin`.

## Operational notes

### Init order and convergence

`init-db` provisions both the `authentik` and `lldap` Postgres roles and
databases. lldap and the Authentik tasks may crash-loop briefly on a fresh
deploy until init-db completes; restart policy handles recovery without
intervention. `init-ldap` reaches lldap on `http://lldap:17170` and waits
internally before bootstrapping the bind users.

### LDAP_KEY_SEED preservation

If lldap's Postgres database is being migrated in (rather than freshly
seeded), the `LLDAP_KEY_SEED` value in SOPS must match the seed that
produced the existing argon2 password hashes. Changing the seed renders
every existing hash unverifiable.

### Resource sizing

`authentik-server` and `authentik-worker` each need `*resources-huge` (1024M).
Authentik is a full Django application. `*resources-medium` causes OOMKill;
`*resources-large` is borderline.

### lldap alias on infra_ldap

Cross-stack consumers (Jellyfin) reach lldap via an `aliases: [lldap]` entry on
the `infra_ldap` overlay attachment. Without the alias the natural
fully-qualified name would be `accounts_lldap`, which Django's URL validator
rejects per RFC 1035 (no underscores in hostnames). Authentik's own
`LDAPSource.server_uri` does not need the alias because it reaches lldap
intra-stack via `lldap:389` on the default network.

### Bootstrap admin

`AUTHENTIK_BOOTSTRAP_*` env vars are commented out and only honored on first
startup of a fresh DB. The `10_directory.yaml` blueprint creates `pggm95`
(value of `GLOBAL_USERNAME`) with admin group memberships and deactivates the
default `akadmin` account.
