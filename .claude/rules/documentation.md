---
description: Documentation style guidelines for project markdown files
# paths:
#   - '**/*.md'
---

# Documentation Patterns

**Use env vars, not filenames** — Reference `GLOBAL_SECRETS`, `SOPS_AGE_KEY_FILE`, `SOPS_CONFIG` instead of their resolved paths. Values change; var names are stable.

**Stay high-level** — Don't list specific var names or secret contents in docs. Say "shared secrets" not "domains, OIDC, LDAP, SMTP". Specific vars belong in the files themselves, not in documentation about those files.

**Generic over specific** — Use `<stack>/secrets.env` not `mealie/secrets.env — OIDC credentials, OpenAI key`. Per-stack details go stale; patterns don't.

**Mise `[env]` is the source of truth** — When referencing where config lives, say `mise [env]` not `.mise/config.toml under [env]`. The tool matters, not the file path.
