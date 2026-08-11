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

StackSecrets = list[tuple[str, str]]
EnvMapping = Mapping[str, str]


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
      1. ``stack_secrets`` (decrypted ``secrets.env`` pairs), case-insensitive
      2. The environment, by uppercased name

    Raises :class:`SecretError` listing the names that have no source.
    """
    needed = required_versioned_secrets(compose_json, deploy_version)
    if not needed:
        return
    env_map: EnvMapping = env if env is not None else os.environ
    have_local = {key.lower() for key, _ in (stack_secrets or [])}
    missing = [
        name.upper()
        for name in sorted(needed)
        if name not in have_local and name.upper() not in env_map
    ]
    if missing:
        raise SecretError(
            f"Missing required secrets: {', '.join(missing)}\n"
            "Add them to the stack's secrets.env or a SOPS secrets file loaded by mise"
        )


def create_versioned_secrets(
    compose_json: dict,
    deploy_version: str,
    stack_secrets: StackSecrets | None = None,
    env: EnvMapping | None = None,
) -> dict:
    """Create versioned Docker secrets for a stack deployment.

    Resolves values from two sources in order:
      1. ``stack_secrets`` -- pre-decrypted (key, value) pairs from secrets.env.
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
    created = skipped = 0
    resolved: set[str] = set()

    # 1. Stack-local secrets from secrets.env
    for key, value in stack_secrets or []:
        lower_key = key.lower()
        if lower_key not in needed:
            continue
        resolved.add(lower_key)
        secret_name = f"{lower_key}_{deploy_version}"
        if secret_name in existing:
            skipped += 1
        else:
            secret_create(secret_name, value)
            info(f"    + {secret_name}")
            created += 1

    # 2. Remaining needed secrets from environment (global secrets loaded by mise)
    for name in needed - resolved:
        value = env_map.get(name.upper())
        if value is None:
            continue
        resolved.add(name)
        secret_name = f"{name}_{deploy_version}"
        if secret_name in existing:
            skipped += 1
        else:
            secret_create(secret_name, value)
            info(f"    + {secret_name}")
            created += 1

    debug(f"    Created: {created}, Skipped: {skipped}")
    return {"created": created, "skipped": skipped}


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

        args = parser.parse_args()
        if not args.command:
            parser.print_help()
            return 1

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
