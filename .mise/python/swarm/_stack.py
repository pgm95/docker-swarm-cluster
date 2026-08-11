"""Stack name resolution and discovery."""

import os
import re
from pathlib import Path

from . import SwarmError

_NN_PREFIX = re.compile(r"^\d{2}_")


def stacks_root() -> Path:
    """Root of the stacks tree.

    Configurable via the ``SWARM_STACKS_DIR`` environment variable. Defaults
    to ``stacks`` (relative to the current working directory). Mise tasks
    set CWD to the project root, so the default works for the canonical
    layout.
    """
    return Path(os.environ.get("SWARM_STACKS_DIR", "stacks"))


def find_namespaces() -> list[Path]:
    """Enumerate namespace directories under the stacks root.

    A namespace is any immediate subdirectory whose name does not start with
    an underscore. The underscore convention reserves names like
    ``_shared``, ``_archive``, ``_anchors`` for project-internal use without
    needing a hardcoded skip list.
    """
    root = stacks_root()
    if not root.is_dir():
        return []
    return sorted(
        d for d in root.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )


def stack_name(path: str | Path) -> str:
    """Derive the Swarm stack name from a stack directory path.

    Strips optional NN_ numeric prefix from the folder basename.
    """
    name = Path(path).name
    return _NN_PREFIX.sub("", name)


def oci_tag_var(service: str) -> str:
    """Env-var name carrying the content-hash tag for a build/<service>/ context.

    The deploy pipeline exports ``OCI_TAG_<UPPER>`` per discovered build dir
    so compose interpolation can substitute the correct tag in the service's
    image string. Hyphens become underscores because env-var names cannot
    contain hyphens. Single source of truth for this formula — both
    ``deploy.discover_build_dirs`` and ``validate._set_oci_tags`` use it.
    """
    return f"OCI_TAG_{service.upper().replace('-', '_')}"


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

    for ns_path in find_namespaces():
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
