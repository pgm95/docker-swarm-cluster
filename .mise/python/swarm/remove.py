"""Remove a deployed stack and wait for services to drain."""

import argparse
import sys
import time

from . import SwarmError, _docker
from ._cli import cli_main
from ._docker import stack_list
from ._output import error, info, init_stack_prefix, warn
from ._stack import resolve_stack_path, stack_name


def remove_stack(stack_ref: str, timeout: int = 60, interval: int = 3) -> int:
    """Remove one stack and wait for services to drain.

    Returns 0 on success (including "not deployed" no-ops), 1 on failure.
    """
    try:
        path = resolve_stack_path(stack_ref)
    except SwarmError as e:
        error(str(e))
        return 1

    name = stack_name(path)
    init_stack_prefix(name)

    if name not in stack_list():
        info(f"Not deployed: {name}")
        return 0

    info(f"Removing: {name}")
    try:
        _docker.run("stack", "rm", name)
    except SwarmError as e:
        error(f"docker stack rm failed: {e}")
        return 1

    info("Waiting for services to drain...")
    deadline = time.monotonic() + timeout
    # `docker stack ps <name>` returns rc=0 while the stack record exists
    # (with task rows during drain) and rc=1 ("nothing found in stack: <name>")
    # once Swarm has fully torn down the stack. The transition is observed
    # to be direct — no intermediate rc=0 with empty stdout state — so
    # exiting on rc != 0 is the correct drain-complete signal.
    drained = False
    while time.monotonic() < deadline:
        result = _docker.run("stack", "ps", name, check=False)
        if result.returncode != 0:
            drained = True
            break
        time.sleep(interval)

    if drained:
        info(f"Removed: {name}")
    else:
        warn(f"Stack still draining after {timeout}s")
    return 0


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="swarm.remove")
        parser.add_argument("stack", help="Stack name or path")
        parser.add_argument("--timeout", type=int, default=60)
        parser.add_argument("--interval", type=int, default=3)
        args = parser.parse_args()
        return remove_stack(args.stack, args.timeout, args.interval)
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
