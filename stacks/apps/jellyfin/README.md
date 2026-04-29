# Jellyfin Stack

## Access

Dual-domain routing via `deploy.labels`. Two routers share one backend service.

## GPU Passthrough

The GPU worker runs Docker with `default-runtime: amd` (`amd-container-runtime` + CDI). This service opts in with `AMD_VISIBLE_DEVICES=all` and pins placement via `*place-gpu` from `stacks/_shared/anchors.yml`. The runtime injects `/dev/dri/*` and `/dev/kfd` with correct ownership at container create. No `/dev/dri` bind mount, no `generic_resources`, no `devices:`.

The CDI spec at `/etc/cdi/amd.json` (on the GPU worker) encodes explicit device major+minor. Regenerate after any change in DRM enumeration (for example, attaching or removing an HDMI iKVM can shift `card0` to `card1`):

```bash
amd-ctk cdi generate --output=/etc/cdi/amd.json
```

## Custom Image

The base `jellyfin/jellyfin` image ships Mesa drivers that predate RDNA 3.5 (gfx1150) support. The Dockerfile at `build/jellyfin/Dockerfile` adds a Debian backports layer to enable hardware transcoding on this silicon:

| Package | Purpose |
|---|---|
| `mesa-va-drivers` | VAAPI decode/encode (radeonsi) with gfx1150 |
| `mesa-vulkan-drivers` | Vulkan (RADV) with gfx1150 |
| `mesa-opencl-icd` | OpenCL 3.0 via Rusticl |
| `libva2`, `libdrm2`, `libdrm-amdgpu1` | Matching userspace libs |
| latest `libllvm*` | Shader compiler backend |

After install, the Dockerfile replaces Jellyfin's bundled `radeonsi_drv_video.so` with Mesa's current gallium library. Content-hash tagging in `swarm:deploy` promotes any Dockerfile change to a fresh image tag automatically.

`RUSTICL_ENABLE=radeonsi` is required in the container env. Rusticl activates no Gallium drivers by default, so without this env var OpenCL sees no devices.

## Volume Ownership

Container starts as root via the `jellyfin_init` Docker Config (`entrypoint: /bin/sh /init.sh`). The init script chowns the persistent volumes to `${GLOBAL_NONROOT_DOCKER}` and drops privileges before exec'ing the stock entrypoint. See `.claude/rules/stack-compose.md` for the general pattern.

## HDR Tone Mapping **DISABLED**

Enabling tonemapping causes ffmpeg to insert a `libplacebo` Vulkan compute filter into the transcode graph, which deadlocks the AMD MES firmware on gfx1150 and hangs the Proxmox host within minutes. Every algorithm in the tone-mapping dropdown (BT.2390, Hable, Reinhard, etc.) routes through libplacebo on AMD VAAPI, so changing the algorithm does not help. "Enable VPP Tone mapping" is Intel-only and has no effect here.

Revisit when `gc_11_5_2_mes*.bin` blobs change in a future `pve-firmware` release.

## LDAP

Jellyfin does not support OIDC. Authentication binds directly to the lldap service in the `accounts` stack, reachable cross-stack on the `infra_ldap` overlay. Install the LDAP Authentication plugin in Jellyfin admin, then configure via the plugin UI (settings live in Jellyfin's own DB, not in compose).

## Activity Log Pruning

Jellyfin writes three `ActivityLogs` rows per successful login (`SessionStarted`, `AuthenticationSucceeded`, `SessionEnded`) and exposes no per-user filter or retention policy. Synthetic monitoring (Gatus) that authenticates against Jellyfin therefore floods the activity dashboard.

The `jellyfin-cleanup` sidecar (`alpine/sqlite`) periodically (`CLEANUP_INTERVAL`) issues a single `DELETE FROM ActivityLogs WHERE UserId = ...` against the live database, mounting the same `jellyfin-data` volume. SQLite WAL mode permits one writer alongside the running Jellyfin process, so no downtime is needed. Placement is pinned via `*place-gpu` to share the node with the volume.

Filter is by username (`CLEANUP_USERNAME`), not user UUID, so a deleted and recreated user is still matched. A non-existent username is a graceful no-op.
