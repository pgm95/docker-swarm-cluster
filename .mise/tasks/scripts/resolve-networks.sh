#!/usr/bin/env bash

# Scans stacks/infra/*/compose.yml for networks declared as external with the infra_ prefix. Returns deduplicated network names.

get_infra_networks() {
    grep -h -B1 'external: *true' stacks/infra/*/compose.yml 2>/dev/null \
        | grep -oE 'infra_[a-z_-]+' \
        | sort -u
}

is_internal_network() {
    local net="$1"
    [[ " ${SWARM_INTERNAL_NETWORKS:-} " == *" ${net} "* ]]
}
