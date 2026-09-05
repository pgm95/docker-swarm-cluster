"""Secret validation and versioned Docker secret creation.

All discovery operates on the rendered compose JSON — the merged result of
``docker compose config --format json``. Compose-spec native composition
(``include:``, anchors, multi-``-f``) is the user's responsibility; the lib
just inspects the final document.
"""

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from . import SecretError, ValidationError
from ._cli import cli_main
from ._compose import compose_json
from ._docker import secret_create, secret_list
from ._output import debug, info
from ._stack import SECRETS_FILE, all_stacks, resolve_stack_path, stack_name

StackSecrets = list[tuple[str, str]]
EnvMapping = Mapping[str, str]

# Suffix shared by every SOPS-encrypted file in the repo. Global files are
# ``<target>.sops.yaml`` under ``PROJECT_SECRETS_DIR``; stack files are
# ``<stack>/secrets.sops.yaml``.
SOPS_SUFFIX = ".sops.yaml"


def global_secrets_dir() -> Path:
    """Directory holding the shared and per-environment SOPS files."""
    return Path(os.environ.get("PROJECT_SECRETS_DIR", ".secrets"))


def global_secrets_files() -> list[Path]:
    """Existing global SOPS files, sorted by name."""
    d = global_secrets_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.name.endswith(SOPS_SUFFIX))


def secrets_file_for(target: str) -> Path:
    """Resolve a secrets target to its SOPS file path.

    A target is either the stem of a global file (``shared``, ``dev``,
    ``prod``) or anything :func:`resolve_stack_path` accepts (bare stack
    name, ``NN_`` directory name, or path). Global names win on collision.
    The returned path may not exist yet; ``sops edit`` creates it.
    """
    global_file = global_secrets_dir() / f"{target}{SOPS_SUFFIX}"
    if global_file.is_file():
        return global_file
    return resolve_stack_path(target) / SECRETS_FILE


def secrets_targets() -> list[str]:
    """All target names: global stems first, then stack names in deploy order."""
    stems = [p.name.removesuffix(SOPS_SUFFIX) for p in global_secrets_files()]
    return stems + [stack_name(d) for d in all_stacks()]


def all_secrets_files() -> list[Path]:
    """Every existing SOPS file: globals first, then stacks in deploy order."""
    stack_files = [f for d in all_stacks() if (f := d / SECRETS_FILE).is_file()]
    return global_secrets_files() + stack_files


def _local_secrets(stack_secrets: StackSecrets | None) -> dict[str, str]:
    """Stack-local pairs keyed by lowercased name (the versioned-secret base name)."""
    return {key.lower(): value for key, value in (stack_secrets or [])}


def required_versioned_secrets(compose_json: dict, deploy_version: str) -> set[str]:
    """Walk the rendered compose document for secrets bound to this deploy version.

    Returns the set of base names (without the ``_<version>`` suffix) for
    secrets whose ``name`` field ends with ``_<deploy_version>``. These are
    the secrets the deploy pipeline must create as Docker secrets.

    Example: an entry ``secrets.foo: { name: foo_abc1234_1700000000, external: true }``
    deployed with ``deploy_version="abc1234_1700000000"`` produces ``{"foo"}``.
    """
    suffix = f"_{deploy_version}"
    needed: set[str] = set()
    for sec in (compose_json.get("secrets") or {}).values():
        full = (sec or {}).get("name", "")
        if full.endswith(suffix):
            needed.add(full[: -len(suffix)])
    return needed


def referenced_config_files(compose_json: dict) -> list[str]:
    """List ``file:`` paths referenced by top-level ``configs:`` entries.

    Paths are returned as compose emitted them (typically already absolute,
    since ``docker compose config`` resolves relative references against the
    project directory).
    """
    files: list[str] = []
    for cfg in (compose_json.get("configs") or {}).values():
        path = (cfg or {}).get("file")
        if path:
            files.append(path)
    return files


def validate_required_secrets(
    compose_json: dict,
    deploy_version: str,
    stack_secrets: StackSecrets | None = None,
    env: EnvMapping | None = None,
) -> None:
    """Verify every versioned secret in the rendered compose has a value source.

    Looks at each base name returned by :func:`required_versioned_secrets`
    and checks it can be resolved from either:
      1. ``stack_secrets`` (decrypted stack secrets file pairs), case-insensitive
      2. The environment, by uppercased name

    Raises :class:`SecretError` listing the names that have no source.
    """
    needed = required_versioned_secrets(compose_json, deploy_version)
    if not needed:
        return
    env_map: EnvMapping = env if env is not None else os.environ
    have_local = _local_secrets(stack_secrets)
    missing = [
        name.upper()
        for name in sorted(needed)
        if name not in have_local and name.upper() not in env_map
    ]
    if missing:
        raise SecretError(
            f"Missing required secrets: {', '.join(missing)}\n"
            f"Add them to the stack's {SECRETS_FILE} or a SOPS secrets file loaded by mise"
        )


def create_versioned_secrets(
    compose_json: dict,
    deploy_version: str,
    stack_secrets: StackSecrets | None = None,
    env: EnvMapping | None = None,
) -> dict:
    """Create versioned Docker secrets for a stack deployment.

    Resolves values from two sources in order:
      1. ``stack_secrets`` -- pre-decrypted (key, value) pairs from the stack secrets file.
      2. Environment variables (global secrets already loaded by mise).

    Returns:
        ``{"created": int, "skipped": int}``
    """
    needed = required_versioned_secrets(compose_json, deploy_version)
    if not needed:
        return {"created": 0, "skipped": 0}

    env_map: EnvMapping = env if env is not None else os.environ
    info(f"Creating versioned secrets ({len(needed)} needed)...")
    existing = set(secret_list())
    counts = {"created": 0, "skipped": 0}

    def ensure(name: str, value: str) -> None:
        secret_name = f"{name}_{deploy_version}"
        if secret_name in existing:
            counts["skipped"] += 1
            return
        secret_create(secret_name, value)
        info(f"    + {secret_name}")
        counts["created"] += 1

    # 1. Stack-local secrets from secrets.sops.yaml
    local = _local_secrets(stack_secrets)
    for name in sorted(needed & local.keys()):
        ensure(name, local[name])

    # 2. Remaining needed secrets from environment (global secrets loaded by mise)
    for name in sorted(needed - local.keys()):
        value = env_map.get(name.upper())
        if value is not None:
            ensure(name, value)

    debug(f"    Created: {counts['created']}, Skipped: {counts['skipped']}")
    return counts


def validate_config_files(compose_json: dict) -> None:
    """Verify every ``file:``-backed config in the rendered compose exists on disk.

    Raises :class:`ValidationError` listing missing paths.
    """
    missing = [p for p in referenced_config_files(compose_json) if not Path(p).is_file()]
    if missing:
        raise ValidationError(
            "Missing config files:\n" + "\n".join(f"  {f}" for f in missing)
        )


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="swarm.secrets")
        sub = parser.add_subparsers(dest="command")

        validate_cmd = sub.add_parser("validate", help="Validate required secrets are sourced")
        validate_cmd.add_argument("stack_path", help="Path to stack directory")
        validate_cmd.add_argument(
            "--version",
            required=True,
            help="Deploy version string (matches the suffix used in secret names)",
        )

        create_cmd = sub.add_parser("create", help="Create versioned Docker secrets")
        create_cmd.add_argument("stack_path", help="Path to stack directory")
        create_cmd.add_argument("--version", required=True, help="Deploy version string")

        configs_cmd = sub.add_parser("validate-configs", help="Validate config file references")
        configs_cmd.add_argument("stack_path", help="Path to stack directory")

        path_cmd = sub.add_parser("path", help="Print SOPS file path(s) to stdout")
        path_cmd.add_argument("target", nargs="?", help="Global stem (shared, dev, prod) or stack name")
        path_group = path_cmd.add_mutually_exclusive_group()
        path_group.add_argument("--all", action="store_true", help="List every existing SOPS file")
        path_group.add_argument("--targets", action="store_true", help="List all target names")

        args = parser.parse_args()
        if not args.command:
            parser.print_help()
            return 1

        if args.command == "path":
            if args.all:
                lines = [str(p) for p in all_secrets_files()]
            elif args.targets:
                lines = secrets_targets()
            elif args.target:
                lines = [str(secrets_file_for(args.target))]
            else:
                path_cmd.print_help(sys.stderr)
                return 1
            for line in lines:
                print(line)
            return 0

        stack = Path(args.stack_path)
        rendered = compose_json(stack / "compose.yml")
        if args.command == "validate":
            validate_required_secrets(rendered, args.version)
            info("All required secrets present.")
        elif args.command == "create":
            create_versioned_secrets(rendered, args.version)
        elif args.command == "validate-configs":
            validate_config_files(rendered)
            info("All config files present.")
        return 0
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
