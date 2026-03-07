#!/usr/bin/env bash
# Decrypt a SOPS-encrypted file and output clean key=value lines to stdout.
# Handles both dotenv (.env) and YAML (.yml/.yaml) formats.
# Filters out comments, empty lines, and sops metadata keys.
#
# Usage: sops_decrypt <file>
# Returns: key=value lines via stdout (empty if file doesn't exist)

sops_decrypt() {
    local file="$1"
    [[ -f "${file}" ]] || return 0

    local decrypt_args=()
    if [[ "${file}" == *.yml || "${file}" == *.yaml ]]; then
        decrypt_args=(--output-type dotenv)
    fi

    local decrypted
    decrypted=$(sops decrypt "${decrypt_args[@]}" "${file}" 2>&1) || {
        echo "Error: Failed to decrypt ${file}" >&2
        echo "${decrypted}" >&2
        return 1
    }

    while IFS= read -r line; do
        local key="${line%%=*}"
        [[ -z "${key}" || "${key}" == \#* || "${key}" == sops* ]] && continue
        echo "${line}"
    done <<< "${decrypted}"
}
