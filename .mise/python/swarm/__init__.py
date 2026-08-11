"""swarm — Python library for Docker Swarm cluster management."""


class SwarmError(Exception):
    """Base exception for all swarm operations."""


class DockerError(SwarmError):
    """Docker CLI command failure."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        cmd_str = " ".join(cmd)
        super().__init__(f"docker command failed (exit {returncode}): {cmd_str}\n{stderr}")


class SSHError(SwarmError):
    """SSH command failure."""

    def __init__(self, hostname: str, returncode: int, stderr: str):
        self.hostname = hostname
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"SSH to {hostname} failed (exit {returncode}): {stderr}")


class SopsError(SwarmError):
    """SOPS decryption failure."""


class SecretError(SwarmError):
    """Secret validation or creation failure."""


class ValidationError(SwarmError):
    """Compose or config validation failure."""
