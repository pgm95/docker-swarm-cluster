"""Remove a deployed Compose project from its host."""

import argparse
import sys

from core import DockerError, engine
from core.cli import cli_main
from core.output import error, info, set_prefix

from . import ToolError
from ._project import project_host, project_name, resolve_project
from .deploy import compose_args


def remove_project(ref: str, volumes: bool = False) -> int:
    """``compose down`` for one project. Returns 0 on success, 1 on failure."""
    try:
        project_dir = resolve_project(ref)
        name = project_name(project_dir)
        set_prefix(name)
        host = project_host(project_dir)
    except ToolError as e:
        error(str(e))
        return 1

    info(f"Removing: {name} on {host}")
    args = ["down", "--remove-orphans"]
    if volumes:
        args.append("--volumes")
    try:
        engine.stream(*compose_args(project_dir, *args), host=host, line_prefixed=True)
    except DockerError as e:
        error(f"compose down failed (exit {e.returncode})")
        return 1
    info(f"Removed: {name}")
    return 0


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="compose.remove")
        parser.add_argument("project", help="Project name (host/project) or path")
        parser.add_argument("--volumes", action="store_true", help="Also remove named volumes")
        args = parser.parse_args()
        return remove_project(args.project, args.volumes)
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
