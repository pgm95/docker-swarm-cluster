---
description: Patterns for writing Docker Swarm stack compose files
# paths:
#   - '**/compose.yml'
---

# Stack Compose Patterns

## Network Assignment

Use the **minimum overlay exposure** principle: overlay networks for cross-stack communication only, stack default network for intra-stack communication.

| Service needs | Networks |
|---------------|----------|
| Traefik routing only (no intra-stack deps) | overlay only (`infra_gw-internal`, `infra_gw-external`) |
| Traefik routing + talks to other services in same stack | `default` + overlay |
| Intra-stack only (no Traefik labels, no cross-stack) | `default` only |
| Cross-stack access (socket-proxy, metrics scraping) | relevant overlay (`infra_socket`, `infra_metrics`) |

Services that explicitly declare `networks:` lose the implicit default network. Always add `default` explicitly when intra-stack communication is needed. Never put a service on an overlay just so a sibling service can reach it.

All networks must be declared with `external: true`. Keys use the actual network name directly (e.g., `infra_socket:`) — no aliases or `name:` indirection.

## Deploy Anchors

All `x-logging`, `x-place-*`, `x-deploy*`, and `x-resources-*` anchors are defined once in `stacks/_shared/anchors.yml`. The `compose_config()` function (sourced from `.mise/tasks/scripts/compose-config.sh`) concatenates this file with each `compose.yml` before `docker compose config` processes it, so anchors resolve across the boundary. Compose files reference anchors (`*logging`, `*place-vm`, `*deploy`, etc.) without defining them.

Each service makes two explicit choices — WHERE (placement) and HOW (behavior):

```yaml
deploy:
  <<: [*place-vm, *deploy]           # normal service on VM
  <<: [*place-storage, *deploy-stop-first]  # database on storage node
```

**Placement anchors** (node targeting only):

| Anchor | Constraint |
|--------|------------|
| `*place-vm` | `location == onprem`, `type == vm` |
| `*place-onprem` | `location == onprem` |
| `*place-storage` | `storage == true` |
| `*place-cloud` | `location == cloud`, `ip == public` |
| `*place-gpu` | `gpu == true` |

Use node labels for placement (`location`, `ip`, `gpu`, `storage`), never hardcoded node names.

**Deploy behavior anchors** (restart, update, rollback):

| Anchor | Update Order | Use When |
|--------|-------------|----------|
| `*deploy` | `start-first` | Default — zero-downtime rolling updates |
| `*deploy-stop-first` | `stop-first` | Host-mode ports, databases, exclusive-access volumes |

`*deploy-stop-first` inherits restart + rollback from `*deploy` via `<<:`, only overrides `update_config`.

**When to use `*deploy-stop-first`:** Services with host-mode port bindings (80, 443) or named volumes requiring exclusive access (databases, any service writing to a data directory). `start-first` launches a new task before stopping the old one — two containers mount the same volume simultaneously, causing data corruption. Symptom for Postgres: `PANIC: could not locate a valid checkpoint record`. For host-mode ports: stuck pending tasks with "host-mode port already in use".

**Restart exhaustion with cross-stack dependencies:** The shared deploy anchors use `max_attempts: 3` with `window: 120s`. Services that fatally validate external dependencies at startup (OIDC providers, databases in other stacks, external APIs) will permanently stall if those dependencies aren't ready within the retry window. This commonly happens during initial `site:deploy` when app stacks start before infra stacks fully converge. Fix: `docker service update --force <service>` after dependencies are healthy. Do not increase `max_attempts` to mask the issue — the limit exists to prevent infinite crash loops.

## Required on All Services

- `x-logging` anchor: `driver: json-file`, `max-size: 10m`, `max-file: 3`
- `stop_grace_period: 30s` (or `60s` for stateful services like databases, caches)

## Volume Patterns

| Type | Pattern | Delivery |
|------|---------|----------|
| Persistent data | `<service>-<purpose>` named volume | Docker volume (auto-prefixed `<stack>_` by Swarm) |
| Config files | `./config/<service>/` | Docker Configs (versioned) |
| Bulk storage | `/mnt/*` | Bind mount |

**Volume initialization caveat:** Docker copies image-layer permissions into empty named volumes on first mount. `swarm:init-volumes` creates a `.volume-init` marker file after chowning to prevent this. Only affects services using `user:` directive — LinuxServer images with PUID/PGID handle their own permissions.

**Bind mount paths must pre-exist:** Swarm rejects tasks immediately (`Rejected` state) when bind mount source paths don't exist on the target node. Unlike Docker Compose, Swarm does not auto-create missing directories. Ensure paths exist before deploying — `swarm:validate` catches this but only as a warning.

## Docker Config Constraints

- **Non-zero size**: Docker rejects configs with 0 bytes. Empty placeholder files need minimal content (a YAML doc separator `---`, a JS comment, etc.).
- **Read-only for non-root**: Configs mount as mode 0444 owned by root. Non-root containers can read them but cannot create sibling files in the same directory. Apps that write skeleton configs at startup fail with EACCES — provide ALL expected files as Docker Configs, even empty stubs.
- **No `mode` field**: `docker compose config` serializes `mode` as an octal string, rejected by `docker stack config`. Workaround: invoke scripts via `/bin/sh /script.sh` instead of setting execute permission.

## LXC Node Constraints (Unprivileged)

The `storage` node runs as an unprivileged Proxmox LXC container. IPVS — Docker Swarm's VIP-based service mesh — is **kernel-blocked** in unprivileged LXC (requires `CAP_NET_ADMIN` in the host user namespace; no LXC config can grant this). This affects any stack deployed to the `storage` node via `*place-storage`.

**Symptom**: Services resolve each other's names to VIPs (DNS works, ICMP works), but TCP connections get `ECONNREFUSED` — IPVS forwarding tables are empty.

**Rule**: Every intra-stack service on an LXC node that does NOT need Traefik routing **must** set `endpoint_mode: dnsrr` under `deploy`. This bypasses IPVS — DNS resolves directly to container IPs instead of VIPs.

```yaml
services:
  database:
    deploy:
      <<: [*place-storage, *deploy-stop-first]
      endpoint_mode: dnsrr    # required on LXC nodes
```

| Service type | `endpoint_mode` | Why |
|-------------|:-:|-----|
| Databases, caches, internal backends | `dnsrr` | Intra-stack only, no Traefik, bypasses broken IPVS |
| Traefik-routed services | default (vip) | VIP resolution happens on Traefik's node (KVM), not the backend node |

**Not affected**: Services that only receive traffic via Traefik. VIP resolution for overlay-routed services happens on the Traefik node (KVM), which has full IPVS support. Only intra-stack service-to-service communication on the LXC node itself is broken.

## Entrypoint Wrappers

For services needing pre-start initialization, mount a shell script as a Docker Config:

- Invoke via `/bin/sh /script.sh` (bypasses mode/permission issues)
- Script runs initialization, then chains into stock entrypoint: `exec /entrypoint.sh "$@"`
- Keep non-fatal: log warnings on failure, still start the service
