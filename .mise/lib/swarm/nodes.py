"""Node discovery and constraint matching."""

import argparse
import json
import sys

from . import SwarmError
from ._compose import compose_config
from ._docker import inspect_nodes
from ._output import setup


def get_swarm_nodes(filters: list[str] | None = None) -> list[dict]:
    """Discover swarm nodes with optional label filters.

    Args:
        filters: List of "key=value" strings. A node must match ALL filters.

    Returns:
        List of {"hostname": str, "labels": dict} dicts.
    """
    raw_nodes = inspect_nodes()
    result = []
    for node in raw_nodes:
        hostname = node["Description"]["Hostname"]
        labels = node["Spec"].get("Labels", {})
        if filters and not _match_labels(labels, filters):
            continue
        result.append({"hostname": hostname, "labels": labels})
    return result


def get_service_node(compose_file: str) -> list[tuple[str, str]]:
    """Match services to nodes based on placement constraints.

    Parses compose config JSON and node inspect data to determine
    which node each service will be scheduled on.

    Returns:
        List of (service_name, hostname|"UNRESOLVED") tuples.
    """
    compose_json = json.loads(compose_config(compose_file, "--format", "json"))
    raw_nodes = inspect_nodes()
    return resolve_service_nodes(compose_json, raw_nodes)


def resolve_service_nodes(
    compose_json: dict, raw_nodes: list[dict],
) -> list[tuple[str, str]]:
    """Match services to nodes from pre-fetched data.

    Returns:
        List of (service_name, hostname|"UNRESOLVED") tuples.
    """
    nodes = [
        {
            "hostname": n["Description"]["Hostname"],
            "labels": n["Spec"].get("Labels", {}),
            "role": n["Spec"].get("Role", ""),
        }
        for n in raw_nodes
    ]

    result = []
    for svc_name, svc in compose_json.get("services", {}).items():
        constraints = (svc.get("deploy") or {}).get("placement", {}).get("constraints", [])
        parsed = _parse_constraints(constraints)
        matched = _find_matching_node(parsed, nodes)
        result.append((svc_name, matched or "UNRESOLVED"))
    return result


def _match_labels(labels: dict, filters: list[str]) -> bool:
    """Check if node labels satisfy all filter conditions."""
    for f in filters:
        key, _, val = f.partition("=")
        if labels.get(key) != val:
            return False
    return True


def _parse_constraints(constraints: list[str]) -> list[tuple[str, str, str]]:
    """Parse Swarm placement constraint strings.

    Args:
        constraints: List of strings like "node.labels.type == vm".

    Returns:
        List of (field, operator, value) tuples.
    """
    parsed = []
    for c in constraints:
        parts = c.split()
        if len(parts) == 3:
            parsed.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return parsed


def _match_constraint(field: str, op: str, value: str, node: dict) -> bool:
    """Check if a single constraint matches a node."""
    if field.startswith("node.labels."):
        label_key = field[len("node.labels."):]
        actual = node["labels"].get(label_key, "")
    elif field == "node.role":
        actual = node["role"]
    elif field == "node.hostname":
        actual = node["hostname"]
    else:
        actual = ""

    if op == "==":
        return actual == value
    if op == "!=":
        return actual != value
    return False


def _find_matching_node(
    constraints: list[tuple[str, str, str]], nodes: list[dict],
) -> str | None:
    """Find the first node that satisfies all constraints."""
    for node in nodes:
        if all(_match_constraint(f, o, v, node) for f, o, v in constraints):
            return node["hostname"]
    return None


def main() -> int:
    setup()
    parser = argparse.ArgumentParser(prog="swarm.nodes")
    sub = parser.add_subparsers(dest="command")

    list_cmd = sub.add_parser("list", help="List swarm nodes")
    list_cmd.add_argument("--filter", action="append", default=[], help="Label filter (key=value)")
    list_cmd.add_argument("--names-only", action="store_true", help="Print hostnames only")

    resolve_cmd = sub.add_parser("resolve-services", help="Map services to nodes")
    resolve_cmd.add_argument("compose_file", help="Path to compose.yml")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "list":
            nodes = get_swarm_nodes(args.filter or None)
            if args.names_only:
                for node in nodes:
                    print(node["hostname"])
            else:
                for node in nodes:
                    labels = ",".join(f"{k}={v}" for k, v in sorted(node["labels"].items()))
                    print(f"{node['hostname']}\t{labels}")

        elif args.command == "resolve-services":
            mappings = get_service_node(args.compose_file)
            for svc, hostname in mappings:
                print(f"{svc}\t{hostname}")

    except SwarmError as e:
        from ._output import error
        error(str(e))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
