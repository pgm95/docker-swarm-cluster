"""Cluster cleanup — versioned secrets/configs and node pruning."""

import os
import re
import sys

from . import SwarmError
from ._docker import config_list, config_rm, secret_list, secret_rm
from ._output import error, info, setup
from ._ssh import ssh_node
from .nodes import get_swarm_nodes

VERSIONED_PATTERN = re.compile(r"_[a-f0-9]+_\d+$")


def cleanup_versioned_items(item_type: str) -> dict:
    """Remove unused versioned secrets or configs.

    Matches names ending with _<hex>_<digits> (deploy version suffix).
    Items in use by running services will fail silently.

    Returns:
        {"removed": int, "items": list[str]}
    """
    lister = secret_list if item_type == "secret" else config_list
    remover = secret_rm if item_type == "secret" else config_rm

    items = [name for name in lister() if VERSIONED_PATTERN.search(name)]
    removed = []
    for name in items:
        if remover(name):
            info(f"  - {name}")
            removed.append(name)
    info(f"  Removed: {len(removed)}")
    return {"removed": len(removed), "items": removed}


def prune_containers(nodes: list[str]) -> dict[str, int]:
    """Run docker container prune on each node."""
    result = {}
    for node in nodes:
        try:
            out = ssh_node(node, "docker container prune -f", check=False)
            count = sum(1 for line in out.stdout.splitlines() if re.match(r"^[a-f0-9]", line))
            result[node] = count
        except Exception:
            result[node] = 0
    return result


def prune_images(nodes: list[str]) -> dict[str, dict]:
    """Run docker image prune -af on each node."""
    result = {}
    for node in nodes:
        try:
            out = ssh_node(node, "docker image prune -af", check=False)
            count = sum(1 for line in out.stdout.splitlines() if line.startswith("deleted:"))
            reclaimed = ""
            for line in out.stdout.splitlines():
                if "reclaimed space:" in line:
                    reclaimed = line.split("reclaimed space:")[-1].strip()
            result[node] = {"count": count, "reclaimed": reclaimed or "0B"}
        except Exception:
            result[node] = {"count": 0, "reclaimed": "0B"}
    return result


def prune_volumes(nodes: list[str]) -> dict[str, dict]:
    """Run docker volume prune on each node."""
    result = {}
    for node in nodes:
        try:
            out = ssh_node(node, "docker volume prune -f", check=False)
            count = sum(1 for line in out.stdout.splitlines() if re.match(r"^[a-f0-9]", line))
            reclaimed = ""
            for line in out.stdout.splitlines():
                if "reclaimed space:" in line:
                    reclaimed = line.split("reclaimed space:")[-1].strip()
            result[node] = {"count": count, "reclaimed": reclaimed or "0B"}
        except Exception:
            result[node] = {"count": 0, "reclaimed": "0B"}
    return result


def cleanup(prune_images_flag: bool = False) -> None:
    """Full cleanup orchestration."""
    total_removed = 0

    print("=== Secrets ===")
    r = cleanup_versioned_items("secret")
    total_removed += r["removed"]
    print()

    print("=== Configs ===")
    r = cleanup_versioned_items("config")
    total_removed += r["removed"]
    print()

    nodes = [n["hostname"] for n in get_swarm_nodes()]

    print("=== Containers ===")
    for node, count in prune_containers(nodes).items():
        print(f"  {node}: {count}")
        total_removed += count
    print()

    if prune_images_flag:
        print("=== Images ===")
        for node, data in prune_images(nodes).items():
            print(f"  {node}: {data['count']} ({data['reclaimed']})")
            total_removed += data["count"]
        print()

    print("=== Volumes ===")
    for node, data in prune_volumes(nodes).items():
        print(f"  {node}: {data['count']} ({data['reclaimed']})")
        total_removed += data["count"]
    print()

    if total_removed == 0:
        print("Nothing to clean up.")
    else:
        print("Cleanup complete.")


def main() -> int:
    setup()
    prune = os.environ.get("usage_prune_images") == "true"

    try:
        cleanup(prune)
    except SwarmError as e:
        error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
