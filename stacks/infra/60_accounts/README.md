# Accounts Stack

Identity, authentication, OIDC provider and forward auth for the cluster. Authentik
(server + worker) plus an embedded lldap directory it consumes as an LDAP Source. Init
sidecars provision the Postgres roles/databases and seed the lldap groups and service
accounts.

## Operating Model

The lldap database is the only state in this stack that cannot be replaced: people,
their password hashes, avatars and group memberships. Everything else is declared in
this repo. Authentik's database is disposable: purging it and restarting rebuilds every
application, provider, policy, mapping and the admin from the blueprints and the LDAP
sync (see [Purge Runbook](#purge-runbook)).

lldap stays the source of truth rather than Authentik because Jellyfin binds to it
directly. Authentik's LDAP outpost is read-only (no password change from Jellyfin), has
no avatar attribute, and would tie Jellyfin logins to Authentik's availability.

Rebuilding lldap from a user export changes every `entryUUID`, which Authentik uses to
match users and groups, so it always goes together with an Authentik purge. Existing
password hashes only stay valid with the same `ACCOUNTS_LLDAP_KEY_SEED`.

## LDAP Source

Authentik consumes lldap as an `LDAPSource` (see `25_ldap-source.yaml`). A full sync
runs every 5 minutes and whenever the source is saved; the schedule is declared in the
same blueprint because Authentik only sets a schedule's crontab when it first creates it
(default every 2 hours). An hourly connectivity check only tests the connection.

The bind identity is `uid=_bind_authentik,ou=people,${GLOBAL_LDAP_BASE_DN}`,
seeded by `init-ldap` into `lldap_password_manager`. Authentik's worker reaches
lldap at `ldap:389` over `infra_ldap`, like every other LDAP consumer (see
[LDAP on infra_ldap](#ldap-on-infra_ldap)).

Service accounts (the bind users and the Gatus test user) are members of the
`lldap_service` group, and the source's user filter skips them, so they never become
Authentik users.
The filter goes by group because lldap ignores substring filters such as `(uid=_*)`.

### What syncs which way

| Change | Direction | When |
|---|---|---|
| User / group create, delete, attribute edit in lldap | lldap to Authentik | scheduled sync (every 5 minutes) or `LDAPSource.save()` |
| Password change in lldap | lldap to Authentik (per user) | next successful Authentik web login by that user; cached hash rewritten via bind delegation |
| Password change in Authentik UI | Authentik to lldap | instant RFC 3062 modify; rejected for `lldap_admin` members |
| Anything else edited in Authentik UI on a sourced user / group (name, email, avatar, membership, deletion) | not propagated | Authentik DB only; clobbered on next sync if the field is mapped from lldap |

Operating model: edit identities (users, groups, memberships, avatars) in lldap;
declare Authentik-only resources (apps, providers, policies) in the blueprints. Nothing
is created in the Authentik UI, because a purge would lose it.

### Authentication paths

Three paths exist; they interact subtly.

1. **Pull sync (lldap to Authentik)**: the worker iterates the person entries
   and `(objectClass=groupOfUniqueNames)` under `ou=people` / `ou=groups`,
   matching by `entryUUID`. Property mappings populate `name`, `email`,
   `attributes.givenName`, `attributes.sn`, and `attributes.avatar` (as a
   `data:image/jpeg;base64,...` URL inline in JSON). lldap does not expose
   `userPassword` over LDAP, so sync never touches passwords.
2. **Bind delegation on Authentik web login**: `LDAPBackend` opens a fresh
   ldap3 bind to `ldap:389` as the user's DN with their candidate password.
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

Practical consequence: a user or group that already exists in Authentik under the same
name but without `attributes.ldap_uniq` collides on the unique name at sync time and
logs a `configuration_error` event. Blueprints therefore never create users or groups
that also come from lldap.

### Writeback limitation: admin

`_bind_authentik` is in `lldap_password_manager`. lldap's permission model
rejects password modifications targeting users in `lldap_admin`, so Authentik cannot
write an admin's password back. The lldap admin's password comes from
`GLOBAL_ADMIN_PASSWORD` and is reset on every lldap start
(`force_ldap_user_pass_reset`); change it in SOPS, not in either UI.

### Stale-hash window

The cached hash is rewritten only on a successful Authentik web login with the
new password. Between an lldap-side password change and the next Authentik
login by that user, the OLD password still works against Authentik because
Django's `InbuiltBackend` is tried before `LDAPBackend` and the cache is not
invalidated. Bounded; mitigated by routing password changes through Authentik
when possible.

## Admins

Membership of lldap's `lldap_admin` group is what makes an Authentik superuser: the
group mapping sets `is_superuser` on the synced `lldap_admin` group, and every sync
reapplies it. There is no Authentik-side admin user or group. lldap creates its admin
(`GLOBAL_ADMIN_USER`) in `lldap_admin` on first start, and `init-ldap` declares the
admin's other memberships (`GLOBAL_ADMIN_GROUP`, `GLOBAL_USER_GROUP`).

The admin's profile is owned by the repo, like its password. lldap's bootstrap script
sends every profile field a user config does not declare as an empty string, and lldap
clears that field, so `init-ldap` declares the admin's email and display name and every
deploy resets them. Undeclared fields (avatar, first and last name) are cleared on every
deploy; edit none of them in the lldap UI.

A fresh database is marked as set up through `AUTHENTIK_BOOTSTRAP_PASSWORD_HASH` (the hash
of a discarded random password); otherwise Authentik 2026.8 sends every visit to its
initial setup flow. Finishing that flow by hand would also make the flow require a
superuser and disable its blueprint; the bootstrap path only sets the flag. With the flag
set the flow refuses anonymous use, so that extra lock is deliberately skipped.

Authentik's own bootstrap admin `akadmin` is kept disabled by `10_directory.yaml`. If
lldap or the sync is unavailable, recover access with
`docker exec <authentik-worker> ak create_recovery_key 10 <username>`.

## Application Access and Identity

Every application is bound to one group, `GLOBAL_USER_GROUP` or `GLOBAL_ADMIN_GROUP`
(see the blueprints); a user outside that group neither sees the application nor can
obtain a token for it. Every OIDC provider sends the lldap username as `sub`
(`sub_mode: user_username`), which survives an Authentik purge where the default hashed
user id would not. lldap usernames are lowercased and cannot be renamed.

Offboard a person by removing them from `GLOBAL_USER_GROUP` and `GLOBAL_ADMIN_GROUP`.
Never delete a user and never reuse a username: a new user with the same name would
inherit the old one's accounts in every app.

## API Service Accounts

Consumers of the Authentik API are declared in `40_service-accounts.yaml`, one section
each: an RBAC role with the permissions the consumer needs, a `service_account` user
holding that role, and a non-expiring API token. The token key is a pairing secret read
with `!File`, and the consumer mounts the same secret, so the value survives a purge.
These accounts live in Authentik only; lldap service accounts (`lldap_service`) are a
different thing and are never synced. The `gatus` account backs a Gatus check that alerts
when any blueprint reports an error, which Authentik otherwise only shows in its UI.

## Avatars

Property mapping `lldap-user-avatar` reads the raw `jpegPhoto` bytes from
lldap, base64-encodes them, and stores `attributes.avatar = data:image/jpeg;base64,<...>`
on the User row. Frontend renders the value verbatim as `<img src=...>`.

The resolution chain comes from `AUTHENTIK_AVATARS` on server and worker. Authentik
reads it only in its initial migration, when a fresh database seeds the tenant row;
afterwards the row is authoritative, and the `authentik_tenants.tenant` model is not
blueprintable (still true in 2026.8). A purge applies it again.

## WebFinger bare-domain redirect

WebFinger requires `/.well-known/webfinger` on the bare domain
(`DOMAIN_PUBLIC`), not on `auth.DOMAIN_PUBLIC`. The Traefik router that redirects it to
the auth subdomain is commented out in compose; it is only needed once, when a client
(Tailscale) discovers the issuer.

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
| `30_` | providers | scope-mappings (custom scopes), synced user and admin groups (application policy bindings) |
| `35_` | proxy-providers | synced user group (application policy bindings) |
| `40_` | service-accounts | none |
| `45_` | links (applications without a provider, library entries only) | synced user group (application policy bindings) |
| `50_` | brand | providers (Tailscale app for WebFinger) |

Authentik's `blueprints_find()` uses `rglob("**/*.yaml")`. Files with `.yml`
extension are silently ignored.

On a fresh database `30_providers` and `35_proxy-providers` usually fail their first
apply: either they run before the first LDAP sync has created the groups their
bindings look up, or two blueprints applying at once hit a Postgres deadlock. A failed
apply is not retried; the hourly discovery picks them up again because their hash was
never recorded. To converge at once, trigger discovery after the first sync (see
[Purge Runbook](#purge-runbook)).

A deploy does not reliably apply a changed blueprint either. The worker updates
`start-first`, and the discovery the new worker queues at startup can be taken by the
outgoing one, which still mounts the old files and finds nothing to apply; the change then
waits for the hourly run. After deploying a blueprint change, run `mise run
accounts:discover`: it waits until one worker is left, triggers discovery there and
fails listing every blueprint that is not applied.

The deadlock can also hit a blueprint that has already applied, when a second apply of it
runs concurrently (seen on Authentik's own `default/flow-oobe.yaml`). Its hash is then
recorded, so discovery never retries it and it stays in error until it is reapplied by
hand (runbook step 6).

## Forward auth

Apps without native OIDC support or weak auth sit behind the embedded outpost in single
application mode. Each needs three pieces:

- Proxy provider and application entries in `35_proxy-providers.yaml`, and the provider
  added to the embedded outpost. That entry replaces the outpost's provider list and its
  config on every apply, so a provider or setting changed in the UI is dropped.
- The `authentik@file` middleware on the app's router (defined by the external gateway).
- A second router on the app's host for `PathPrefix(/outpost.goauthentik.io/)` pointing
  at `authentik@swarm`, which serves the login callback.

A backend that also wants HTTP basic auth gets it from the provider's basic auth
option: the outpost sends an `Authorization` header built from two keys of
`ak_proxy.user_attributes`, and the middleware forwards that header. The keys come
from a scope mapping attached to that provider alone, with the password read from a
pairing secret by `!File`. They are never set as group attributes: the synced groups
carry `ldap_uniq`, and a blueprint write replaces the whole attribute dict, so the next
sync would no longer match the group and would try to create a duplicate.

Every forward auth provider needs a scope mapping of its own that blanks `avatar` (for
a basic auth provider, the credentials mapping above). The default proxy mapping copies
every user attribute into the tokens, and an inline lldap photo can run to hundreds of
kilobytes. The outpost reads at most 1 MiB of the token response, so a large photo
truncates it: the code redemption fails and the login loops between the app and
Authentik. A backend behind nginx would also reject the resulting identity header (8 KB
header limit). The provider's own mapping runs after the default one (scope name order)
and its empty value wins.

## Brand entry

The default brand entry in `50_brand.yaml` requires `domain: authentik-default`
explicitly. Authentik 2026.2's brand serializer rejects entries without it
even when matched by `default: true` identifier.

## Secret delivery

Four subsystems read secrets in four different ways. Every value is defined
once and mounted as a versioned secret; the consumers differ only in how they
address the mounted file.

| Mode | Consumed by | Compose form |
|---|---|---|
| Python config loader (`file://`) | Select `AUTHENTIK_*` keys (secret key, Postgres password, SMTP password) | `KEY=file:///run/secrets/<name>` plus Docker secret mount |
| lldap `_FILE` suffix | Every lldap credential (JWT secret, key seed, admin password, database URL, SMTP password) | `LLDAP_*_FILE=/run/secrets/<name>` plus Docker secret mount |
| Blueprint `!File` tag | Worker at blueprint apply time (pairing secrets: client secrets, bind password, the Firefox basic auth password, service account tokens) | Docker secret mount read at YAML parse time |
| Init scripts | `init-db` and `init-ldap` | read `/run/secrets/<name>` directly |

`GLOBAL_ADMIN_PASSWORD` feeds the lldap admin through `LLDAP_LDAP_USER_PASS_FILE` and
the lldap bootstrap through `LLDAP_ADMIN_PASSWORD_FILE` (init-ldap). Authentik never
reads it. The lldap database password exists once, inside `ACCOUNTS_LLDAP_DB_URL`;
`init-db` extracts it from there.

## Purge Runbook

Purging rebuilds Authentik from the repo and lldap. Sessions, event history, the OIDC
signing key and every MFA device are lost; users log in again and re-enroll MFA.

1. Dump the database: `pg_dump -Fc authentik` inside the central Postgres container.
2. Remove the stack (`swarm:remove accounts`).
3. Drop it: `DROP DATABASE authentik WITH (FORCE);`
4. Redeploy the stack (`swarm:deploy accounts`). `init-db` recreates the database.
5. Once the first LDAP sync has run (the admin appears under Directory, Users), trigger
   blueprint discovery so the blueprints that failed on the first pass apply now instead
   of within the hour: `mise run accounts:discover`. It lists what is still pending.
6. Reapply every blueprint still in error, Authentik's default blueprints included.
   Discovery only reapplies a blueprint whose hash was never recorded; one that applied
   and then lost a deadlock in a second, concurrent apply stays in error for good:
   `docker exec -i <authentik-worker> ak shell -c "from authentik.blueprints.models import BlueprintInstance; from authentik.blueprints.v1.tasks import apply_blueprint; [apply_blueprint.send(b.pk) for b in BlueprintInstance.objects.filter(status='error')]"`
7. Check that every blueprint reports successful, and that the Gatus check
   "Authentik Blueprints" is green.

A fresh environment (both databases empty) follows the same steps from step 4.

Roll back with `pg_restore` of the dump, deployed with the Authentik image that wrote it:
Authentik refuses to start on a database from an older major version it cannot upgrade
from directly.

## Operational notes

### Init order and convergence

`init-db` provisions both the `authentik` and `lldap` Postgres roles and
databases. lldap and the Authentik tasks may crash-loop briefly on a fresh
deploy until init-db completes; restart policy handles recovery without
intervention. `init-ldap` reaches lldap on `http://lldap:17170` and waits
internally before bootstrapping the groups, service accounts and admin memberships.
It never removes a membership it does not declare; family memberships are lldap state.

### Fresh database settings (Authentik 2026.8)

Three settings get a value on an upgraded database but start empty on a fresh one, so
they are declared:

- the `setup` flag, through `AUTHENTIK_BOOTSTRAP_PASSWORD_HASH` (see [Admins](#admins)).
  An upgrade migration sets it on existing installs;
- every OIDC provider's `grant_types` in `30_providers.yaml`. An upgrade migration grants
  existing providers every type; a provider created without it allows no grant at all
  and refuses every login;
- the tenant's base URL (the instance's public URL), through `AUTHENTIK_WEB__BASE_URL`.
  No migration sets it: at every start Authentik fills an empty value from this variable,
  or failing that from the embedded outpost's host. On a fresh database the outpost host
  is only set later by `35_proxy-providers.yaml`, so without the variable the first start
  leaves it empty and logs a `configuration_warning` event until a later start copies it.

### LDAP_KEY_SEED preservation

If lldap's Postgres database is being migrated in (rather than freshly
seeded), the `ACCOUNTS_LLDAP_KEY_SEED` value in SOPS must match the seed that
produced the existing argon2 password hashes. Changing the seed renders
every existing hash unverifiable.

### Resource sizing

`authentik-server` and `authentik-worker` each need at least `*resources-huge` (1024M).
Authentik is a full Django application. `*resources-medium` causes OOMKill;
`*resources-large` is borderline. The server runs with a 2048M limit: at 1024M it peaked
at the limit and was OOM-killed once.

### LDAP on infra_ldap

LDAP consumers (Authentik's server for password logins and writeback, its worker for
the sync, and Jellyfin) join `infra_ldap` and connect to
`ldap:389`, an alias lldap carries on that network only, so their binds never cross the
shared networks lldap also joins; the automatic `lldap` alias exists on all of them.
lldap itself still listens on every network: Swarm has no per-network access control or
static IPs, and a task cannot resolve its own alias while starting, so binding LDAP to
`ldap` fails. Hostnames must not contain underscores, which Authentik's
`server_uri` validation rejects.
