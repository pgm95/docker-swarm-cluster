#!/usr/bin/env bash
# Compute content-based hash of a build context directory.
# Outputs a 12-char hex digest of all non-markdown file paths and contents.
#
# Usage: compute_content_hash <dir>
# Returns: 12-char hex string via stdout

compute_content_hash() {
    local dir="$1"
    (cd "${dir}" && find . -type f ! -name '*.md' | sort \
        | while read -r f; do echo "$f"; cat "$f"; done \
        | shasum -a 256 | cut -c1-12)
}
