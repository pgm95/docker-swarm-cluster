"""Cluster cleanup — versioned secrets/configs and node-wide docker system prune."""

import re
import sys

from . import SwarmError
from ._docker import config_list, config_rm, secret_list, secret_rm
from ._output import error, info, setup
from ._ssh import ssh_node
from .nodes import get_swarm_nodes

VERSIONED_PATTERN = re.compile(r"_[a-f0-9]+_\d+$")
SECTION_HEADER = re.compile(
    r"^Deleted (Containers|Networks|Volumes|Images|build cache objects):\s*$",
    re.IGNORECASE,
)
SECTION_KEY = {
    "containers": "containers",
    "networks": "networks",
    "volumes": "volumes",
    "images": "image_layers",
    "build cache objects": "build_cache",
}


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


def parse_prune_output(stdout: str) -> dict:
    """Parse `docker system prune` output into per-type counts and reclaimed space.

    Image layers count = lines starting with `deleted: sha256:` (deterministic and
    aligns with reclaimed bytes). Other sections are 1:1 with their line count.
    """
    counts = {"containers": 0, "networks": 0, "volumes": 0, "image_layers": 0, "build_cache": 0}
    reclaimed = "0B"
    section = None
    for raw in stdout.splitlines():
        line = raw.rstrip()
        m = SECTION_HEADER.match(line)
        if m:
            section = SECTION_KEY[m.group(1).lower()]
            continue
        if line.lower().startswith("total reclaimed space:"):
            reclaimed = line.split(":", 1)[-1].strip()
            section = None
            continue
        if not line:
            section = None
            continue
        if section == "image_layers":
            if line.startswith("deleted: sha256:"):
                counts["image_layers"] += 1
        elif section:
            counts[section] += 1
    return {**counts, "reclaimed": reclaimed}


def system_prune(nodes: list[str]) -> dict[str, dict | None]:
    """Run `docker system prune --all --volumes --force` on each node.

    Returns per-node stats dict, or None if the node was unreachable.
    """
    result = {}
    for node in nodes:
        try:
            out = ssh_node(node, "docker system prune --all --volumes --force", check=False)
            result[node] = parse_prune_output(out.stdout)
        except Exception:
            result[node] = None
    return result


_LABELS = [
    ("containers", "containers"),
    ("image_layers", "image layers"),
    ("volumes", "volumes"),
    ("networks", "networks"),
    ("build_cache", "build cache"),
]


def _format_node_summary(stats: dict) -> str:
    parts = [f"{stats[k]} {label}" for k, label in _LABELS if stats[k]]
    summary = ", ".join(parts) if parts else "nothing"
    return f"{summary} ({stats['reclaimed']})"


def cleanup() -> None:
    """Full cleanup orchestration."""
    cleanup_versioned_items("secret")
    cleanup_versioned_items("config")

    nodes = [n["hostname"] for n in get_swarm_nodes()]
    info("--- System prune ---")
    for node, stats in system_prune(nodes).items():
        if stats is None:
            info(f"  {node}: unreachable")
        else:
            info(f"  {node}: {_format_node_summary(stats)}")

    info("")
    info("Cleanup complete.")


def main() -> int:
    setup()
    try:
        cleanup()
    except SwarmError as e:
        error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
