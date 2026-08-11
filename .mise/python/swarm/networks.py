"""Overlay network discovery and initialization.

Networks are discovered by walking every stack's rendered compose document,
collecting top-level ``networks:`` entries marked ``external: true``. The
lib creates each such network if it doesn't exist on the cluster, with
``--internal`` if its name appears in ``SWARM_INTERNAL_NETWORKS``.

There is no name-prefix convention or namespace specialization — any
external network referenced by any stack qualifies for creation.
"""

import argparse
import os
import sys

from . import SwarmError
from ._cli import cli_main
from ._compose import compose_json
from ._docker import run as docker_run
from ._output import debug, error, info
from ._stack import find_namespaces, find_stacks


def get_external_networks() -> list[str]:
    """Discover external overlay networks across every stack.

    Renders each ``<namespace>/<stack>/compose.yml`` once and pulls out
    ``networks.<key>`` entries with ``external: true``. Compose-spec
    permits a ``name:`` field that overrides the dict key — when present,
    that's the actual platform network name and what we need to create on
    the cluster. The dict key alone is just the stack-local alias.
    Networks referenced by multiple stacks dedupe naturally.

    Returns:
        Sorted list of platform network names.
    """
    found: set[str] = set()
    for ns in find_namespaces():
        for stack_dir in find_stacks(ns):
            compose = stack_dir / "compose.yml"
            if not compose.is_file():
                continue
            try:
                rendered = compose_json(compose)
            except Exception as e:
                # Stack with a broken compose; skip and let
                # `validate:compose` surface the error elsewhere.
                debug(f"skipping {compose}: {e}")
                continue
            for key, spec in (rendered.get("networks") or {}).items():
                spec = spec or {}
                if spec.get("external") is True:
                    # Prefer spec["name"] over the dict key — compose-spec
                    # allows a stack-local alias that differs from the
                    # platform-level network name.
                    found.add(spec.get("name") or key)
    return sorted(found)


def init_networks(internal_networks: set[str] | None = None) -> None:
    """Create overlay networks idempotently.

    Args:
        internal_networks: Set of network names that should be ``--internal``.
            Defaults to ``SWARM_INTERNAL_NETWORKS`` env var (space-separated).
    """
    if internal_networks is None:
        raw = os.environ.get("SWARM_INTERNAL_NETWORKS", "")
        internal_networks = set(raw.split()) if raw else set()

    for net in get_external_networks():
        result = docker_run("network", "inspect", net, check=False)
        if result.returncode == 0:
            info(f"  exists: {net}")
            continue
        cmd = ["network", "create", "-d", "overlay", "--attachable"]
        mtu = os.environ.get("SWARM_OVERLAY_MTU", "")
        if mtu:
            cmd += ["--opt", f"com.docker.network.driver.mtu={mtu}"]
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
    def run() -> int:
        parser = argparse.ArgumentParser(prog="swarm.networks")
        sub = parser.add_subparsers(dest="command")
        sub.add_parser("list", help="List external overlay networks discovered across stacks")
        sub.add_parser("init", help="Create overlay networks (idempotent)")
        args = parser.parse_args()
        if not args.command:
            parser.print_help()
            return 1
        if args.command == "list":
            for net in get_external_networks():
                print(net)
        elif args.command == "init":
            init_networks()
        return 0
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
