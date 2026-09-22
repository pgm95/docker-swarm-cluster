"""Validate Compose projects without touching any daemon.

Per project: ``compose config --format json`` renders the document on the
client (interpolation, includes, schema), then every ``build.context`` in the
rendered services must exist on disk. Prints ``✓``/``✗ <name>`` to stderr,
one line per project, and exits non-zero if any failed. Backs the
``validate:compose`` task and the ``validate-compose`` pre-commit hook.
"""

import argparse
import json
import sys
from pathlib import Path

from core import DockerError, engine
from core.cli import cli_main
from core.output import error, info

from . import ToolError, ValidationError
from ._project import all_projects, project_name, resolve_project
from .deploy import compose_args


def render(project_dir: Path) -> dict:
    """Rendered compose document as a dict; raises ValidationError on a bad document."""
    try:
        result = engine.run(*compose_args(project_dir, "config", "--format", "json"))
    except DockerError as e:
        raise ValidationError(f"compose config failed:\n{e.stderr}") from e
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ValidationError(f"compose config produced invalid JSON: {e}") from e


def missing_build_contexts(project_dir: Path, rendered: dict) -> list[str]:
    """``<service>: <context>`` for every build context that is not a directory."""
    missing = []
    for name, svc in (rendered.get("services") or {}).items():
        build = svc.get("build")
        if not build:
            continue
        context = build if isinstance(build, str) else build.get("context", ".")
        path = Path(context)
        if not path.is_absolute():
            path = project_dir / path
        if not path.is_dir():
            missing.append(f"{name}: {context}")
    return missing


def validate_project(project_dir: Path) -> bool:
    """Validate one project; True on success. Diagnostics go to stderr."""
    name = project_name(project_dir)
    try:
        rendered = render(project_dir)
        missing = missing_build_contexts(project_dir, rendered)
        if missing:
            raise ValidationError("build contexts not found:\n" + "\n".join(f"  {m}" for m in missing))
    except ValidationError as e:
        error(f"✗ {name}\n{e}")
        return False
    info(f"✓ {name}")
    return True


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="compose.validate")
        parser.add_argument("projects", nargs="*", help="Project names (host/project) or paths; all when omitted")
        args = parser.parse_args()
        try:
            dirs = [resolve_project(p) for p in args.projects] if args.projects else all_projects()
        except ToolError as e:
            error(str(e))
            return 1
        if not dirs:
            info("No compose projects found")
            return 0
        results = [validate_project(d) for d in dirs]
        failed = results.count(False)
        if failed:
            error(f"{failed} of {len(results)} project(s) failed validation")
            return 1
        return 0
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
