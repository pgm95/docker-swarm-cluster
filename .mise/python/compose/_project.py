"""Project discovery, resolution and host lookup."""

import os
from pathlib import Path

from . import ToolError

COMPOSE_FILE = "compose.yml"


def projects_root() -> Path:
    """Root of the projects tree (``COMPOSE_PROJECTS_DIR``, default ``compose``)."""
    return Path(os.environ.get("COMPOSE_PROJECTS_DIR", "compose"))


def all_projects() -> list[Path]:
    """Every project directory, sorted by host then project.

    A project is ``<root>/<host>/<project>/`` holding a ``compose.yml``.
    Hosts starting with an underscore are skipped (same convention as the
    Swarm stacks tree).
    """
    root = projects_root()
    if not root.is_dir():
        return []
    return sorted(
        d
        for host in root.iterdir()
        if host.is_dir() and not host.name.startswith("_")
        for d in host.iterdir()
        if (d / COMPOSE_FILE).is_file()
    )


def project_name(path: str | Path) -> str:
    """``host/project`` for a project directory."""
    p = Path(path)
    return f"{p.parent.name}/{p.name}"


def host_role(path: str | Path) -> str:
    """The host role a project belongs to: its parent directory name."""
    return Path(path).parent.name


def resolve_project(ref: str) -> Path:
    """Resolve a ``host/project`` name or a directory path to the project directory.

    A path is accepted only when it sits under the projects root, so a
    Swarm stack path handed to a Compose task fails instead of being deployed
    to the wrong place.
    """
    for d in all_projects():
        if project_name(d) == ref:
            return d
    p = Path(ref)
    if p.is_dir() and (p / COMPOSE_FILE).is_file():
        try:
            p.resolve().relative_to(projects_root().resolve())
        except ValueError:
            raise ToolError(f"Not a compose project (outside {projects_root()}): {ref}") from None
        return p
    raise ToolError(f"Compose project not found: {ref}")


def host_env_var(role: str) -> str:
    """``COMPOSE_HOST_<ROLE>``: the profile variable holding a host's Docker URL."""
    return "COMPOSE_HOST_" + role.upper().replace("-", "_")


def project_host(path: str | Path) -> str:
    """Docker URL for the project's host from the active mise profile.

    Unset means the host has no counterpart in this environment; that is a
    hard error rather than a fallback, so a prod-only host is never deployed
    from the dev profile by accident.
    """
    role = host_role(path)
    var = host_env_var(role)
    url = os.environ.get(var, "")
    if not url:
        raise ToolError(f"{var} is not set: host {role!r} is not deployable in this environment")
    return url
