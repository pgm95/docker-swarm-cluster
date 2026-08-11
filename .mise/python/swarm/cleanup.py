"""Cluster cleanup — versioned secrets/configs, orphaned networks, node-wide prune.

Three layers, matching the three resource scopes:

1. Cluster state managed by Swarm (secrets, configs, overlay networks): list
   from the manager, attempt removal of each, let Docker's "in use" check
   enforce safety.
2. Per-node Docker daemon state (containers, images, volumes, build cache):
   ``docker system prune --all --volumes --force`` over SSH on each node.
   Output is not parsed — exit code only.

Strict I/O: per-resource summaries on stderr, no machine-readable output.
"""

import re
import sys

from ._cli import cli_main
from ._docker import (
    config_list,
    config_rm,
    network_list,
    network_rm,
    secret_list,
    secret_rm,
)
from ._output import info
from ._ssh import parallel_run, ssh_node
from .nodes import get_swarm_nodes

# Versioned secrets/configs follow the naming convention
# ``<base>_<git-sha>_<epoch>``, e.g. ``authentik_secret_key_4711029_1777558235``.
# The pattern requires exactly 7 hex chars (git short SHA) and 10 digits
# (Unix epoch seconds, current values are 10 digits and will be through 2286).
# This avoids false-positives on user-named items like ``aws_iam_2024_01``.
VERSIONED_PATTERN = re.compile(r"_[a-f0-9]{7}_\d{10}$")

# Swarm's built-in load-balancer overlay. Never remove.
SYSTEM_NETWORKS = frozenset({"ingress"})


def cleanup_versioned_items(item_type: str) -> int:
    """Remove unused versioned secrets or configs. Returns count removed."""
    lister = secret_list if item_type == "secret" else config_list
    remover = secret_rm if item_type == "secret" else config_rm

    candidates = [name for name in lister() if VERSIONED_PATTERN.search(name)]
    removed = [name for name in candidates if remover(name)]

    info(f"--- {item_type.capitalize()}s ---")
    if removed:
        for name in removed:
            info(f"  {name}")
        info(f"  ({len(removed)} removed)")
    else:
        info("  none")
    return len(removed)


def cleanup_orphaned_networks() -> int:
    """Remove swarm-scoped overlay networks that are no longer in use.

    Lists all swarm-scoped networks on the cluster (excluding the built-in
    ``ingress``) and attempts removal of each. Docker refuses to remove
    networks with active service/container attachments — those failures
    are silently skipped via ``network_rm`` returning False, mirroring the
    secret/config cleanup pattern.

    Genuinely orphaned networks (no attachments, no longer referenced by any
    deployed stack) are removed.
    """
    candidates = [
        n for n in network_list(filters=["scope=swarm"])
        if n not in SYSTEM_NETWORKS
    ]
    removed = [n for n in candidates if network_rm(n)]

    info("--- Networks ---")
    if removed:
        for name in removed:
            info(f"  {name}")
        info(f"  ({len(removed)} removed)")
    else:
        info("  none")
    return len(removed)


def _prune_one(node: str) -> bool:
    """Run ``docker system prune --all --volumes --force`` on one node.

    Returns True on exit code 0, False on any failure (including SSH errors).
    Output is not parsed — Docker's progress messages stream to whatever
    stderr the SSH session inherits and are dropped.
    """
    try:
        result = ssh_node(node, "docker system prune --all --volumes --force", check=False)
        return result.returncode == 0
    except Exception:
        return False


def node_prune(nodes: list[str]) -> dict[str, bool]:
    """Run ``docker system prune`` on each node in parallel.

    Returns a per-node mapping of hostname to True (success) or False
    (unreachable / non-zero exit).
    """
    return parallel_run(nodes, _prune_one)


def cleanup() -> None:
    """Full cleanup orchestration."""
    cleanup_versioned_items("secret")
    cleanup_versioned_items("config")
    cleanup_orphaned_networks()

    nodes = [n["hostname"] for n in get_swarm_nodes()]
    info("--- Node prune ---")
    for node, ok in node_prune(nodes).items():
        info(f"  {node}: {'ok' if ok else 'failed'}")

    info("")
    info("Cleanup complete.")


def main() -> int:
    def run() -> int:
        cleanup()
        return 0
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
