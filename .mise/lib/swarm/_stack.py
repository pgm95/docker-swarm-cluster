"""Stack name resolution and discovery."""

import re
from pathlib import Path

_NN_PREFIX = re.compile(r"^\d{2}_")


def stack_name(path: str | Path) -> str:
    """Derive the Swarm stack name from a stack directory path.

    Strips optional NN_ numeric prefix from the folder basename.
    """
    name = Path(path).name
    return _NN_PREFIX.sub("", name)


def find_stacks(namespace_dir: str | Path, reverse: bool = False) -> list[Path]:
    """Discover stack directories under a namespace, sorted by folder name.

    Args:
        namespace_dir: Path to namespace (e.g., "stacks/infra").
        reverse: Sort in reverse order (for teardown).

    Returns:
        Sorted list of stack directory Paths.
    """
    ns = Path(namespace_dir)
    if not ns.is_dir():
        return []
    dirs = [d for d in ns.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: d.name, reverse=reverse)
    return dirs
