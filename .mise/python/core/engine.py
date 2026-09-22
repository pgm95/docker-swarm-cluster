"""Docker CLI subprocess wrappers, daemon-agnostic.

Every docker invocation in the tooling goes through ``run`` or ``stream``.
The daemon is chosen per call through ``host`` (a Docker URL such as
``ssh://root@swarm-vm`` or ``unix:///var/run/docker.sock``); ``None`` leaves
the CLI's own resolution alone (context, ``DOCKER_HOST`` in the shell). The
Swarm package defaults ``host`` to ``SWARM_HOST``; the Compose package
looks it up per project host.
"""

import os
import subprocess
import sys

from . import DockerError
from .output import get_prefix, log


def docker_env(host: str | None = None) -> dict[str, str]:
    """Subprocess env for a docker call, with ``DOCKER_HOST`` set when ``host`` is given.

    Keeps ``DOCKER_HOST`` out of the user's shell so local Docker contexts
    stay free; the override exists only for the child process.
    """
    env = os.environ.copy()
    if host:
        env["DOCKER_HOST"] = host
    return env


def run(
    *args: str,
    host: str | None = None,
    check: bool = True,
    capture: bool = True,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a docker command.

    Args:
        *args: Docker subcommand and arguments (without 'docker' prefix).
        host: Docker URL of the daemon to target; None keeps the CLI default.
        check: Raise DockerError on non-zero exit.
        capture: Capture stdout/stderr.
        input: String to pass to stdin.

    Returns:
        CompletedProcess with stdout/stderr as strings.
    """
    cmd = ["docker", *args]
    log.debug("$ %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=capture, text=True, input=input, env=docker_env(host), check=False,
    )
    if check and result.returncode != 0:
        raise DockerError(cmd, result.returncode, result.stderr.strip())
    return result


def stream(
    *args: str,
    host: str | None = None,
    input: str | None = None,
    line_prefixed: bool = False,
) -> None:
    """Run a docker command streaming output, with stdout redirected to stderr.

    Used for commands whose stdout would otherwise pollute the strict I/O
    contract (build progress, push progress, stack deploy diagnostics). When
    `input` is provided, it is piped on stdin (e.g. compose YAML for
    `docker stack deploy -c -`).

    When `line_prefixed=True`, output is read line by line and
    `output.get_prefix()` is prepended to each line before writing.
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
        prefix = get_prefix()
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
            env=docker_env(host),
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
        env=docker_env(host),
    )
    if result.returncode != 0:
        raise DockerError(cmd, result.returncode, "")


def manifest_exists(image: str, host: str | None = None) -> bool:
    """Check if a Docker image manifest exists in a registry."""
    result = run("manifest", "inspect", image, host=host, check=False)
    return result.returncode == 0


def build(tag: str, context_dir: str, host: str | None = None) -> None:
    """Build a Docker image, streaming output to stderr.

    Stdout is redirected to stderr to keep the strict I/O contract: build
    diagnostics are not pipeable data.
    """
    stream("build", "-t", tag, context_dir, host=host)


def push(image: str, host: str | None = None) -> None:
    """Push a Docker image to a registry, streaming output to stderr."""
    stream("push", image, host=host)
