"""Overlay network discovery and initialization."""

import argparse
import os
import re
import sys
from pathlib import Path

from . import SwarmError
from ._docker import run as docker_run
from ._output import error, info, setup


def get_infra_networks() -> list[str]:
    """Discover overlay networks from infra compose files.

    Scans stacks/infra/*/compose.yml for networks declared as
    external with the infra_ prefix.
    """
    networks = set()
    for compose in sorted(Path("stacks/infra").glob("*/compose.yml")):
        text = compose.read_text()
        # Find "infra_*:" lines followed by "external: true"
        for match in re.finditer(r"(infra_[a-z_-]+):\s*\n\s+external:\s*true", text):
            networks.add(match.group(1))
    return sorted(networks)


def init_networks(internal_networks: set[str] | None = None) -> None:
    """Create overlay networks idempotently.

    Args:
        internal_networks: Set of network names that should be --internal.
            Defaults to SWARM_INTERNAL_NETWORKS env var (space-separated).
    """
    if internal_networks is None:
        raw = os.environ.get("SWARM_INTERNAL_NETWORKS", "")
        internal_networks = set(raw.split()) if raw else set()

    for net in get_infra_networks():
        result = docker_run("network", "inspect", net, check=False)
        if result.returncode == 0:
            info(f"  exists: {net}")
            continue
        cmd = ["network", "create", "-d", "overlay", "--attachable"]
        if net in internal_networks:
            cmd.append("--internal")
        cmd.append(net)
        result = docker_run(*cmd, check=False)
        if result.returncode == 0:
            info(f"  created: {net}")
        else:
            error(f"FAILED: {net}")
            raise SwarmError(f"Failed to create network {net}")


def main() -> int:
    setup()
    parser = argparse.ArgumentParser(prog="swarm.networks")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List discovered infra overlay networks")
    sub.add_parser("init", help="Create overlay networks (idempotent)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "list":
            for net in get_infra_networks():
                print(net)
        elif args.command == "init":
            init_networks()
    except SwarmError as e:
        error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
