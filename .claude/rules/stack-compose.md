---
description: Patterns for writing Docker Swarm stack compose files
# paths:
#   - '**/compose.yml'
---

# Stack Compose Patterns

## Network Assignment

Minimum overlay exposure: overlay networks for cross-stack communication only, stack default network for intra-stack.

| Service needs | Networks |
|---------------|----------|
| Traefik routing only (no intra-stack deps) | overlay only (`infra_gw-internal`, `infra_gw-external`) |
| Traefik routing + talks to other services in same stack | `default` + overlay |
| Intra-stack only (no Traefik labels, no cross-stack) | `default` only |
| Cross-stack access (socket-proxy, metrics scraping) | relevant overlay (`infra_socket`, `infra_metrics`) |

Services that declare `networks:` lose the implicit default. Always add `default` explicitly when intra-stack communication is needed. Never put a service on an overlay just so a sibling service can reach it.

All networks: `external: true`. Keys use actual network name directly (e.g., `infra_socket:`) — no aliases or `name:` indirection.

## Deploy Anchors

Anchors defined in `stacks/_shared/anchors.yml`, resolved by `compose_config()` at preprocessing time.

Each service makes two choices — WHERE (placement) and HOW (behavior):

```yaml
deploy:
  <<: [*place-vm, *deploy]           # normal service on VM
  <<: [*place-storage, *deploy-stop-first]  # database on storage node
  <<: [*place-vm, *deploy-init]      # init sidecar on VM
```

**Placement anchors:**

| Anchor | Constraints |
|--------|-------------|
| `*place-vm` | `location == onprem`, `type == vm` |
| `*place-onprem` | `location == onprem`, `ip == private` |
| `*place-storage` | `location == onprem`, `type == lxc`, `storage == true` |
| `*place-cloud` | `location == cloud`, `ip == public` |
| `*place-gpu` | `location == onprem`, `type == lxc`, `gpu == true` |

Use node labels for placement, never hardcoded node names.

**Deploy behavior anchors:**

| Anchor | Update Order | Use When |
|--------|-------------|----------|
| `*deploy` | `start-first` | Default — zero-downtime rolling updates |
| `*deploy-stop-first` | `stop-first` | Host-mode ports, databases, exclusive-access volumes |
| `*deploy-init` | `stop-first` | Init sidecars that exit after provisioning |

`*deploy-stop-first` inherits restart + rollback from `*deploy` via `<<:`, only overrides `update_config`.

`*deploy-init` uses `condition: on-failure` (exit 0 = done, exit 1 = retry), `failure_action: continue` (sidecar failure doesn't rollback the stack), and `monitor: 0s` (prevents Swarm from misinterpreting a quick exit as a failed update).

## Environment Variable Interpolation

`${VAR}` in compose `environment:` resolves against the **host/mise environment**, not sibling entries. Never reference a value defined in the same `environment:` block.

```yaml
# BUG — OFFLINE_MODE is a container env var, not in mise env
environment:
  - OFFLINE_MODE=true
  - DISABLE_ONLINE_API=${OFFLINE_MODE:-false}  # resolves to "false"

# CORRECT — hardcode directly
environment:
  - DISABLE_ONLINE_API=true
```

## Required on All Services

- `logging: *logging` anchor: `driver: json-file`, `max-size: 10m`, `max-file: 3`
- `stop_grace_period: 30s` (or `60s` for stateful services like databases, caches)

## Volume Patterns

| Type | Pattern | Delivery |
|------|---------|----------|
| Persistent data | `<service>-<purpose>` named volume | Docker volume (auto-prefixed `<stack>_` by Swarm) |
| Config files | `./config/<service>/` | Docker Configs (versioned) |
| Bulk storage | `/mnt/*` | Bind mount |

## Volume Ownership

Services needing non-root access use entrypoint wrappers (Docker Config init scripts) that chown volumes and drop privileges.

| Base Image | Privilege Drop Method |
|------------|----------------------|
| Debian (util-linux) | `setpriv --reuid --regid --clear-groups` |
| Alpine (BusyBox) | `su` — requires `addgroup`/`adduser` first for passwd entry |

**Pattern:**

- Remove `user:` from compose, add `entrypoint: ["/bin/sh", "/init.sh"]`
- Add owner env var (e.g., `JELLYFIN_OWNER: ${GLOBAL_NONROOT_DOCKER}`)
- Script chowns volumes (skip if `.volume-init` marker exists), drops to target UID, `exec`s stock entrypoint
- Use `chown -R ... 2>/dev/null || true` when Docker Configs share a volume mount point
- LinuxServer images with PUID/PGID handle their own permissions — no wrapper needed

## Docker Config Constraints

- **Non-zero size**: Docker rejects 0-byte configs. Use minimal content (YAML `---`, a comment, etc.)
- **Read-only (0444, root)**: Non-root containers can't create sibling files. Provide ALL expected files as configs
- **chown conflicts**: Configs inside a volume dir cause `chown -R` to fail. Use `2>/dev/null || true`
- **No `mode` field**: `docker compose config` serializes mode as octal string, rejected by `docker stack config`. Use `/bin/sh /script.sh` instead

## LXC Node Constraints

Unprivileged LXC cannot use IPVS. Every intra-stack service on LXC that does NOT need Traefik routing must set `endpoint_mode: dnsrr`.

```yaml
services:
  database:
    deploy:
      <<: [*place-storage, *deploy-stop-first]
      endpoint_mode: dnsrr    # required on LXC nodes
```

Services only receiving traffic via Traefik are unaffected (VIP resolution happens on the Traefik node).

## Entrypoint Wrappers

For services needing pre-start initialization:

- Invoke via `/bin/sh /script.sh` (bypasses mode/permission issues)
- Script runs initialization, then chains: `exec /entrypoint.sh "$@"`
- Keep non-fatal: log warnings on failure, still start the service

**`$@` caveat:** When compose sets `entrypoint:` without `command:`, `$@` is empty. Services chaining through a stock entrypoint must hardcode the default command. Services with explicit `command:` in compose are unaffected.
