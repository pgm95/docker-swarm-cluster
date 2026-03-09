#!/usr/bin/env bash

# Discover all SOPS-managed secret files in the project.

find_secret_files() {
    find "${PROJECT_SECRETS_DIR}" -maxdepth 1 -name "*.yaml" 2>/dev/null | sort
    find stacks -name "secrets.env" 2>/dev/null | sort
}
