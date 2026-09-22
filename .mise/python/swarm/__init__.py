"""swarm: Docker Swarm stack management (deploy, remove, status, cleanup, validation).

Shared plumbing lives in ``core``; the exceptions are re-exported here under
the names the Swarm modules and their tests use. ``SwarmError`` is the
``core.ToolError`` base, so a ``DockerError`` raised by the engine is still
caught by ``except SwarmError``.
"""

from core import DockerError, SopsError, SSHError, ToolError, ValidationError

SwarmError = ToolError


class SecretError(SwarmError):
    """Secret validation or creation failure."""


__all__ = ["DockerError", "SSHError", "SecretError", "SopsError", "SwarmError", "ValidationError"]
