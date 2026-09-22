"""core: what the Swarm and Compose tooling share.

Docker CLI plumbing (``engine``), SOPS decryption (``sops``), the strict
stdout/stderr output contract (``output``), the CLI entry-point wrapper
(``cli``) and the exception hierarchy below. Nothing in here knows about
stacks, projects or where a daemon lives; callers pass the host explicitly.
"""


class ToolError(Exception):
    """Base exception for every tooling failure; ``cli_main`` formats it."""


class DockerError(ToolError):
    """Docker CLI command failure."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        cmd_str = " ".join(cmd)
        super().__init__(f"docker command failed (exit {returncode}): {cmd_str}\n{stderr}")


class SSHError(ToolError):
    """SSH command failure."""

    def __init__(self, hostname: str, returncode: int, stderr: str):
        self.hostname = hostname
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"SSH to {hostname} failed (exit {returncode}): {stderr}")


class SopsError(ToolError):
    """SOPS decryption failure."""


class ValidationError(ToolError):
    """Compose or config validation failure."""
