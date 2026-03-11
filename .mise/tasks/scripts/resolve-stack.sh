#!/usr/bin/env bash

# Derives the Swarm stack name from a stack directory path.
# Strips optional NN_ numeric prefix from the folder basename.
stack_name() {
    basename "$1" | sed 's/^[0-9]\{2\}_//'
}

# Discovers stack directories under a namespace, sorted by folder name.
# Usage: find_stacks <namespace_dir> [--reverse]
find_stacks() {
    local sort_flag=""
    [[ "${2:-}" == "--reverse" ]] && sort_flag="-r"
    find "$1" -mindepth 1 -maxdepth 1 -type d | sort ${sort_flag}
}
