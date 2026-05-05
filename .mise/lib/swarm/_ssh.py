"""SSH execution helpers for swarm nodes."""

import os
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from . import SSHError
from ._output import log

_T = TypeVar("_T")
_R = TypeVar("_R")

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


def parallel_run(
    items: list[_T],
    fn: Callable[[_T], _R],
    *,
    max_workers: int = 4,
) -> dict[_T, _R]:
    """Run ``fn(item)`` in parallel across ``items`` and return a per-item map.

    Used for cluster-wide fan-out: SSH to every node, render every stack's
    compose, etc. Worker count is capped at ``max_workers`` (default 4) or
    ``len(items)``, whichever is smaller. The returned mapping preserves
    item identity regardless of which future completes first.

    ``fn`` should swallow per-item failures and return whatever the caller
    treats as a "failed" sentinel (False, None, empty result), since the
    fan-out itself doesn't try to interpret per-item results.
    """
    if not items:
        return {}
    with ThreadPoolExecutor(max_workers=min(len(items), max_workers)) as ex:
        futures = {ex.submit(fn, item): item for item in items}
        return {futures[fut]: fut.result() for fut in futures}
