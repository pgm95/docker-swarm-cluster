"""SSH execution helpers for swarm nodes."""

import os
import subprocess

from . import SSHError
from ._output import log

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
]


def ssh_node(
    hostname: str,
    command: str,
    stdin_data: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute a command on a swarm node via SSH.

    Args:
        hostname: Node hostname (resolved via Tailscale/DNS).
        command: Shell command string to execute remotely.
        stdin_data: If provided, piped to stdin (for docker login, etc.).
            When None, stdin is closed (-n flag equivalent).
        check: Raise SSHError on non-zero exit.

    Returns:
        CompletedProcess with stdout/stderr as strings.
    """
    user = os.environ.get("SWARM_SSH_USER", "root")
    cmd = ["ssh"]
    if stdin_data is None:
        cmd.append("-n")
    cmd.extend(SSH_OPTS)
    cmd.append(f"{user}@{hostname}")
    cmd.append(command)

    log.debug("$ ssh %s %s", hostname, command)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=stdin_data,
    )
    if check and result.returncode != 0:
        raise SSHError(hostname, result.returncode, result.stderr.strip())
    return result
