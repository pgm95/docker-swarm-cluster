#!/usr/bin/env bash

# Shared function: concatenates centralized anchors with a compose file
# before passing to docker compose config.

SHARED_ANCHORS="stacks/_shared/anchors.yml"

compose_config() {
    local stack_file="$1"
    shift
    local stack_dir
    stack_dir=$(dirname "$stack_file")

    docker compose --project-directory "$stack_dir" \
        -f <(cat "$SHARED_ANCHORS" "$stack_file") \
        config "$@"
}
