"""Compose config preprocessing.

Concatenates centralized anchors with a stack's compose file,
then runs docker compose config.

Also callable as: python3 -m swarm._compose <stack-file> [extra-args...]
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import DockerError
from ._output import log, setup
from ._stack import stack_name

# docker compose config stringifies certain integer fields that
# docker stack deploy requires as raw integers.
_QUOTED_INT_FIELDS = re.compile(r"((?:published|size): )\"(\d+)\"")

SHARED_ANCHORS = Path("stacks/_shared/anchors.yml")


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

    Concatenates anchors.yml + compose file into a temp file, then runs
    docker compose config with proper project settings. Applies fixups
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

    anchors_content = SHARED_ANCHORS.read_text()
    compose_content = stack_file.read_text()

    fd, tmp_path = tempfile.mkstemp(suffix=".yml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(anchors_content)
            f.write("\n")
            f.write(compose_content)

        cmd = [
            "docker", "compose",
            "--project-directory", str(stack_dir),
            "--project-name", name,
            "-f", tmp_path,
            "config",
            *extra_args,
        ]
        log.debug("$ %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise DockerError(cmd, result.returncode, result.stderr.strip())
        return _fixup_config(result.stdout)
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    setup()
    parser = argparse.ArgumentParser(prog="swarm._compose")
    parser.add_argument("stack_file", help="Path to stack's compose.yml")
    parser.add_argument("extra", nargs="*", help="Additional args for docker compose config")
    args = parser.parse_args()
    try:
        print(compose_config(args.stack_file, *args.extra), end="")
    except DockerError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
