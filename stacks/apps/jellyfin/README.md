# Jellyfin Stack

## Access

Dual-domain routing via `deploy.labels`. Two routers (`jellyfin-external`, `jellyfin-internal`) share one backend service.

## GPU Passthrough

The GPU worker runs Docker with `default-runtime: amd` (`amd-container-runtime` + CDI). This service opts in with `AMD_VISIBLE_DEVICES=all` and pins placement via `*place-gpu` from `stacks/_shared/anchors.yml`. The runtime injects `/dev/dri/*` and `/dev/kfd` with correct ownership at container create. No `/dev/dri` bind mount, no `generic_resources`, no `devices:`.

The CDI spec at `/etc/cdi/amd.json` (on the GPU worker) encodes explicit device major+minor. Regenerate after any change in DRM enumeration (for example, attaching or removing an HDMI iKVM can shift `card0` to `card1`):

```
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

## HDR Tone Mapping Disabled

"Enable Tone mapping" in the Jellyfin admin dashboard is **off**. Leave it off.

Enabling it causes ffmpeg to insert a `libplacebo` Vulkan compute filter into the transcode graph, which deadlocks the AMD MES firmware on gfx1150 and hangs the Proxmox host within minutes. Every algorithm in the tone-mapping dropdown (BT.2390, Hable, Reinhard, etc.) routes through libplacebo on AMD VAAPI, so changing the algorithm does not help. "Enable VPP Tone mapping" is Intel-only and has no effect here.

Practical consequences:

- SDR content: unaffected.
- HDR direct-play to HDR-capable clients: unaffected.
- HDR transcoded to SDR: plays, but colors are washed out because the HDR dynamic range is not compressed into SDR. Acceptable in practice since HDR transcoded to SDR is rare. Generate SDR variants offline for any titles where it matters.

Full investigation and reproducer in `.local/gpu-kernel/REPORT.md` (local only, not committed). Revisit when `gc_11_5_2_mes*.bin` blobs change in a future `pve-firmware` release.

## LDAP

Jellyfin does not support OIDC. Authentication binds directly to the lldap service in the `accounts` stack, reachable cross-stack on the `infra_ldap` overlay. Install the LDAP Authentication plugin in Jellyfin admin, then configure via the plugin UI (settings live in Jellyfin's own DB, not in compose).

### Connection

| Setting | Value |
|---|---|
| LDAP Server | `lldap` (alias on `infra_ldap` overlay) |
| LDAP Port | `389` |
| Secure LDAP | unchecked (Tailscale encrypts the underlay) |
| LDAP Bind User | `uid=_bind_jellyfin,ou=people,GLOBAL_LDAP_BASE_DN` |
| LDAP Bind Password | from accounts stack `secrets.env` (`JELLYFIN_BIND_PASS`); seeded by `init-ldap` into `lldap_password_manager` |
| LDAP Base DN | `GLOBAL_LDAP_BASE_DN` |

### Users

| Setting | Value |
|---|---|
| LDAP Search Filter | `(objectClass=person)` |
| LDAP Search Attributes | `uid, cn, mail, displayName` |
| LDAP Uid Attribute | `uid` |
| LDAP Username Attribute | `uid` |
| LDAP Password Attribute | *(empty)* |
| Enable case insensitive username | checked |
| Enable user creation | checked |
| Allow password change | checked (lldap accepts RFC 3062 modify from the bind user) |
| Password Reset URL | `https://lldap.DOMAIN_PRIVATE` |

### Administrators

| Setting | Value |
|---|---|
| LDAP Admin Base DN | `GLOBAL_LDAP_BASE_DN` |
| LDAP Admin Filter | `(memberOf=cn=app_admin,ou=groups,GLOBAL_LDAP_BASE_DN)` |
| Enable Admin Filter memberUid mode | unchecked |

`GLOBAL_LDAP_BASE_DN` and `DOMAIN_PRIVATE` vary by environment.
