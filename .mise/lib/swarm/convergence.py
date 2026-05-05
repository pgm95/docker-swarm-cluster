"""Deploy convergence polling."""

import argparse
import sys
import time

from ._cli import cli_main
from ._docker import (
    parse_replicas,
    service_ps,
    stack_ps,
    stack_services,
    task_name_to_service,
)
from ._output import error, info, warn

PENDING_STATES = {"Pending", "Preparing", "Starting", "Ready"}


def verify(
    stack_name: str,
    timeout: int = 180,
    max_interval: int = 15,
    *,
    interval: float = 2.0,
) -> tuple[bool, list[dict]]:
    """Wait for convergence then check replica health in one call.

    Phase 1: Poll until no tasks remain in transient states
        (Pending/Preparing/Starting/Ready) and replicas match desired,
        or `timeout` seconds elapse. Polling sleep grows exponentially
        from `interval` (initial, fixed at 2s — kept as a keyword-only
        parameter for tests) up to `max_interval` at 1.5x per iteration.
        Fast deploys stay responsive (early polls are short); slow deploys
        avoid flooding the Swarm manager with redundant queries.
    Phase 2: Inspect each service's final replica state. Init sidecars
        whose latest shutdown task is `Complete` are exempt.

    Returns:
        (converged, unhealthy) where converged is False if Phase 1 timed
        out, and unhealthy is the list of services whose current < desired
        after Phase 1 finished (excluding completed init sidecars).
    """
    info("Waiting for services to converge...")
    deadline = time.monotonic() + timeout
    converged = False
    sleep_for: float = interval

    while time.monotonic() < deadline:
        # Phase 1: check for transient task states
        rows = stack_ps(stack_name, format_str="{{.CurrentState}}")
        states = [r[0].split()[0] if r else "" for r in rows]
        if not any(s in PENDING_STATES for s in states) and _all_replicas_healthy(stack_name):
            converged = True
            break
        time.sleep(sleep_for)
        sleep_for = min(sleep_for * 1.5, max_interval)

    if not converged:
        warn(f"Timeout waiting for convergence after {timeout}s")

    return converged, _collect_unhealthy(stack_name)


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


def _collect_unhealthy(stack_name: str) -> list[dict]:
    """Inspect all services in a stack; return entries whose current < desired.

    Services with transient unparseable replica strings (e.g. ``"N/A"``) are
    treated as unhealthy so they surface in the report rather than being
    silently skipped — the convergence check needs an answer either way.
    """
    # Fetch task errors for the whole stack ONCE and group by service name.
    # Avoids re-querying `docker stack ps` for every unhealthy service.
    errors_by_service = _task_errors_by_service(stack_name)

    unhealthy = []
    for name, replicas in stack_services(stack_name):
        parsed = parse_replicas(replicas)
        if parsed is None or parsed[0] < parsed[1]:
            if parsed is not None and is_task_complete(name):
                continue
            unhealthy.append({
                "name": name,
                "replicas": replicas,
                "errors": errors_by_service.get(name, []),
            })
            info(f"UNHEALTHY: {name} {replicas}")
    return unhealthy


def _all_replicas_healthy(stack_name: str) -> bool:
    """Check if all services have desired replica count.

    Unparseable replica strings count as not-healthy so the polling loop keeps
    waiting rather than declaring a transient state as final.
    """
    for name, replicas in stack_services(stack_name):
        parsed = parse_replicas(replicas)
        if parsed is None:
            return False
        if parsed[0] < parsed[1] and not is_task_complete(name):
            return False
    return True


def _task_errors_by_service(stack_name: str) -> dict[str, list[str]]:
    """Group failing-task error messages by service name in one stack-ps call.

    A single ``docker stack ps`` query covers the whole stack; downstream
    callers look up per-service error lists by name. With N unhealthy
    services this replaces N redundant whole-stack queries with one.
    """
    rows = stack_ps(
        stack_name,
        format_str="{{.Name}}\t{{.Error}}",
        filters=["desired-state=running"],
        no_trunc=True,
    )
    grouped: dict[str, list[str]] = {}
    for row in rows:
        if len(row) < 2 or not row[1]:
            continue
        service = task_name_to_service(row[0])
        grouped.setdefault(service, []).append(row[1])
    return grouped


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="swarm.convergence")
        parser.add_argument("stack_name")
        parser.add_argument("--timeout", type=int, default=180)
        parser.add_argument("--max-interval", type=int, default=15)
        args = parser.parse_args()

        converged, unhealthy = verify(
            args.stack_name, timeout=args.timeout, max_interval=args.max_interval,
        )
        if unhealthy:
            error(f"{len(unhealthy)} service(s) not converged")
        return 1 if not converged or unhealthy else 0
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
