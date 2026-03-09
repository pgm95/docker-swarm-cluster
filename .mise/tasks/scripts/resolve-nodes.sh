#!/usr/bin/env bash

# Resolve swarm nodes from live cluster state.
# Requires: compose-config.sh sourced first (for get_service_node).

# Optional label filters narrow results. A node must match ALL filters.
get_swarm_nodes() {
    local filters=("$@")
    # word splitting on node IDs is intentional
    # shellcheck disable=SC2046
    docker node inspect $(docker node ls -q) 2>/dev/null | python3 -c '
import json, sys
filters = sys.argv[1:]
nodes = json.load(sys.stdin)
for node in nodes:
    hostname = node["Description"]["Hostname"]
    labels = node["Spec"].get("Labels", {})
    match = True
    for f in filters:
        key, _, val = f.partition("=")
        if labels.get(key) != val:
            match = False
            break
    if match:
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        print(f"{hostname}\t{label_str}")
' "${filters[@]}"
}

# If multiple nodes match, prints the first. If no nodes match, prints
# service_name<TAB>UNRESOLVED and returns 1 after processing all services.
get_service_node() {
    local compose_file="$1"
    local node_json
    # word splitting on node IDs is intentional
    # shellcheck disable=SC2046
    node_json=$(docker node inspect $(docker node ls -q) 2>/dev/null)
    compose_config "${compose_file}" --format json 2>/dev/null | python3 -c '
import json, sys
node_json = json.loads(sys.argv[1])
config = json.load(sys.stdin)
nodes = []
for n in node_json:
    nodes.append({
        "hostname": n["Description"]["Hostname"],
        "labels": n["Spec"].get("Labels", {}),
        "role": n["Spec"].get("Role", ""),
    })
unresolved = False
for svc_name, svc in config.get("services", {}).items():
    constraints = (svc.get("deploy") or {}).get("placement", {}).get("constraints", [])
    parsed = []
    for c in constraints:
        parts = c.split()
        if len(parts) == 3:
            parsed.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    matched = None
    for node in nodes:
        node_match = True
        for field, op, value in parsed:
            if field.startswith("node.labels."):
                label_key = field[len("node.labels."):]
                actual = node["labels"].get(label_key, "")
            elif field == "node.role":
                actual = node["role"]
            elif field == "node.hostname":
                actual = node["hostname"]
            else:
                actual = ""
            if op == "==" and actual != value:
                node_match = False
                break
            elif op == "!=" and actual == value:
                node_match = False
                break
        if node_match:
            matched = node["hostname"]
            break
    if matched:
        print(f"{svc_name}\t{matched}")
    else:
        print(f"{svc_name}\tUNRESOLVED")
        unresolved = True
sys.exit(1 if unresolved else 0)
' "${node_json}"
}

# SSH to a swarm node. User is configurable via SWARM_SSH_USER (default: root).
# Uses -n to prevent stdin consumption (safe for all command-arg callers).
ssh_node() {
    local hostname="$1"
    shift
    local user="${SWARM_SSH_USER:-root}"
    ssh -n "${user}@${hostname}" "$@"
}

# SSH to a swarm node with stdin passthrough (for piping data to remote commands).
ssh_node_stdin() {
    local hostname="$1"
    shift
    local user="${SWARM_SSH_USER:-root}"
    ssh "${user}@${hostname}" "$@"
}
