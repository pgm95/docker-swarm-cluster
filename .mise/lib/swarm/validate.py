"""Compose validation and bind mount checks."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import SwarmError
from ._compose import compose_config
from ._output import error, setup
from ._ssh import ssh_node
from .deploy import compute_content_hash
from .nodes import get_service_node


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


def check_bind_mounts(stack_file: Path) -> list[dict]:
    """Full bind mount check for a stack.

    Returns:
        [{"stack", "node", "path", "status": "ok"|"missing"|"unreachable", "permissions"}]
    """
    try:
        raw_json = compose_config(str(stack_file), "--format", "json")
        compose_json = json.loads(raw_json)
    except Exception:
        return []

    binds = extract_bind_mounts(compose_json)
    if not binds:
        return []

    # Resolve service->node mapping
    try:
        svc_nodes = dict(get_service_node(str(stack_file)))
    except Exception:
        svc_nodes = {}

    # Group paths by target node, deduplicating
    node_paths: dict[str, set[str]] = {}
    for svc, paths in binds.items():
        node = svc_nodes.get(svc, "UNKNOWN")
        node_paths.setdefault(node, set()).update(paths)

    stack_nm = str(stack_file.parent)
    results = []
    for node, paths in sorted(node_paths.items()):
        if node in ("UNKNOWN", "UNRESOLVED"):
            for p in sorted(paths):
                results.append({"stack": stack_nm, "node": node, "path": p, "status": "unreachable", "permissions": ""})
            continue

        stat_cmd = "; ".join(
            f"if [ -e '{p}' ]; then stat -c '%A %U:%G {p}' '{p}'; else echo 'MISSING {p}'; fi"
            for p in sorted(paths)
        )
        try:
            result = ssh_node(node, stat_cmd, check=False)
            for line in result.stdout.strip().splitlines():
                if line.startswith("MISSING"):
                    path = line.split(" ", 1)[1] if " " in line else line
                    results.append({"stack": stack_nm, "node": node, "path": path, "status": "missing", "permissions": ""})
                else:
                    parts = line.rsplit(" ", 1)
                    perms = parts[0] if len(parts) > 1 else ""
                    path = parts[-1]
                    results.append({"stack": stack_nm, "node": node, "path": path, "status": "ok", "permissions": perms})
        except Exception:
            for p in sorted(paths):
                results.append({"stack": stack_nm, "node": node, "path": p, "status": "unreachable", "permissions": ""})

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

    # --- Bind mount checks ---
    print()
    print("=== Bind mount paths ===")
    warnings = 0
    for f in files:
        results = check_bind_mounts(f)
        if not results:
            continue
        # Group by node for display
        current_stack = ""
        for r in results:
            header = f"{r['stack']} ({r['node']}):"
            if header != current_stack:
                current_stack = header
                print(header)
            if r["status"] == "missing":
                print(f"  ⚠ MISSING {r['path']}")
                warnings += 1
            elif r["status"] == "unreachable":
                print(f"  ⚠ {r['node']}: unreachable")
                warnings += 1
            else:
                print(f"  {r['permissions']}")

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
