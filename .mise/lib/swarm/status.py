"""Cluster status display."""

import sys

from . import SwarmError
from ._docker import inspect_nodes, stack_list, stack_ps, stack_services
from ._output import error, setup, table
from ._stack import find_stacks, stack_name
from .convergence import is_task_complete


def get_node_status() -> list[dict]:
    """Get node info from docker node ls + inspect."""
    raw_nodes = inspect_nodes()
    result = []
    for node in raw_nodes:
        desc = node["Description"]
        spec = node["Spec"]
        labels = spec.get("Labels", {})
        label_str = " ".join(f"{k}={v}" for k, v in sorted(labels.items())) or "-"

        manager = node.get("ManagerStatus", {})
        role = "Leader" if manager.get("Leader") else "Reachable" if manager.get("Reachability") else "Worker"

        result.append({
            "hostname": desc["Hostname"],
            "role": role,
            "status": f"{node['Status']['State']}/{spec.get('Availability', 'unknown')}",
            "engine": desc.get("Engine", {}).get("EngineVersion", "?"),
            "labels": label_str,
        })
    return result


def get_stack_health(svc_stack_name: str) -> dict:
    """Assess health of a deployed stack.

    Returns:
        {"name", "status", "services": [...], "nodes": [...], "errors": [...]}
    """
    deployed = stack_list()
    if svc_stack_name not in deployed:
        return {"name": svc_stack_name, "status": "not deployed", "services": [], "nodes": [], "errors": []}

    services = []
    healthy = True
    for name, replicas in stack_services(svc_stack_name):
        current, desired = replicas.split("/")
        current, desired = int(current), int(desired)
        svc_healthy = True
        if current < desired:
            if not is_task_complete(name):
                svc_healthy = False
                healthy = False
        short = name.removeprefix(f"{svc_stack_name}_")
        services.append({"name": short, "replicas": replicas, "healthy": svc_healthy})

    # Collect node placement
    nodes = set()
    rows = stack_ps(
        svc_stack_name,
        format_str="{{.Name}}\t{{.Node}}",
        filters=["desired-state=running"],
    )
    for row in rows:
        if len(row) >= 2 and row[1]:
            nodes.add(row[1])

    # Collect errors for unhealthy services
    errors = []
    if not healthy:
        err_rows = stack_ps(
            svc_stack_name,
            format_str="{{.Name}}\t{{.Error}}",
            filters=["desired-state=running"],
            no_trunc=True,
        )
        for row in err_rows:
            if len(row) >= 2 and row[1]:
                short = row[0].split(".")[0].removeprefix(f"{svc_stack_name}_")
                errors.append({"service": short, "error": row[1]})

    return {
        "name": svc_stack_name,
        "status": "healthy" if healthy else "UNHEALTHY",
        "services": services,
        "nodes": sorted(nodes),
        "errors": errors,
    }


def status() -> int:
    """Full status display. Returns exit code."""
    # --- Nodes ---
    nodes = get_node_status()
    table(
        ["NODE", "ROLE", "STATUS", "ENGINE", "LABELS"],
        [[n["hostname"], n["role"], n["status"], n["engine"], n["labels"]] for n in nodes],
    )
    print()

    # --- Stacks ---
    all_stacks = []
    for ns in ["stacks/infra", "stacks/apps"]:
        for d in find_stacks(ns):
            all_stacks.append(stack_name(d))

    total = healthy_count = unhealthy_count = 0
    rows = []
    error_details = []

    for name in all_stacks:
        health = get_stack_health(name)
        if health["status"] == "not deployed":
            rows.append([name, "not deployed", "", ""])
            continue
        total += 1
        svc_summary = " ".join(f"{s['name']}({s['replicas']})" for s in health["services"])
        node_col = " ".join(health["nodes"]) if health["nodes"] else "-"
        rows.append([name, health["status"], node_col, svc_summary])

        if health["status"] == "UNHEALTHY":
            unhealthy_count += 1
            for e in health["errors"]:
                error_details.append((name, e["service"], e["error"]))
        else:
            healthy_count += 1

    table(["STACK", "STATUS", "NODE", "SERVICES"], rows)

    for stack_nm, svc, err in error_details:
        print(f"  {svc}: {err}")

    print()
    print(f"Deployed: {total} | Healthy: {healthy_count} | Unhealthy: {unhealthy_count}")
    return 1 if unhealthy_count > 0 else 0


def main() -> int:
    setup()
    try:
        return status()
    except SwarmError as e:
        error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
