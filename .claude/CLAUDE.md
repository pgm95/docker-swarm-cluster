# swarm-cluster

Docker Swarm homelab infrastructure with centralized management.

## Key Files

| File | Purpose |
|------|---------|
| `README.md` | Architecture, setup, gotchas |
| `.mise/README.md` | Task reference, deploy pipeline, environment profiles |
| `PROJECT_SECRETS_DIR/shared.yaml` | SOPS-encrypted shared secrets (`GLOBAL_SECRETS`) |
| `PROJECT_SECRETS_DIR/{env}.yaml` | SOPS-encrypted env-specific secrets (per `MISE_ENV`) |
| `stacks/<namespace>/<stack>/secrets.env` | SOPS-encrypted stack-specific secrets |
| `stacks/<namespace>/<stack>/secrets.yml` | Swarm secret definitions (versioned) |
| `stacks/<namespace>/<stack>/configs.yml` | Docker config definitions (versioned) |
| `stacks/_shared/anchors.yml` | Centralized YAML anchors (logging, placement, deploy, resources) |
| `.mise/tasks/scripts/*.sh` | Shared bash function libraries sourced by tasks |

## Core Concepts

- **Remote management**: All Docker commands target remote Swarm manager via SSH (`DOCKER_HOST`). SOPS decryption and compose preprocessing happen locally.
- **Environment profiles**: `MISE_ENV` (dev default, prod explicit). Base `[env]` processes before profile `[env]` — Tera templates in base cannot reference profile vars.
- **Stack discovery**: Infra stacks auto-discovered via `NN_` folder prefix in `stacks/infra/`. `stack_name()` strips prefix for Swarm. App stacks auto-discovered alphabetically.
- **Node discovery**: Swarm nodes discovered via `docker node inspect`. Only `SWARM_NODE_DEFAULT` is configured manually.
- **Image naming**: `${GLOBAL_SWARM_OCI_REGISTRY}/<stack>/<service>:<tag>`. Custom images use content-hash tags from `build/*/` directories.
