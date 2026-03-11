---
description: Documentation style guidelines for project markdown files
# paths:
#   - '**/*.md'
---

# Documentation Patterns

## Style

**Use env vars, not filenames** — Reference `GLOBAL_SECRETS`, `SOPS_AGE_KEY_FILE`, `SOPS_CONFIG` instead of their resolved paths. Values change; var names are stable.

**Stay high-level** — Don't list specific var names or secret contents in docs. Say "shared secrets" not "domains, OIDC, LDAP, SMTP". Specific vars belong in the files themselves, not in documentation about those files.

**Generic over specific** — Use `<stack>/secrets.env` not `mealie/secrets.env — OIDC credentials, OpenAI key`. Per-stack details go stale; patterns don't.

**Mise `[env]` is the source of truth** — When referencing where config lives, say `mise [env]` not `.mise/config.toml under [env]`. The tool matters, not the file path.

## Structure

**Single ownership** — Every piece of information lives in exactly one document. Other documents link to it. Never duplicate content across files.

**Architecture in main README** — Project-wide design decisions (dual gateways, secrets model, network topology) belong in `README.md`, not in component or stack READMEs.

**Stack READMEs only for deviations** — Don't create a README that restates what's readable from compose files. Only document behavior that is non-obvious or deviates from documented patterns (GPU passthrough, restore procedures, bootstrap quirks).

**Don't document volatile state** — Services routed through a gateway, files in a directory, consumer stacks of a service — these change frequently and become stale. If the info is derivable from the code, let the code be the source of truth.

**Doc location by audience** — Mise/tooling docs in `.mise/README.md`. AI agent instructions in `.claude/`. Human-facing architecture in `README.md`. Stack-specific operational details in stack READMEs.
