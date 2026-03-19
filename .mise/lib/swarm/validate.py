"""Compose validation and bind mount checks."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import SwarmError
from ._compose import compose_config
from ._docker import inspect_nodes
from ._output import error, setup
from ._ssh import ssh_node
from .deploy import compute_content_hash
from .nodes import resolve_service_nodes


def validate_compose(stack_file: Path) -> tuple[bool, str]:
    """Run full Swarm validation pipeline for a single compose file.

    compose_config -> sed transforms -> docker stack config

    Returns:
        (success, error_output)
    """
    try:
        raw = compose_config(str(stack_file))
    except Exception as e:
        return False, str(e)

    # Apply the same sed transforms as the deploy pipeline
    transformed = _sed_transforms(raw)

    result = subprocess.run(
        ["docker", "stack", "config", "-c", "-"],
        input=transformed,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, ""


def validate_all_compose() -> list[dict]:
    """Discover and validate all compose.yml files.

    Sets up OCI_TAG_* env vars for stacks with build contexts.

    Returns:
        [{"file": str, "valid": bool, "error": str}]
    """
    import os

    # Export OCI_TAG_* for stacks with build dirs
    for compose_file in _find_all_compose():
        stack_dir = compose_file.parent
        build_root = stack_dir / "build"
        if not build_root.is_dir():
            continue
        for svc_dir in sorted(build_root.iterdir()):
            if svc_dir.is_dir():
                tag = compute_content_hash(svc_dir)
                var = f"OCI_TAG_{svc_dir.name.upper()}"
                os.environ[var] = tag

    results = []
    for compose_file in _find_all_compose():
        valid, err = validate_compose(compose_file)
        results.append({"file": str(compose_file), "valid": valid, "error": err})
    return results


def extract_bind_mounts(compose_json: dict) -> dict[str, list[str]]:
    """Extract bind mount source paths grouped by service name.

    Pure function — takes parsed compose JSON.

    Returns:
        {service_name: [source_paths]}
    """
    result = {}
    for svc_name, svc in compose_json.get("services", {}).items():
        paths = []
        for v in svc.get("volumes", []):
            if isinstance(v, dict) and v.get("type") == "bind":
                paths.append(v["source"])
        if paths:
            result[svc_name] = paths
    return result


def collect_bind_mounts(
    stack_file: Path, raw_nodes: list[dict],
) -> dict[str, set[str]]:
    """Collect bind mount paths grouped by node for a stack.

    Returns:
        {node_hostname: {path, ...}} — empty dict if no bind mounts.
    """
    try:
        raw_json = compose_config(str(stack_file), "--format", "json")
        compose_json = json.loads(raw_json)
    except Exception:
        return {}

    binds = extract_bind_mounts(compose_json)
    if not binds:
        return {}

    try:
        svc_nodes = dict(resolve_service_nodes(compose_json, raw_nodes))
    except Exception:
        svc_nodes = {}

    node_paths: dict[str, set[str]] = {}
    for svc, paths in binds.items():
        node = svc_nodes.get(svc, "UNKNOWN")
        node_paths.setdefault(node, set()).update(paths)
    return node_paths


def check_paths_on_node(node: str, paths: set[str]) -> list[dict]:
    """SSH to a node and stat all paths in a single call.

    Returns:
        [{"path", "status": "ok"|"missing", "permissions"}]
    """
    stat_cmd = "; ".join(
        f"if [ -e '{p}' ]; then stat -c '%A %U:%G {p}' '{p}'; else echo 'MISSING {p}'; fi"
        for p in sorted(paths)
    )
    try:
        result = ssh_node(node, stat_cmd, check=False)
    except Exception:
        return [{"path": p, "status": "unreachable", "permissions": ""} for p in sorted(paths)]

    results = []
    for line in result.stdout.strip().splitlines():
        if line.startswith("MISSING"):
            path = line.split(" ", 1)[1] if " " in line else line
            results.append({"path": path, "status": "missing", "permissions": ""})
        else:
            parts = line.rsplit(" ", 1)
            perms = parts[0] if len(parts) > 1 else ""
            path = parts[-1]
            results.append({"path": path, "status": "ok", "permissions": perms})
    return results


def validate(stack_file: str | None = None) -> int:
    """Full validation run. Returns exit code."""
    # --- Compose validation ---
    if stack_file:
        files = [Path(stack_file)]
    else:
        files = _find_all_compose()

    import os

    # Set OCI_TAG_* for stacks with build dirs
    for f in files:
        build_root = f.parent / "build"
        if not build_root.is_dir():
            continue
        for svc_dir in sorted(build_root.iterdir()):
            if svc_dir.is_dir():
                tag = compute_content_hash(svc_dir)
                os.environ[f"OCI_TAG_{svc_dir.name.upper()}"] = tag

    failed = False
    for f in files:
        valid, err = validate_compose(f)
        if valid:
            print(f"✓ {f}")
        else:
            print(f"✗ {f}")
            if err:
                for line in err.splitlines()[:5]:
                    print(f"  {line}")
            failed = True

    if failed:
        return 1

    # --- Bind mount checks (1 inspect_nodes + 1 SSH per node) ---
    print()
    print("=== Bind mount paths ===")

    raw_nodes = inspect_nodes()

    # Collect all bind mounts across all stacks, grouped by node
    # Track which stack+node owns each path for display
    all_node_paths: dict[str, set[str]] = {}
    path_owners: dict[tuple[str, str], str] = {}  # (node, path) -> stack
    for f in files:
        stack_nm = str(f.parent)
        node_paths = collect_bind_mounts(f, raw_nodes)
        for node, paths in node_paths.items():
            all_node_paths.setdefault(node, set()).update(paths)
            for p in paths:
                path_owners[(node, p)] = stack_nm

    # One SSH call per node for all paths
    node_results: dict[str, list[dict]] = {}
    for node, paths in sorted(all_node_paths.items()):
        if node in ("UNKNOWN", "UNRESOLVED"):
            node_results[node] = [{"path": p, "status": "unreachable", "permissions": ""} for p in sorted(paths)]
        else:
            node_results[node] = check_paths_on_node(node, paths)

    # Display grouped by stack and node
    warnings = 0
    displayed: set[str] = set()
    for f in files:
        stack_nm = str(f.parent)
        node_paths = collect_bind_mounts(f, raw_nodes)
        for node in sorted(node_paths):
            header = f"{stack_nm} ({node}):"
            header_printed = False
            for r in node_results.get(node, []):
                if path_owners.get((node, r["path"])) != stack_nm:
                    continue
                display_key = f"{stack_nm}:{node}:{r['path']}"
                if display_key in displayed:
                    continue
                displayed.add(display_key)
                if not header_printed:
                    print(header)
                    header_printed = True
                if r["status"] == "missing":
                    print(f"  ⚠ MISSING {r['path']}")
                    warnings += 1
                elif r["status"] == "unreachable":
                    print(f"  ⚠ {node}: unreachable")
                    warnings += 1
                else:
                    print(f"  {r['permissions']} {r['path']}")

    if warnings > 0:
        print()
        print(f"{warnings} warning(s) — missing bind mount paths.")

    return 0


def _sed_transforms(config: str) -> str:
    """Apply the same transforms as the deploy pipeline's sed command."""
    import re
    lines = []
    for line in config.splitlines():
        if line.strip().startswith("name:") and not line.startswith(" "):
            continue
        line = re.sub(r'published: "(\d+)"', r"published: \1", line)
        lines.append(line)
    return "\n".join(lines)


def _find_all_compose() -> list[Path]:
    """Find all compose.yml files in stacks/."""
    return sorted(Path("stacks").rglob("compose.yml"))


def main() -> int:
    setup()
    parser = argparse.ArgumentParser(prog="swarm.validate")
    parser.add_argument("--stack", help="Validate a single compose file")

    args = parser.parse_args()
    try:
        return validate(args.stack)
    except SwarmError as e:
        error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
