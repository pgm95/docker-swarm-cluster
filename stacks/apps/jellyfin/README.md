# Jellyfin Stack

## Access

Dual-domain routing via `deploy.labels`. Two routers share one backend service.

## GPU Passthrough

Intel Arc Pro B50 dGPU passed through (PCI passthrough) to the GPU node. The device nodes are bind-mounted in, and the stock `jellyfin/jellyfin` image's bundled `jellyfin-ffmpeg` (Intel iHD VAAPI driver) handles QSV decode, VPP tone-mapping, and QSV encode with no custom image.

Device access has two parts: the init script grants the host `/dev/dri` GIDs to the non-root worker via `setpriv --groups`, and the [`dmm`](../../infra/70_dmm/README.md) stack grants the cgroup device rule (Swarm cannot pass devices itself).

## Volume Ownership

Container starts as root via the `jellyfin_init` Docker Config (`entrypoint: /bin/sh /init.sh`). The init script chowns the persistent volumes to `${GLOBAL_NONROOT_DOCKER}` and drops privileges with `setpriv` before exec'ing the stock entrypoint.

## LDAP

Jellyfin does not support OIDC. Authentication binds directly to the lldap service in the `accounts` stack, reachable cross-stack on the `infra_ldap` overlay. Install the LDAP Authentication plugin in Jellyfin admin, then configure via the plugin UI (settings live in Jellyfin's own DB, not in compose).

## Activity Log Pruning

Jellyfin writes three `ActivityLogs` rows per successful login (`SessionStarted`, `AuthenticationSucceeded`, `SessionEnded`) and exposes no per-user filter or retention policy. Synthetic monitoring (Gatus) that authenticates against Jellyfin therefore floods the activity dashboard.

The `jellyfin-cleanup` sidecar (`alpine/sqlite`) periodically (`CLEANUP_INTERVAL`) issues a single `DELETE FROM ActivityLogs WHERE UserId = ...` against the live database, mounting the same `jellyfin-data` volume. SQLite WAL mode permits one writer alongside the running Jellyfin process, so no downtime is needed. Placement is pinned via `*place-gpu` to share the node with the volume.

Filter is by username (`CLEANUP_USERNAME`), not user UUID, so a deleted and recreated user is still matched. A non-existent username is a graceful no-op.
