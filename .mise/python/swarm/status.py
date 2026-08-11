"""Cluster status display."""

import os
import sys

from ._cli import cli_main
from ._docker import (
    inspect_nodes,
    parse_replicas,
    service_ls,
    service_ps_multi,
    task_name_to_service,
)
from ._output import table
from ._stack import find_namespaces, find_stacks, stack_name


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


def status() -> int:
    """Full status display. Returns exit code."""
    # --- Nodes (2 docker calls) ---
    nodes = get_node_status()
    table(
        ["NODE", "ROLE", "STATUS", "ENGINE", "LABELS"],
        [[n["hostname"], n["role"], n["status"], n["engine"], n["labels"]] for n in nodes],
    )
    print()

    # --- Discover all known stacks across every namespace ---
    all_stack_names = []
    for ns in find_namespaces():
        for d in find_stacks(ns):
            all_stack_names.append(stack_name(d))

    # --- Fetch all services in one call ---
    all_services = service_ls()

    # Group services by stack. Service name format is `<stack>_<service>`. Stack
    # names contain no underscores after the NN_ prefix is stripped (project
    # convention enforced by `stacks/<ns>/<NN>_<name>` directory layout), so
    # the first underscore-delimited segment of any service name is the stack.
    known_stacks = set(all_stack_names)
    deployed_stacks: set[str] = set()
    services_by_stack: dict[str, list[tuple[str, str]]] = {}
    for svc_name, replicas in all_services:
        candidate, _, _ = svc_name.partition("_")
        if candidate in known_stacks:
            deployed_stacks.add(candidate)
            services_by_stack.setdefault(candidate, []).append((svc_name, replicas))

    # --- Identify services needing task queries ---
    # Services with current < desired need is_task_complete check
    # All deployed services need node placement
    deployed_svc_names = [name for name, _ in all_services]

    # Fetch running tasks for all services (node placement) — 1 call
    node_by_task: dict[str, set[str]] = {}
    if deployed_svc_names:
        task_rows = service_ps_multi(
            deployed_svc_names,
            format_str="{{.Name}}\t{{.Node}}",
            filters=["desired-state=running"],
        )
        for row in task_rows:
            if len(row) >= 2 and row[1]:
                svc_key = task_name_to_service(row[0])
                node_by_task.setdefault(svc_key, set()).add(row[1])

    # Identify under-replicated services that might be init sidecars.
    # parse_replicas returns None for transient "N/A" values; treat unknown as
    # not-under-replicated (we can't tell, so don't trigger a Complete check).
    under_replicated = []
    for svc_name, replicas in all_services:
        parsed = parse_replicas(replicas)
        if parsed is not None and parsed[0] < parsed[1]:
            under_replicated.append(svc_name)

    # Fetch shutdown tasks for under-replicated services (is_task_complete) — 1 call
    completed_services: set[str] = set()
    if under_replicated:
        shutdown_rows = service_ps_multi(
            under_replicated,
            format_str="{{.Name}}\t{{.CurrentState}}",
            filters=["desired-state=shutdown"],
        )
        # Group by service, check if most recent shutdown task is Complete
        latest_state: dict[str, str] = {}
        for row in shutdown_rows:
            if len(row) >= 2:
                svc_key = task_name_to_service(row[0])
                if svc_key not in latest_state:
                    latest_state[svc_key] = row[1]
        for svc_name in under_replicated:
            state = latest_state.get(svc_name, "")
            if state.startswith("Complete"):
                completed_services.add(svc_name)

    # --- Build output ---
    total = healthy_count = unhealthy_count = 0
    rows = []
    not_deployed = []

    for name in all_stack_names:
        if name not in deployed_stacks:
            not_deployed.append(name)
            continue

        total += 1
        stack_svcs = services_by_stack.get(name, [])
        healthy = True
        svc_parts = []

        for svc_name, replicas in stack_svcs:
            parsed = parse_replicas(replicas)
            # Unparseable replicas (e.g. transient "N/A") count as not-healthy
            # so the stack surfaces as UNHEALTHY rather than silently passing.
            if parsed is None or (parsed[0] < parsed[1] and svc_name not in completed_services):
                healthy = False
            short = svc_name.removeprefix(f"{name}_")
            svc_parts.append(f"{short}({replicas})")

        # Collect nodes for this stack's services
        stack_nodes: set[str] = set()
        for svc_name, _ in stack_svcs:
            stack_nodes.update(node_by_task.get(svc_name, set()))

        svc_summary = " ".join(svc_parts)
        node_col = " ".join(sorted(stack_nodes)) if stack_nodes else "-"
        status_str = "healthy" if healthy else "UNHEALTHY"
        rows.append([name, status_str, node_col, svc_summary])

        if healthy:
            healthy_count += 1
        else:
            unhealthy_count += 1

    wrap = int(os.environ.get("STATUS_SERVICE_WRAP", "3"))
    table(["STACK", "STATUS", "NODE", "SERVICES"], rows, wrap_width=wrap)
    print()

    if not_deployed:
        print(f"Not deployed: {', '.join(not_deployed)}")
    print(f"Deployed: {total} | Healthy: {healthy_count} | Unhealthy: {unhealthy_count}")
    return 1 if unhealthy_count > 0 else 0


def main() -> int:
    return cli_main(status)


if __name__ == "__main__":
    sys.exit(main())
