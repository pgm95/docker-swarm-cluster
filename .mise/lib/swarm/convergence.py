"""Deploy convergence polling."""

import argparse
import sys
import time

from . import SwarmError
from ._docker import service_ps, stack_ps, stack_services
from ._output import error, info, setup, warn

PENDING_STATES = {"Pending", "Preparing", "Starting", "Ready"}


def wait_for_convergence(
    stack_name: str, timeout: int = 180, interval: int = 2,
) -> bool:
    """Poll until all services converge or timeout.

    Phase 1: Wait for no tasks in transient states (Pending/Preparing/Starting/Ready).
    Phase 2: Check replicas match desired (init sidecars exempted).

    Returns:
        True if converged, False if timed out.
    """
    info("Waiting for services to converge...")
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        # Phase 1: check for transient task states
        rows = stack_ps(stack_name, format_str="{{.CurrentState}}")
        states = [r[0].split()[0] if r else "" for r in rows]
        if any(s in PENDING_STATES for s in states):
            time.sleep(interval)
            continue

        # Phase 2: check replicas
        if _all_replicas_healthy(stack_name):
            return True
        time.sleep(interval)

    warn(f"Timeout waiting for convergence after {timeout}s")
    return False


def is_task_complete(service_name: str) -> bool:
    """Check if a service's most recent shutdown task completed successfully.

    Used to recognize init sidecars that finished their work.
    """
    states = service_ps(
        service_name,
        format_str="{{.CurrentState}}",
        filters=["desired-state=shutdown"],
    )
    if not states:
        return False
    return states[0].startswith("Complete")


def check_replica_health(stack_name: str) -> list[dict]:
    """Final health check after convergence.

    Returns:
        List of unhealthy services: [{"name": str, "replicas": str, "errors": list[str]}].
        Empty list = all healthy.
    """
    unhealthy = []
    for name, replicas in stack_services(stack_name):
        current, desired = _parse_replicas(replicas)
        if current < desired:
            if is_task_complete(name):
                continue
            errors = _get_task_errors(stack_name, name)
            unhealthy.append({"name": name, "replicas": replicas, "errors": errors})
            info(f"UNHEALTHY: {name} {replicas}")

    if unhealthy:
        error(f"{len(unhealthy)} service(s) not converged")
    return unhealthy


def _all_replicas_healthy(stack_name: str) -> bool:
    """Check if all services have desired replica count."""
    for name, replicas in stack_services(stack_name):
        current, desired = _parse_replicas(replicas)
        if current < desired and not is_task_complete(name):
            return False
    return True


def _parse_replicas(replicas: str) -> tuple[int, int]:
    """Parse "current/desired" string into ints."""
    parts = replicas.split("/")
    return int(parts[0]), int(parts[1])


def _get_task_errors(stack_name: str, service_name: str) -> list[str]:
    """Get error messages from failing tasks."""
    rows = stack_ps(
        stack_name,
        format_str="{{.Name}}\t{{.Error}}",
        filters=["desired-state=running"],
        no_trunc=True,
    )
    errors = []
    for row in rows:
        if len(row) >= 2 and row[1] and row[0].startswith(service_name.rsplit("_", 1)[-1]):
            errors.append(row[1])
    return errors


def main() -> int:
    setup()
    parser = argparse.ArgumentParser(prog="swarm.convergence")
    sub = parser.add_subparsers(dest="command")

    wait_cmd = sub.add_parser("wait", help="Wait for stack to converge")
    wait_cmd.add_argument("stack_name")
    wait_cmd.add_argument("--timeout", type=int, default=180)
    wait_cmd.add_argument("--interval", type=int, default=2)

    check_cmd = sub.add_parser("check", help="Check replica health")
    check_cmd.add_argument("stack_name")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "wait":
            converged = wait_for_convergence(args.stack_name, args.timeout, args.interval)
            return 0 if converged else 2

        elif args.command == "check":
            unhealthy = check_replica_health(args.stack_name)
            return 1 if unhealthy else 0

    except SwarmError as e:
        error(str(e))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
