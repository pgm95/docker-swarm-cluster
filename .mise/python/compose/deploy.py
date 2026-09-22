"""Deploy one Compose project to its host.

Pipeline, all from the operator's machine:
  1. resolve the project and its host from the profile
  2. ``compose config --quiet``: a client-side syntax gate, no daemon needed
  3. ``docker version`` against the host, so an unreachable daemon fails in
     seconds instead of hanging inside ``up``
  4. ``compose up -d --build --remove-orphans --wait``: images are built on
     the target daemon, ``--wait`` makes an unhealthy container a failed deploy
  5. ``compose ps`` table on stderr

Config and build contexts travel with the request; nothing is copied to the host.
"""

import argparse
import os
import sys
from pathlib import Path

from core import DockerError, engine
from core.cli import cli_main
from core.output import error, info, set_prefix, table

from . import ToolError
from ._project import project_host, project_name, resolve_project

COMPOSE_FILE = "compose.yml"


def compose_args(project_dir: Path, *args: str) -> list[str]:
    """``compose`` invocation pinned to the project directory and file."""
    return ["compose", "--project-directory", str(project_dir), "-f", str(project_dir / COMPOSE_FILE), *args]


def deploy_project(ref: str) -> int:
    """Deploy one project. Returns 0 on success, 1 on any failure."""
    try:
        project_dir = resolve_project(ref)
        name = project_name(project_dir)
        set_prefix(name)
        host = project_host(project_dir)
    except ToolError as e:
        error(str(e))
        return 1

    info(f"Deploying: {name} -> {host}")
    try:
        engine.run(*compose_args(project_dir, "config", "--quiet"))
    except DockerError as e:
        error(f"compose config failed:\n{e.stderr}")
        return 1

    try:
        engine.run("version", "--format", "{{.Server.Version}}", host=host)
    except DockerError as e:
        error(f"Docker host unreachable ({host}): {e.stderr}")
        return 1

    wait_timeout = os.environ.get("COMPOSE_WAIT_TIMEOUT", "120")
    try:
        engine.stream(
            *compose_args(
                project_dir,
                "up", "-d", "--build", "--remove-orphans",
                "--wait", "--wait-timeout", wait_timeout,
            ),
            host=host,
            line_prefixed=True,
        )
    except DockerError as e:
        error(f"compose up failed (exit {e.returncode})")
        return 1

    _print_containers(project_dir, host)
    info(f"Deployed: {name}")
    return 0


def _print_containers(project_dir: Path, host: str) -> None:
    result = engine.run(
        *compose_args(project_dir, "ps", "--all", "--format", "{{.Name}}\t{{.State}}\t{{.Health}}"),
        host=host, check=False,
    )
    rows = [line.split("\t") for line in result.stdout.strip().splitlines() if line.strip()]
    if rows:
        table(["CONTAINER", "STATE", "HEALTH"], rows, file=sys.stderr)


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="compose.deploy")
        parser.add_argument("project", help="Project name (host/project) or path")
        args = parser.parse_args()
        return deploy_project(args.project)
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
