# Jellyfin Stack

## Access

Dual-domain routing via `deploy.labels`. Two routers share one backend service.

## GPU Passthrough

Intel Arc Pro B50 dGPU passed through (PCI passthrough) to the GPU node. The device nodes are bind-mounted in, and the stock `jellyfin/jellyfin` image's bundled `jellyfin-ffmpeg` (Intel iHD VAAPI driver) handles QSV decode, VPP tone-mapping, and QSV encode with no custom image.

Device access has two parts: the init script grants the host `/dev/dri` GIDs to the non-root worker via `setpriv --groups`, and the [`dmm`](../../infra/70_dmm/README.md) stack grants the cgroup device rule (Swarm cannot pass devices itself).

## Volume Ownership

Container starts as root via the `jellyfin_init` Docker Config (`entrypoint: /bin/sh /init.sh`). The init script chowns the persistent volumes to `${GLOBAL_NONROOT_DOCKER}` and drops privileges with `setpriv` before exec'ing the stock entrypoint.

## LDAP

Jellyfin does not support OIDC. Authentication binds directly to the lldap service in the `accounts` stack over the `infra_ldap` overlay. Install the LDAP Authentication plugin in Jellyfin admin, then configure via the plugin UI (settings live in the plugin's XML in the data volume, not in compose).

Settings that matter:

- **Server** `ldap` on port 389: that alias exists only on `infra_ldap`, so the binds stay on that network.
- **Base DN and admin base DN** `ou=people,<GLOBAL_LDAP_BASE_DN>`. A base at the directory root also searches the group entries, and lldap then logs `Ignoring unknown group attribute "memberof"` for every search.
- **Search filter** `(memberof=cn=<GLOBAL_USER_GROUP>,ou=groups,<GLOBAL_LDAP_BASE_DN>)` and **admin filter** the same with `GLOBAL_ADMIN_GROUP`. Users log in with their uid or their email (search attributes `uid,mail`).
- **"Admin filter is a memberUid filter"** (`EnableLdapAdminFilterMemberUid`) **off**.

The memberUid option makes the plugin grant admin whenever the admin search returns anything; with the group filter above, every login became an admin login. The option also fails open, because the LDAP library counts an error response as a result. With it off, the plugin lists the admins and compares DNs, and a failed search refuses the login.

Password changes made in Jellyfin go through the bind user, a member of `lldap_password_manager`. lldap lets that group change any password except an `lldap_admin` member's, so admins change theirs in lldap.

Jellyfin writes an admin flag the plugin changes at login to the database only for a new user. For an existing user the change stays in the in-memory user cache: the save attaches the user row alone, so the changed permission row is never written. The running server can therefore grant rights the database does not show; the dashboard shows the live state, and a restart reloads it from the database. Restart Jellyfin after correcting admin rights. To inspect the database, copy `jellyfin.db` together with its `-wal` file: opening it `immutable` ignores the WAL and shows stale rows.

## Activity Log Pruning

Jellyfin writes three `ActivityLogs` rows per successful login (`SessionStarted`, `AuthenticationSucceeded`, `SessionEnded`) and exposes no per-user filter or retention policy. Synthetic monitoring (Gatus) that authenticates against Jellyfin therefore floods the activity dashboard.

The `jellyfin-cleanup` sidecar (`alpine/sqlite`) periodically (`CLEANUP_INTERVAL`) issues a single `DELETE FROM ActivityLogs WHERE UserId = ...` against the live database, mounting the same `jellyfin-data` volume. SQLite WAL mode permits one writer alongside the running Jellyfin process, so no downtime is needed. Placement is pinned via `*place-gpu` to share the node with the volume.

Filter is by username (`CLEANUP_USERNAME`), not user UUID, so a deleted and recreated user is still matched. A non-existent username is a graceful no-op.
