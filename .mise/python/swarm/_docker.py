"""Docker CLI wrappers bound to the Swarm manager, plus Swarm resource helpers.

``run``, ``stream``, ``build``, ``push`` and ``manifest_exists`` delegate to
``core.engine`` with ``host`` defaulted to ``SWARM_HOST``, so every Swarm
module targets the manager without naming it. Tests patch ``swarm._docker.run``
and the helpers below go through it. The resource helpers (nodes, stacks,
services, secrets, configs, networks) only make sense against a manager.
"""

import json
import os
import subprocess

from core import engine


def swarm_host() -> str | None:
    """Docker URL of the Swarm manager (``SWARM_HOST``), None when unset."""
    return os.environ.get("SWARM_HOST") or None


def run(
    *args: str,
    check: bool = True,
    capture: bool = True,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a docker command against the Swarm manager (see ``core.engine.run``)."""
    return engine.run(*args, host=swarm_host(), check=check, capture=capture, input=input)


def stream(*args: str, input: str | None = None, line_prefixed: bool = False) -> None:
    """Stream a docker command against the Swarm manager (see ``core.engine.stream``)."""
    engine.stream(*args, host=swarm_host(), input=input, line_prefixed=line_prefixed)


def manifest_exists(image: str) -> bool:
    """Check if a Docker image manifest exists in a registry."""
    result = run("manifest", "inspect", image, check=False)
    return result.returncode == 0


def build(tag: str, context_dir: str) -> None:
    """Build a Docker image on the manager, streaming output to stderr."""
    stream("build", "-t", tag, context_dir)


def push(image: str) -> None:
    """Push a Docker image from the manager, streaming output to stderr."""
    stream("push", image)


def inspect_nodes() -> list[dict]:
    """Get all swarm node details as parsed JSON."""
    node_ids = run("node", "ls", "-q").stdout.strip().splitlines()
    if not node_ids:
        return []
    result = run("node", "inspect", *node_ids)
    return json.loads(result.stdout)


def stack_services(stack_name: str) -> list[tuple[str, str]]:
    """Get services for a stack.

    Returns:
        List of (service_name, "current/desired") tuples.
    """
    result = run(
        "stack", "services", stack_name,
        "--format", "{{.Name}}\t{{.Replicas}}",
    )
    services = []
    for line in result.stdout.strip().splitlines():
        if "\t" in line:
            name, replicas = line.split("\t", 1)
            services.append((name, replicas))
    return services


def stack_ps(
    stack_name: str,
    format_str: str = "{{.Name}}\t{{.CurrentState}}",
    filters: list[str] | None = None,
    no_trunc: bool = False,
) -> list[list[str]]:
    """Get task list for a stack.

    Returns:
        List of rows, each row a list of fields split by tab.
    """
    cmd = ["stack", "ps", stack_name, "--format", format_str]
    if no_trunc:
        cmd.append("--no-trunc")
    for f in filters or []:
        cmd.extend(["--filter", f])
    result = run(*cmd, check=False)
    rows = []
    for line in result.stdout.strip().splitlines():
        if line:
            rows.append(line.split("\t"))
    return rows


def service_ps(
    service_name: str,
    format_str: str = "{{.CurrentState}}",
    filters: list[str] | None = None,
) -> list[str]:
    """Get task states for a specific service."""
    cmd = ["service", "ps", service_name, "--format", format_str]
    for f in filters or []:
        cmd.extend(["--filter", f])
    result = run(*cmd, check=False)
    return [line for line in result.stdout.strip().splitlines() if line]


def stack_list() -> list[str]:
    """Get names of all deployed stacks."""
    result = run("stack", "ls", "--format", "{{.Name}}")
    return [line for line in result.stdout.strip().splitlines() if line]


def service_ls() -> list[tuple[str, str]]:
    """Get all services across all stacks.

    Returns:
        List of (service_name, "current/desired") tuples.
    """
    result = run(
        "service", "ls",
        "--format", "{{.Name}}\t{{.Replicas}}",
    )
    services = []
    for line in result.stdout.strip().splitlines():
        if "\t" in line:
            name, replicas = line.split("\t", 1)
            services.append((name, replicas))
    return services


def service_ps_multi(
    service_names: list[str],
    format_str: str = "{{.Name}}\t{{.CurrentState}}",
    filters: list[str] | None = None,
) -> list[list[str]]:
    """Get tasks for multiple services in a single call.

    Returns:
        List of rows, each row a list of fields split by tab.
    """
    if not service_names:
        return []
    cmd = ["service", "ps", *service_names, "--format", format_str]
    for f in filters or []:
        cmd.extend(["--filter", f])
    result = run(*cmd, check=False)
    rows = []
    for line in result.stdout.strip().splitlines():
        if line:
            rows.append(line.split("\t"))
    return rows


def secret_create(name: str, value: str) -> None:
    """Create a Docker secret from a string value."""
    run("secret", "create", name, "-", input=value)


def secret_list() -> list[str]:
    """List all Docker secret names."""
    result = run("secret", "ls", "--format", "{{.Name}}")
    return [line for line in result.stdout.strip().splitlines() if line]


def config_list() -> list[str]:
    """List all Docker config names."""
    result = run("config", "ls", "--format", "{{.Name}}")
    return [line for line in result.stdout.strip().splitlines() if line]


def secret_rm(name: str) -> bool:
    """Remove a Docker secret. Returns True if removed, False if in use."""
    result = run("secret", "rm", name, check=False)
    return result.returncode == 0


def config_rm(name: str) -> bool:
    """Remove a Docker config. Returns True if removed, False if in use."""
    result = run("config", "rm", name, check=False)
    return result.returncode == 0


def network_list(filters: list[str] | None = None) -> list[str]:
    """List Docker network names, optionally filtered.

    Args:
        filters: list of ``key=value`` filter expressions (e.g. ``"scope=swarm"``).

    Returns:
        Network names, one per entry.
    """
    args = ["network", "ls", "--format", "{{.Name}}"]
    for f in filters or []:
        args.extend(["--filter", f])
    result = run(*args)
    return [line for line in result.stdout.strip().splitlines() if line]


def network_rm(name: str) -> bool:
    """Remove a Docker network. Returns True if removed, False if in use.

    Mirrors the secret/config rm pattern: Docker enforces the safety check
    (refuses to remove networks with attached containers/services), and the
    caller treats False as "skip, still in use".
    """
    result = run("network", "rm", name, check=False)
    return result.returncode == 0


def task_name_to_service(task_name: str) -> str:
    """Strip Swarm's `.<slot>.<id>` suffix from a task name to recover the service name.

    Swarm names tasks `<service>.<slot>.<id>` (replicated) or `<service>.<node-id>.<id>`
    (global). The service name is everything before the last two dot-separated segments.
    Returns the input unchanged if there are no dots.
    """
    if "." not in task_name:
        return task_name
    return task_name.rsplit(".", 2)[0]


def parse_replicas(replicas: str) -> tuple[int, int] | None:
    """Parse a Swarm replica count of the form ``"current/desired"`` (e.g. ``"1/1"``).

    Returns ``None`` when the value is missing, malformed, or contains
    non-integer placeholders like ``"N/A"`` that Swarm can transiently emit
    for global services during reconfiguration. Callers should treat ``None``
    as "state currently unknown" rather than as a definite failure.
    """
    parts = replicas.split("/")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None
