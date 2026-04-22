"""Docker CLI subprocess wrappers.

All Docker commands in the swarm package go through this module,
making them easy to mock in tests.
"""

import json
import subprocess
import sys

from . import DockerError
from ._output import log


def run(
    *args: str,
    check: bool = True,
    capture: bool = True,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a docker command.

    Args:
        *args: Docker subcommand and arguments (without 'docker' prefix).
        check: Raise DockerError on non-zero exit.
        capture: Capture stdout/stderr.
        input: String to pass to stdin.

    Returns:
        CompletedProcess with stdout/stderr as strings.
    """
    cmd = ["docker", *args]
    log.debug("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=capture, text=True, input=input)
    if check and result.returncode != 0:
        raise DockerError(cmd, result.returncode, result.stderr.strip())
    return result


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


def secret_exists(name: str) -> bool:
    """Check if a Docker secret exists."""
    result = run("secret", "inspect", name, check=False)
    return result.returncode == 0


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


def manifest_exists(image: str) -> bool:
    """Check if a Docker image manifest exists in a registry."""
    result = run("manifest", "inspect", image, check=False)
    return result.returncode == 0


def build(tag: str, context_dir: str) -> None:
    """Build a Docker image, streaming output to stderr.

    Stdout is redirected to stderr so the parent shell's command substitution
    (which captures stdout for export statements) isn't polluted by build logs.
    """
    _stream("build", "-t", tag, context_dir)


def push(image: str) -> None:
    """Push a Docker image to a registry, streaming output to stderr."""
    _stream("push", image)


def _stream(*args: str) -> None:
    """Run a docker command streaming stderr to terminal, stdout to stderr."""
    cmd = ["docker", *args]
    log.debug("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, stdout=sys.stderr, check=False)
    if result.returncode != 0:
        raise DockerError(cmd, result.returncode, "")


def node_inspect_raw() -> tuple[list[str], str]:
    """Get raw node IDs and inspect JSON.

    Returns:
        Tuple of (node_id_list, raw_json_string).
        Useful when callers need to pass JSON to other functions.
    """
    node_ids = run("node", "ls", "-q").stdout.strip().splitlines()
    if not node_ids:
        return [], "[]"
    result = run("node", "inspect", *node_ids)
    return node_ids, result.stdout
