# Jellyfin Stack

## Access

Dual-domain routing via `deploy.labels` (two routers: `jellyfin-external` and `jellyfin-internal`).

## GPU Passthrough

Docker Swarm does not support `devices:` in service specs (SwarmKit issue #1244, open since 2016, unresolved). Without `--device`, containers lack the cgroup device whitelist needed to access `/dev/dri/*` character devices (major 226). Bind-mounting `/dev/dri:/dev/dri` creates the mount point but the kernel's cgroup device controller blocks access — the device files appear as "No such file or directory" inside the container.

### What Doesn't Work

| Approach | Why It Fails |
|----------|-------------|
| `devices:` in compose | Stripped by `docker stack deploy` — not supported in Swarm |
| `device-cgroup-rules` in daemon.json | Not a valid daemon.json option — Docker rejects it and fails to start |
| `--default-cgroup-rule` dockerd flag | Does not exist in Docker Engine |
| CDI (Container Device Interface) | Works with `docker run` only — Swarm has no mechanism to pass CDI device requests |
| `privileged: true` | Not supported in Swarm stack files |
| AMD Container Runtime Toolkit | Targets Instinct datacenter GPUs with ROCm — incompatible with consumer RDNA APUs |

### Host Node Setup

**1. udev rule** — persistent device permissions across reboots:

```
/etc/udev/rules.d/99-dri.rules

KERNEL=="card[0-9]*", SUBSYSTEM=="drm", MODE="0666"
KERNEL=="renderD*", SUBSYSTEM=="drm", MODE="0666"
```

Apply without reboot: `udevadm trigger`

**2. daemon.json** — GPU resource advertising for Swarm scheduler:

```json
{
  "node-generic-resources": ["gpu=1"]
}
```

No custom runtime needed. No Docker restart required for the udev change.

### How It Works

The real blocker is the cgroup device controller, not the bind mount. Docker's `--device` flag does two things: creates the device node AND adds a cgroup allow rule. Swarm's bind mount only does the first. The udev rule sidesteps the second by making the devices world-accessible at the OS level, before Docker's cgroup layer gets involved.

- udev rule: Makes `/dev/dri/*` world-accessible (`0666`), bypassing cgroup device restrictions
- `/dev/dri:/dev/dri` bind mount in compose: Exposes device nodes inside the container
- `node-generic-resources`: Advertises GPU availability to Swarm scheduler
- `generic_resources` in compose: Requests GPU from scheduler, prevents oversubscription
- Placement constraint `gpu == true`: Ensures service runs on GPU node

### Security

`MODE="0666"` grants all containers on the GPU node access to `/dev/dri`. Acceptable here because only jellyfin is constrained to the GPU node. If other services are added to that node, evaluate whether GPU access should be restricted.

## Custom Image

The base `jellyfin/jellyfin` image ships Mesa drivers that predate RDNA 3.5 (gfx1150) support. The Dockerfile installs newer drivers from Debian backports to enable hardware transcoding on the Radeon 890M.

### What It Installs

| Package | Purpose |
|---------|---------|
| `mesa-va-drivers` | VAAPI decode/encode (radeonsi) |
| `mesa-vulkan-drivers` | Vulkan video decode/encode (RADV) |
| `mesa-opencl-icd` | Rusticl OpenCL 3.0 (GPU-accelerated HDR tone mapping) |
| `libva2`, `libva-drm2` | VA-API runtime libraries |
| `libdrm2`, `libdrm-amdgpu1` | DRM/amdgpu kernel interface |
| `libllvm*` (newest available) | LLVM backend for shader compilation |

After installing, the Dockerfile replaces Jellyfin's bundled `radeonsi_drv_video.so` with the newer Gallium library from Mesa backports. Content-hash tagging via `swarm:deploy` — Dockerfile changes automatically produce new image tags.

### Why Rusticl

Mesa 25.2 removed the legacy Clover OpenCL backend. `mesa-opencl-icd` now contains only Rusticl (`libRusticlOpenCL.so`), providing OpenCL 3.0 with image support on radeonsi. Required for `tonemap_opencl` in jellyfin-ffmpeg.

Rusticl requires `RUSTICL_ENABLE=radeonsi` in the container environment to advertise the GPU as an OpenCL device. Without it, no OpenCL platform is registered and ffmpeg's OpenCL init fails.

## Hardware Acceleration

### GPU Hardware

AMD Ryzen AI 9 HX PRO 370 (Strix Point) with Radeon 890M iGPU (RDNA 3.5, gfx1150).

### Driver Stack

| Layer | Driver | Version |
|-------|--------|---------|
| DRM | amdgpu | 3.57.0 |
| VAAPI | Mesa Gallium (radeonsi) | 25.2.6 |
| Vulkan | RADV PHOENIX | 25.2.6 |
| OpenCL | Rusticl (radeonsi) | OpenCL 3.0 |
| LLVM | — | 19.1.7 |

### Codec Support

| Codec | VAAPI Decode | VAAPI Encode |
|-------|:------------:|:------------:|
| H.264 (High) | Yes | Yes |
| HEVC (Main) | Yes | Yes |
| HEVC (Main 10) | Yes | Yes |
| AV1 (Main) | Yes | Yes |
| VP9 (Profile 0) | Yes | No |
| VP9 (Profile 2) | Yes | No |
| JPEG Baseline | Yes | No |

### GPU Processing Filters

| Filter | Status |
|--------|--------|
| `scale_vaapi` (resize on GPU) | Works |
| `deinterlace_vaapi` | Works |
| `overlay_vaapi` | Works |
| `tonemap_opencl` (HDR → SDR) | Works (via Rusticl) |
| `tonemap_vaapi` (HDR → SDR) | Fails — Mesa radeonsi VPP lacks HDR support |

### HDR Tone Mapping

`tonemap_vaapi` is not available — Mesa's radeonsi VAAPI driver does not expose VPP HDR capabilities. Two working alternatives:

**1. `tonemap_opencl` via Rusticl (preferred)** — tone mapping kernel runs on GPU:

```
VAAPI decode → hwdownload → hwupload (OpenCL) → tonemap_opencl → hwdownload → hwupload_vaapi → VAAPI encode
```

**2. `tonemapx` CPU fallback** — SIMD-optimized software tone mapping (AVX2+FMA3):

```
VAAPI decode → hwdownload → tonemapx (CPU) → hwupload → VAAPI encode
```

Both require a round-trip through system memory because Rusticl lacks VA-API interop (`cl_intel_va_api_media_sharing` is Intel-specific, and DRM-to-OpenCL device derivation is not implemented in Rusticl). Zero-copy VAAPI ↔ OpenCL mapping is not possible.

### Vulkan Video

Vulkan video extensions are available (decode: H.264, H.265, AV1; encode: H.264, H.265) with dedicated decode/encode queue families. Not currently used by Jellyfin's transcoding pipeline but available for future ffmpeg Vulkan video support.

### What Doesn't Exist

| Feature | Why |
|---------|-----|
| VP9 VAAPI encode | No `EncSlice` entrypoint in radeonsi — hardware limitation |
| JPEG VAAPI encode | No encode entrypoint for JPEG Baseline |
| `tonemap_vulkan` filter | Does not exist as an ffmpeg filter |
| OpenCL zero-copy interop | Rusticl lacks VA-API/DRM device derivation for ffmpeg |

## LDAP Setup

Jellyfin does not support OIDC. Authentication is via LDAP against the Authentik LDAP outpost
over the `infra_ldap` overlay. The outpost uses `endpoint_mode: dnsrr` for LXC compatibility.

Install the **LDAP Authentication** plugin in Jellyfin admin, then configure:

### Connection

| Setting | Value |
|---------|-------|
| LDAP Server | `accounts_ldap-outpost` |
| LDAP Port | `3389` |
| Secure LDAP | Unchecked (Tailscale encrypts the overlay) |
| LDAP Bind User | `cn=ldapservice,ou=users,GLOBAL_LDAP_BASE_DN` |
| LDAP Bind Password | From `secrets.env` (`AUTHENTIK_LDAP_BIND_PASSWORD`) |
| LDAP Base DN | `GLOBAL_LDAP_BASE_DN` |

### Users

| Setting | Value |
|---------|-------|
| LDAP Search Filter | `(objectClass=user)` |
| LDAP Search Attributes | `uid, cn, mail, displayName` |
| LDAP Uid Attribute | `uid` |
| LDAP Username Attribute | `cn` |
| LDAP Password Attribute | *(empty)* |
| Enable case insensitive username | Checked |
| Enable user creation | Checked |
| Allow password change | Unchecked |
| Password Reset URL | `https://auth.DOMAIN_PUBLIC/if/user/#/settings` |

### Administrators

| Setting | Value |
|---------|-------|
| LDAP Admin Base DN | `GLOBAL_LDAP_BASE_DN` |
| LDAP Admin Filter | `(memberOf=cn=app_admin,ou=groups,GLOBAL_LDAP_BASE_DN)` |
| Enable Admin Filter memberUid mode | Unchecked |

### Authentik LDAP Limitations

Authentik's LDAP outpost is read-only. Password changes and profile image sync are not supported
via LDAP. Users change passwords through the Authentik web UI (password reset URL above).
Profile images must be managed in Authentik directly.

`GLOBAL_LDAP_BASE_DN` and `DOMAIN_PUBLIC` vary by environment. Configuration is stored in
Jellyfin's database (set once via plugin UI, not in compose).
