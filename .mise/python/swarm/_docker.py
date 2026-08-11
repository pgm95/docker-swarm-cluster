"""Docker CLI subprocess wrappers.

All Docker commands in the swarm package go through this module,
making them easy to mock in tests.
"""

import json
import os
import subprocess
import sys

from . import DockerError
from ._output import log


def docker_env() -> dict[str, str]:
    """Return a subprocess env that maps SWARM_HOST to DOCKER_HOST.

    Keeps DOCKER_HOST out of the user's shell so local Docker contexts stay free.
    """
    env = os.environ.copy()
    if swarm_host := env.get("SWARM_HOST"):
        env["DOCKER_HOST"] = swarm_host
    return env


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
    result = subprocess.run(cmd, capture_output=capture, text=True, input=input, env=docker_env(), check=False)
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


def manifest_exists(image: str) -> bool:
    """Check if a Docker image manifest exists in a registry."""
    result = run("manifest", "inspect", image, check=False)
    return result.returncode == 0


def build(tag: str, context_dir: str) -> None:
    """Build a Docker image, streaming output to stderr.

    Stdout is redirected to stderr to keep the strict I/O contract: build
    diagnostics are not pipeable data.
    """
    stream("build", "-t", tag, context_dir)


def push(image: str) -> None:
    """Push a Docker image to a registry, streaming output to stderr."""
    stream("push", image)


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


def stream(*args: str, input: str | None = None, line_prefixed: bool = False) -> None:
    """Run a docker command streaming output, with stdout redirected to stderr.

    Used for commands whose stdout would otherwise pollute the strict I/O
    contract (build progress, push progress, stack deploy diagnostics). When
    `input` is provided, it is piped on stdin (e.g. compose YAML for
    `docker stack deploy -c -`).

    When `line_prefixed=True`, output is read line by line and
    `_output.get_stack_prefix()` is prepended to each line before writing.
    This keeps `docker stack deploy`'s own output (`Creating service X`,
    `Updating config Y`) attributable to the stack the lib is currently
    handling, matching the formatting of our own `info()` calls.

    `line_prefixed=False` (the default) preserves the raw byte stream so
    that progress output with carriage-return refresh (e.g. `docker build`,
    `docker push`) renders correctly on a TTY.
    """
    cmd = ["docker", *args]
    log.debug("$ %s", " ".join(cmd))

    if line_prefixed:
        from . import _output  # avoid import cycle at module load time
        prefix = _output.get_stack_prefix()
        # Track the tail of the streamed output so a non-zero exit can
        # surface Docker's actual error message, not just the exit code.
        tail: list[str] = []
        TAIL_MAX = 20
        with subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if input is not None else None,
            text=True,
            env=docker_env(),
        ) as proc:
            if input is not None:
                assert proc.stdin is not None
                try:
                    proc.stdin.write(input)
                    proc.stdin.close()
                except BrokenPipeError:
                    # Child died before consuming all of stdin; the tail of
                    # captured output and the eventual non-zero exit code
                    # below describe what happened.
                    pass
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stderr.write(f"{prefix}{line}")
                sys.stderr.flush()
                tail.append(line)
                if len(tail) > TAIL_MAX:
                    tail.pop(0)
            proc.wait()
            if proc.returncode != 0:
                raise DockerError(cmd, proc.returncode, "".join(tail).rstrip())
        return

    result = subprocess.run(
        cmd,
        stdout=sys.stderr,
        input=input,
        text=input is not None,
        check=False,
        env=docker_env(),
    )
    if result.returncode != 0:
        raise DockerError(cmd, result.returncode, "")
