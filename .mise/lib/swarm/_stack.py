"""Stack name resolution and discovery."""

import re
from pathlib import Path

from . import SwarmError

_NN_PREFIX = re.compile(r"^\d{2}_")
_NAMESPACES = ["stacks/infra", "stacks/apps"]


def stack_name(path: str | Path) -> str:
    """Derive the Swarm stack name from a stack directory path.

    Strips optional NN_ numeric prefix from the folder basename.
    """
    name = Path(path).name
    return _NN_PREFIX.sub("", name)


def resolve_stack_path(name_or_path: str) -> Path:
    """Resolve a stack identifier to its directory path.

    Accepts:
        - Full/relative path: stacks/infra/40_metrics
        - Directory name with prefix: 40_metrics
        - Bare stack name: metrics
    """
    p = Path(name_or_path)
    if p.is_dir():
        return p

    for ns in _NAMESPACES:
        ns_path = Path(ns)
        if not ns_path.is_dir():
            continue
        for d in ns_path.iterdir():
            if not d.is_dir():
                continue
            if d.name == name_or_path:
                return d
            if stack_name(d) == name_or_path:
                return d

    raise SwarmError(f"Stack not found: {name_or_path}")


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
