"""Site-wide orchestration — deploy infra/apps, reset."""

import argparse
import subprocess
import sys
import time

from . import SwarmError
from ._docker import run as docker_run
from ._output import error, info, setup
from ._stack import find_stacks
from .networks import get_infra_networks


def _mise_run(task: str, *args: str) -> bool:
    """Run a mise task. Returns True on success."""
    cmd = ["mise", "run", task, *args]
    return subprocess.run(cmd).returncode == 0


def deploy_infra() -> int:
    stacks = find_stacks("stacks/infra")
    failed = []
    info("=== Infra Deploy ===")
    info(f"Stacks: {len(stacks)}")
    info("")

    for stack in stacks:
        info(f"━━━ {stack.name} ━━━")
        if not _mise_run("swarm:deploy", str(stack)):
            info(f"FAILED: {stack.name}")
            failed.append(stack.name)
        info("")

    deployed = len(stacks) - len(failed)
    summary = f"deployed {deployed}"
    if failed:
        summary += f", failed {len(failed)}: {' '.join(failed)}"
    info(f"=== Infra deploy complete ({summary}) ===")
    return 1 if failed else 0


def deploy_apps() -> int:
    stacks = find_stacks("stacks/apps")
    failed = []
    skipped = 0
    info("=== Apps Deploy ===")
    info(f"Stacks: {len(stacks)}")
    info("")

    for stack in stacks:
        if (stack / ".nodeploy").exists():
            info(f"━━━ {stack.name} ━━━")
            info("SKIP (.nodeploy)")
            skipped += 1
            info("")
            continue
        info(f"━━━ {stack.name} ━━━")
        if not _mise_run("swarm:deploy", str(stack)):
            info(f"FAILED: {stack.name}")
            failed.append(stack.name)
        time.sleep(5)
        info("")

    deployed = len(stacks) - skipped - len(failed)
    summary = f"deployed {deployed}"
    if skipped:
        summary += f", skipped {skipped}"
    if failed:
        summary += f", failed {len(failed)}: {' '.join(failed)}"
    info(f"=== Apps deploy complete ({summary}) ===")
    return 1 if failed else 0


def drain() -> int:
    # Remove app stacks
    info("--- Removing app stacks ---")
    for stack in find_stacks("stacks/apps"):
        _mise_run("swarm:remove", str(stack))
    info("")

    # Remove infra stacks in reverse order
    info("--- Removing infra stacks ---")
    for stack in find_stacks("stacks/infra", reverse=True):
        _mise_run("swarm:remove", str(stack))

    # Remove overlay networks
    info("")
    info("--- Removing overlay networks ---")
    for net in get_infra_networks():
        result = docker_run("network", "rm", net, check=False)
        if result.returncode == 0:
            info(f"  removed: {net}")
        else:
            info(f"  skipped: {net} (not found or in use)")

    return 0


def main() -> int:
    setup()
    parser = argparse.ArgumentParser(prog="swarm.site")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("deploy-infra", help="Deploy infrastructure stacks in order")
    sub.add_parser("deploy-apps", help="Deploy all application stacks")
    sub.add_parser("drain", help="Remove all stacks in reverse order")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "deploy-infra":
            return deploy_infra()
        elif args.command == "deploy-apps":
            return deploy_apps()
        elif args.command == "drain":
            return drain()
    except SwarmError as e:
        error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
