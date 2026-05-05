"""Registry authentication across swarm nodes."""

import argparse
import os
import shlex
import sys

from . import _docker
from ._cli import cli_main
from ._output import error, info
from ._ssh import parallel_run, ssh_node
from .nodes import get_swarm_nodes


def login_node(hostname: str, registry: str, user: str, password: str) -> bool:
    """Docker login on a remote node via SSH stdin pipe."""
    try:
        ssh_node(
            hostname,
            f"docker login -u {shlex.quote(user)} --password-stdin {shlex.quote(registry)}",
            stdin_data=password,
        )
        return True
    except Exception:
        return False


def login_local(registry: str, user: str, password: str) -> bool:
    """Docker login on the local machine via the Docker CLI."""
    try:
        result = _docker.run(
            "login", "-u", user, "--password-stdin", registry,
            input=password,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def registry_auth(local: bool = False) -> int:
    """Login all swarm nodes (+ optionally local) to private registry in parallel."""
    registry = os.environ.get("GLOBAL_SWARM_OCI_REGISTRY", "")
    user = os.environ.get("REGISTRY_USER", "")
    password = os.environ.get("REGISTRY_PASS", "")

    if not registry or not user or not password:
        error("Missing GLOBAL_SWARM_OCI_REGISTRY, REGISTRY_USER, or REGISTRY_PASS")
        return 1

    nodes = get_swarm_nodes()
    target_count = len(nodes) + (1 if local else 0)
    info(f"Registry: {registry}")
    info(f"Targets: {target_count}" + (f" ({len(nodes)} nodes + local machine)" if local else " nodes"))
    info("")

    # Fan out node logins in parallel; the result dict's insertion order
    # follows submission order (== get_swarm_nodes order), then local.
    hostnames = [n["hostname"] for n in nodes]
    results: dict[str, bool] = parallel_run(
        hostnames, lambda h: login_node(h, registry, user, password),
    )
    if local:
        results["local machine"] = login_local(registry, user, password)

    failed = 0
    for target, ok in results.items():
        info(f"  {target}: {'ok' if ok else 'FAILED'}")
        if not ok:
            failed += 1

    info("")
    if failed > 0:
        error(f"{failed} target(s) failed to authenticate")
        return 1
    info("All targets authenticated.")
    return 0


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="swarm.registry_auth")
        parser.add_argument(
            "--local",
            action="store_true",
            help="Also login the local dev machine in addition to swarm nodes",
        )
        args = parser.parse_args()
        return registry_auth(args.local)
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
