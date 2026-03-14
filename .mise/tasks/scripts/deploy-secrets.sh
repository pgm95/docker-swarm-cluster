#!/usr/bin/env bash

# Validate and create versioned Docker secrets for a stack deployment.
# Expects: DEPLOY_VERSION, STACK_PATH set in caller's env.
# Expects: sops_decrypt() available (source sops-decrypt.sh or sops-export.sh first).

validate_required_secrets() {
    [[ -f "${STACK_PATH}/secrets.yml" ]] || return 0
    grep -q 'DEPLOY_VERSION' "${STACK_PATH}/secrets.yml" 2>/dev/null || return 0

    local missing=()
    while IFS= read -r required_var; do
        [[ -z "${required_var}" ]] && continue
        if [[ -z "${!required_var:-}" ]]; then
            missing+=("${required_var}")
        fi
    done < <(grep 'name:.*DEPLOY_VERSION' "${STACK_PATH}/secrets.yml" 2>/dev/null \
        | sed 's/.*name: *\([a-z_]*\)_.*/\1/' | sort -u \
        | while read -r secret; do echo "${secret^^}"; done)

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "ERROR: Missing required secrets:"
        printf "  %s\n" "${missing[@]}"
        echo "Add them to ${STACK_PATH}/secrets.env or GLOBAL_SECRETS"
        return 1
    fi
}

create_versioned_secrets() {
    [[ -f "${STACK_PATH}/secrets.yml" ]] || return 0
    grep -q 'DEPLOY_VERSION' "${STACK_PATH}/secrets.yml" 2>/dev/null || return 0

    # Build set of secret names actually referenced in secrets.yml
    local -A needed_secrets=()
    while IFS= read -r name; do
        [[ -z "${name}" ]] && continue
        needed_secrets["${name}"]=1
    done < <(grep 'name:.*DEPLOY_VERSION' "${STACK_PATH}/secrets.yml" 2>/dev/null \
        | sed 's/.*name: *\([a-z_]*\)_.*/\1/' | sort -u)

    echo "Creating versioned secrets (${#needed_secrets[@]} needed)..."
    local created=0 skipped=0 filtered=0
    for secret_file in "${STACK_PATH}/secrets.env" "${GLOBAL_SECRETS}"; do
        [[ -f "${secret_file}" ]] || continue
        while IFS= read -r line; do
            local key="${line%%=*}"
            local value="${line#*=}"
            if [[ "${key}" == *_B64 ]]; then
                key="${key%_B64}"
                value=$(echo -n "${value}" | base64 -d) || { echo "ERROR: base64-decode failed for ${key}_B64"; return 1; }
            fi
            local lower_key="${key,,}"
            if [[ -z "${needed_secrets[${lower_key}]:-}" ]]; then
                ((filtered++))
                continue
            fi
            local secret_name="${lower_key}_${DEPLOY_VERSION}"
            if docker secret inspect "${secret_name}" >/dev/null 2>&1; then
                ((skipped++))
            elif echo -n "${value}" | docker secret create "${secret_name}" -; then
                echo "    + ${secret_name}"
                ((created++))
            else
                echo "ERROR: Failed to create secret '${secret_name}'"
                return 1
            fi
        done < <(sops_decrypt "${secret_file}")
    done
    echo "    Created: ${created}, Skipped: ${skipped}, Filtered: ${filtered}"
}

validate_config_files() {
    [[ -f "${STACK_PATH}/configs.yml" ]] || return 0
    while IFS= read -r config_file; do
        [[ -z "${config_file}" ]] && continue
        [[ -f "${STACK_PATH}/${config_file}" ]] || { echo "Error: Missing config file: ${STACK_PATH}/${config_file}"; return 1; }
    done < <(grep 'file:' "${STACK_PATH}/configs.yml" 2>/dev/null | sed 's/.*file:[[:space:]]*//')
}
