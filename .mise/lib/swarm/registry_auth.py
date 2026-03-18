"""Registry authentication across swarm nodes."""

import os
import subprocess
import sys

from . import SwarmError
from ._output import error, info, setup
from ._ssh import ssh_node
from .nodes import get_swarm_nodes


def login_node(hostname: str, registry: str, user: str, password: str) -> bool:
    """Docker login on a remote node via SSH stdin pipe."""
    try:
        ssh_node(
            hostname,
            f"docker login -u '{user}' --password-stdin '{registry}'",
            stdin_data=password,
        )
        return True
    except Exception:
        return False


def login_local(registry: str, user: str, password: str) -> bool:
    """Docker login on the local machine."""
    result = subprocess.run(
        ["docker", "login", "-u", user, "--password-stdin", registry],
        input=password,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def registry_auth(local: bool = False) -> int:
    """Login all swarm nodes (+ optionally local) to private registry."""
    registry = os.environ.get("GLOBAL_SWARM_OCI_REGISTRY", "")
    user = os.environ.get("REGISTRY_USER", "")
    password = os.environ.get("REGISTRY_PASS", "")

    if not registry or not user or not password:
        error("Missing GLOBAL_SWARM_OCI_REGISTRY, REGISTRY_USER, or REGISTRY_PASS")
        return 1

    nodes = get_swarm_nodes()
    target_count = len(nodes) + (1 if local else 0)
    info(f"Registry: {registry}")
    info(f"Targets: {target_count}" + (f" ({len(nodes)} nodes + local machine)" if local else f" nodes"))
    info("")

    failed = 0
    if local:
        if login_local(registry, user, password):
            info("  local machine: ok")
        else:
            info("  local machine: FAILED")
            failed += 1

    for node in nodes:
        hostname = node["hostname"]
        if login_node(hostname, registry, user, password):
            info(f"  {hostname}: ok")
        else:
            info(f"  {hostname}: FAILED")
            failed += 1

    info("")
    if failed > 0:
        error(f"{failed} target(s) failed to authenticate")
        return 1
    info("All targets authenticated.")
    return 0


def main() -> int:
    setup()
    local = os.environ.get("usage_local") == "true"

    try:
        return registry_auth(local)
    except SwarmError as e:
        error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
