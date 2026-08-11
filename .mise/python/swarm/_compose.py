"""Compose config preprocessing.

Concatenates centralized anchors with a stack's compose file, pipes the
result through `docker compose config` on stdin, and applies fixups for
`docker stack deploy` compatibility.

Also callable as: python3 -m swarm._compose <stack-file> [extra-args...]
"""

import argparse
import json
import os
import re
import sys
from functools import cache
from pathlib import Path

from . import _docker
from ._cli import cli_main
from ._stack import stack_name

# docker compose config stringifies certain integer fields that
# docker stack deploy requires as raw integers.
_QUOTED_INT_FIELDS = re.compile(r"((?:published|size): )\"(\d+)\"")


def _anchors_path() -> Path:
    """Path to the shared YAML anchors file.

    Configurable via the ``SWARM_ANCHORS_FILE`` environment variable.
    Defaults to ``stacks/_shared/anchors.yml`` for the canonical layout.
    Missing files are tolerated by ``_anchors_content`` (returns empty).
    """
    return Path(os.environ.get("SWARM_ANCHORS_FILE", "stacks/_shared/anchors.yml"))


@cache
def _read_anchors_file(path: Path) -> str:
    """Read an anchors file once per (process, path) pair.

    Cache is keyed on the resolved path so a mid-process change to
    ``SWARM_ANCHORS_FILE`` would correctly read the new file. A missing
    file is not an error — projects without shared anchors get an empty
    prefix and the stack's own ``compose.yml`` is rendered as-is.
    """
    return path.read_text() if path.is_file() else ""


def _anchors_content() -> str:
    """Current shared anchors content (or empty string if no file)."""
    return _read_anchors_file(_anchors_path())


def _fixup_config(config: str) -> str:
    """Fix docker compose config output for docker stack deploy compatibility.

    Strips the top-level 'name:' key (rejected by stack deploy) and unquotes
    integer fields that compose config erroneously serializes as strings.
    """
    lines = []
    for line in config.splitlines():
        if line.strip().startswith("name:") and not line.startswith(" "):
            continue
        line = _QUOTED_INT_FIELDS.sub(r"\1\2", line)
        lines.append(line)
    return "\n".join(lines)


def compose_config(stack_file: str | Path, *extra_args: str) -> str:
    """Preprocess a compose file through docker compose config.

    Concatenates anchors.yml + compose file in memory, pipes the combined
    YAML to `docker compose -f - config` on stdin, then applies fixups
    for docker stack deploy compatibility.

    Args:
        stack_file: Path to the stack's compose.yml.
        *extra_args: Additional args for docker compose config (e.g., '--format', 'json').

    Returns:
        The resolved compose config as a string.
    """
    stack_file = Path(stack_file)
    stack_dir = stack_file.parent
    name = stack_name(stack_dir)

    combined = _anchors_content() + "\n" + stack_file.read_text()

    result = _docker.run(
        "compose",
        "--project-directory", str(stack_dir),
        "--project-name", name,
        "-f", "-",
        "config",
        *extra_args,
        input=combined,
    )
    return _fixup_config(result.stdout)


def compose_json(stack_file: str | Path) -> dict:
    """Render the stack's compose document and return it as a parsed dict.

    Convenience wrapper around ``compose_config(stack_file, "--format", "json")``
    plus ``json.loads()``. This is the canonical entry point for code that
    needs to inspect the rendered compose structurally — discovery of
    versioned secrets, configs to validate, external networks, bind mounts,
    placement constraints, and so on.

    Use ``compose_config(stack_file)`` directly only when you need the YAML
    text (e.g., to feed ``docker stack deploy -c -``).
    """
    return json.loads(compose_config(stack_file, "--format", "json"))


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="swarm._compose")
        parser.add_argument("stack_file", help="Path to stack's compose.yml")
        parser.add_argument("extra", nargs="*", help="Additional args for docker compose config")
        args = parser.parse_args()
        print(compose_config(args.stack_file, *args.extra), end="")
        return 0
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
