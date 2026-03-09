#!/usr/bin/env bash

# Wait for a Docker stack to converge and verify replica health.
# Expects: CONVERGE_TIMEOUT, CONVERGE_INTERVAL (with defaults)

wait_for_convergence() {
    local stack_name="$1"
    local timeout="${CONVERGE_TIMEOUT:-180}"
    local interval="${CONVERGE_INTERVAL:-2}"
    local elapsed=0

    echo "Waiting for services to converge..."
    while [[ ${elapsed} -lt ${timeout} ]]; do
        if docker stack ps "${stack_name}" 2>/dev/null | grep -qE "Pending|Preparing|Starting|Ready"; then
            sleep "${interval}"; ((elapsed += interval))
            continue
        fi
        local all_healthy=true
        while IFS=$'\t' read -r _ replicas; do
            local current="${replicas%%/*}"
            local desired="${replicas##*/}"
            if [[ "${current}" -lt "${desired}" ]]; then
                all_healthy=false
                break
            fi
        done < <(docker stack services "${stack_name}" --format '{{.Name}}\t{{.Replicas}}' 2>/dev/null)
        ${all_healthy} && return 0
        sleep "${interval}"; ((elapsed += interval))
    done
    echo "WARNING: Timeout waiting for convergence after ${timeout}s"
}

check_replica_health() {
    local stack_name="$1"
    local unhealthy=0

    while IFS=$'\t' read -r name replicas; do
        local current="${replicas%%/*}"
        local desired="${replicas##*/}"
        if [[ "${current}" -lt "${desired}" ]]; then
            echo "UNHEALTHY: ${name} ${replicas}"
            ((unhealthy++))
        fi
    done < <(docker stack services "${stack_name}" --format '{{.Name}}\t{{.Replicas}}' 2>/dev/null)

    if [[ ${unhealthy} -gt 0 ]]; then
        echo ""
        echo "ERROR: ${unhealthy} service(s) not converged"
        docker stack ps "${stack_name}" --no-trunc --filter "desired-state=running" 2>/dev/null | grep -vE "Running|\\\\\_" | head -5
        return 1
    fi
}
