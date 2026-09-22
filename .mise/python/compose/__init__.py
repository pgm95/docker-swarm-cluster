"""compose: standalone Docker Compose projects deployed to remote hosts over ssh.

A project lives at ``<COMPOSE_PROJECTS_DIR>/<host>/<project>/compose.yml``.
The directory name is the host role; the mise profile maps it to a Docker
URL through ``COMPOSE_HOST_<HOST>`` (hyphens to underscores, upper case), so
the same project directory deploys to a different daemon per environment.
Nothing is copied to the host: the local Compose client drives the remote
daemon, and ``build:`` contexts travel with the request.

Shared plumbing lives in ``core``.
"""

from core import DockerError, ToolError, ValidationError

__all__ = ["DockerError", "ToolError", "ValidationError"]
