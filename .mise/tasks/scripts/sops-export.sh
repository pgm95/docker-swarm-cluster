#!/usr/bin/env bash
# Decrypt a SOPS-encrypted file and export all key=value pairs as env vars.
# Handles _B64 suffix: base64-decodes value and strips suffix from key name.
#
# Usage: sops_export <file>
# Effect: Exports decrypted vars into the caller's environment

source "$(dirname "${BASH_SOURCE[0]}")/sops-decrypt.sh"

sops_export() {
    local file="$1"
    [[ -f "${file}" ]] || return 0
    echo "Loading secrets: ${file}"

    while IFS= read -r line; do
        local key="${line%%=*}"
        local value="${line#*=}"

        if [[ "${key}" == *_B64 ]]; then
            key="${key%_B64}"
            value=$(echo -n "${value}" | base64 -d) || {
                echo "ERROR: base64-decode failed for ${key}_B64" >&2
                return 1
            }
        fi
        export "${key}=${value}"
    done < <(sops_decrypt "${file}") || return 1
}
