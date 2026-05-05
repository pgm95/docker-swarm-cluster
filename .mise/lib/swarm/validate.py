"""Compose validation and bind mount checks."""

import argparse
import os
import shlex
import sys
from pathlib import Path

from . import SwarmError, ValidationError, _docker
from ._cli import cli_main
from ._compose import compose_config, compose_json
from ._docker import inspect_nodes
from ._output import info, warn
from ._ssh import parallel_run, ssh_node
from ._stack import find_namespaces, find_stacks, oci_tag_var
from .deploy import compute_content_hash
from .nodes import resolve_service_nodes
from .secrets import validate_config_files


def _set_oci_tags(stack_dir: Path) -> None:
    """Export OCI_TAG_<SERVICE> for each build/<service>/ dir.

    Validation needs to see the same env vars that deploy will produce so
    compose interpolation resolves identically. The shared :func:`oci_tag_var`
    formula keeps the two paths in lock-step.
    """
    build_root = stack_dir / "build"
    if not build_root.is_dir():
        return
    for svc_dir in sorted(build_root.iterdir()):
        if svc_dir.is_dir():
            os.environ[oci_tag_var(svc_dir.name)] = compute_content_hash(svc_dir)


def validate_compose(stack_file: Path, yaml_text: str | None = None) -> tuple[bool, str]:
    """Run full Swarm validation pipeline for a single compose file.

    `yaml_text` may be passed in to skip a redundant `compose_config` call
    when the caller has already produced the preprocessed YAML.

    Returns:
        (success, error_output)
    """
    try:
        if yaml_text is None:
            yaml_text = compose_config(str(stack_file))
    except Exception as e:
        return False, str(e)

    try:
        result = _docker.run("stack", "config", "-c", "-", input=yaml_text, check=False)
    except SwarmError as e:
        return False, str(e)
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, ""


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
    stack_file: Path,
    raw_nodes: list[dict],
    rendered: dict | None = None,
) -> dict[str, set[str]]:
    """Collect bind mount paths grouped by node for a stack.

    `rendered` may be passed in to reuse a previously-parsed compose
    document and skip a redundant `docker compose config --format json`
    invocation.

    Returns:
        {node_hostname: {path, ...}} — empty dict if no bind mounts.
    """
    if rendered is None:
        try:
            rendered = compose_json(stack_file)
        except Exception:
            return {}
    if rendered is None:
        return {}

    binds = extract_bind_mounts(rendered)
    if not binds:
        return {}

    try:
        svc_nodes = dict(resolve_service_nodes(rendered, raw_nodes))
    except Exception:
        svc_nodes = {}

    node_paths: dict[str, set[str]] = {}
    for svc, paths in binds.items():
        # Use the same sentinel as nodes.resolve_service_nodes — single
        # canonical "we couldn't determine the target node" marker.
        node = svc_nodes.get(svc, "UNRESOLVED")
        node_paths.setdefault(node, set()).update(paths)
    return node_paths


def check_paths_on_node(node: str, paths: set[str]) -> list[dict]:
    """SSH to a node and stat all paths in a single call.

    Each path produces exactly one line on stdout: either ``%A:%U:%G`` from
    stat (path exists) or the literal string ``MISSING``. We rely on
    positional alignment between the input path list and the output line
    list — if a shell error truncates the chain mid-execution, the tail of
    paths comes back unmatched. Those tail paths are reported as
    ``unreachable`` rather than silently dropped (which would happen if we
    just ``zip``ped the two lists).

    Returns:
        [{"path", "status": "ok"|"missing"|"unreachable", "permissions"}]
    """
    sorted_paths = sorted(paths)
    parts = []
    for p in sorted_paths:
        qp = shlex.quote(p)
        parts.append(f"if [ -e {qp} ]; then stat -c %A:%U:%G {qp}; else echo MISSING; fi")
    stat_cmd = "; ".join(parts)
    try:
        result = ssh_node(node, stat_cmd, check=False)
    except Exception:
        return [{"path": p, "status": "unreachable", "permissions": ""} for p in sorted_paths]

    out_lines = result.stdout.strip().splitlines()
    results = []
    for i, path in enumerate(sorted_paths):
        if i >= len(out_lines):
            results.append({"path": path, "status": "unreachable", "permissions": ""})
            continue
        line = out_lines[i]
        if line == "MISSING":
            results.append({"path": path, "status": "missing", "permissions": ""})
        else:
            results.append({"path": path, "status": "ok", "permissions": line})
    return results


def validate(stack_file: str | None = None) -> int:
    """Full validation run. Returns exit code."""
    files = [Path(stack_file)] if stack_file else _find_all_compose()

    # Set OCI_TAG_* once per stack with build dirs (deploy.py uses the same
    # formula; consistent env between validate and deploy is mandatory).
    for f in files:
        _set_oci_tags(f.parent)

    # Per-file caches: compose YAML (for stack config) and parsed JSON
    # (for bind-mount extraction). Each file produces at most one YAML and
    # one JSON compose_config call instead of three.
    yaml_cache: dict[Path, str] = {}
    json_cache: dict[Path, dict] = {}

    failed = False
    for f in files:
        try:
            yaml_cache[f] = compose_config(str(f))
        except Exception as e:
            info(f"✗ {f}")
            info(f"  {e}")
            failed = True
            continue

        valid, err = validate_compose(f, yaml_text=yaml_cache[f])
        if valid:
            info(f"✓ {f}")
        else:
            info(f"✗ {f}")
            if err:
                for line in err.splitlines()[:5]:
                    info(f"  {line}")
            failed = True
            continue

        try:
            json_cache[f] = compose_json(f)
        except Exception:
            json_cache[f] = {}
            continue

        # Verify any `configs.<x>.file:` paths actually exist on disk.
        try:
            validate_config_files(json_cache[f])
        except ValidationError as e:
            info(f"✗ {f}")
            for line in str(e).splitlines()[:5]:
                info(f"  {line}")
            failed = True

    if failed:
        return 1

    # --- Bind mount checks ---
    info("")
    info("=== Bind mount paths ===")

    raw_nodes = inspect_nodes()

    all_node_paths: dict[str, set[str]] = {}
    path_owners: dict[tuple[str, str], str] = {}
    per_stack_paths: dict[Path, dict[str, set[str]]] = {}
    for f in files:
        stack_nm = str(f.parent)
        node_paths = collect_bind_mounts(f, raw_nodes, rendered=json_cache.get(f))
        per_stack_paths[f] = node_paths
        for node, paths in node_paths.items():
            all_node_paths.setdefault(node, set()).update(paths)
            for p in paths:
                path_owners[(node, p)] = stack_nm

    # Parallel SSH across reachable nodes; unreachable/unknown nodes get a
    # synthetic result without a network call.
    reachable = {n: p for n, p in all_node_paths.items() if n != "UNRESOLVED"}
    node_results: dict[str, list[dict]] = {
        node: [{"path": p, "status": "unreachable", "permissions": ""} for p in sorted(paths)]
        for node, paths in all_node_paths.items()
        if node not in reachable
    }
    node_results.update(parallel_run(
        list(reachable.keys()),
        lambda node: check_paths_on_node(node, reachable[node]),
    ))

    # Display grouped by stack and node
    warnings = 0
    displayed: set[str] = set()
    for f in files:
        stack_nm = str(f.parent)
        node_paths = per_stack_paths[f]
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
                    info(header)
                    header_printed = True
                if r["status"] == "missing":
                    warn(f"MISSING {r['path']}")
                    warnings += 1
                elif r["status"] == "unreachable":
                    warn(f"{node}: unreachable")
                    warnings += 1
                else:
                    info(f"  {r['permissions']} {r['path']}")

    if warnings > 0:
        info("")
        warn(f"{warnings} warning(s) — missing bind mount paths.")

    return 0


def _find_all_compose() -> list[Path]:
    """Find every stack's compose.yml across all namespaces under the stacks root."""
    composes: list[Path] = []
    for ns in find_namespaces():
        for stack_dir in find_stacks(ns):
            compose = stack_dir / "compose.yml"
            if compose.is_file():
                composes.append(compose)
    return composes


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="swarm.validate")
        parser.add_argument("--stack", help="Validate a single compose file")
        args = parser.parse_args()
        return validate(args.stack)
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
