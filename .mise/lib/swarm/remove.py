"""Remove a deployed stack and wait for services to drain."""

import argparse
import sys
import time

from . import SwarmError
from ._docker import run as docker_run, stack_list
from ._output import error, info, setup, warn
from ._stack import resolve_stack_path, stack_name


def remove(stack_ref: str, timeout: int = 60, interval: int = 3) -> int:
    """Remove a stack and wait for services to drain."""
    name = stack_name(resolve_stack_path(stack_ref))

    if name not in stack_list():
        info(f"Not deployed: {name}")
        return 0

    info(f"Removing: {name}")
    docker_run("stack", "rm", name)

    info("Waiting for services to drain...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = docker_run("stack", "ps", name, check=False)
        if result.returncode != 0:
            break
        time.sleep(interval)

    # Final check
    result = docker_run("stack", "ps", name, check=False)
    if result.returncode == 0:
        warn(f"Stack still draining after {timeout}s")
    else:
        info(f"Removed: {name}")
    return 0


def main() -> int:
    setup()
    parser = argparse.ArgumentParser(prog="swarm.remove")
    parser.add_argument("stack_path", help="Path to stack directory")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--interval", type=int, default=3)
    args = parser.parse_args()

    try:
        return remove(args.stack_path, args.timeout, args.interval)
    except SwarmError as e:
        error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
