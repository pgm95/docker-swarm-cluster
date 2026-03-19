"""Secret validation and versioned Docker secret creation."""

import argparse
import os
import re
import sys
from pathlib import Path

from . import SecretError, ValidationError
from ._docker import secret_create, secret_list
from ._output import error, info, setup
from ._sops import sops_decrypt

# Pattern: "name: some_secret_name_${DEPLOY_VERSION}" or similar
_VERSIONED_NAME_RE = re.compile(r"name:\s*([a-z_]+)_\$\{DEPLOY_VERSION\}")


def parse_versioned_names(secrets_yml: Path) -> set[str]:
    """Extract secret base names from secrets.yml that use DEPLOY_VERSION.

    Returns:
        Set of lowercase secret name prefixes
        (e.g., {"authelia_jwt_secret", "lldap_ldap_user_pass"}).
    """
    if not secrets_yml.is_file():
        return set()
    text = secrets_yml.read_text()
    if "DEPLOY_VERSION" not in text:
        return set()
    return set(_VERSIONED_NAME_RE.findall(text))


def validate_required_secrets(stack_path: Path, env: dict | None = None) -> None:
    """Check that all secrets referenced in secrets.yml exist in environment.

    Args:
        stack_path: Path to the stack directory.
        env: Environment dict to check (defaults to os.environ).

    Raises:
        SecretError: If any required secrets are missing.
    """
    needed = parse_versioned_names(stack_path / "secrets.yml")
    if not needed:
        return
    env = env if env is not None else os.environ
    missing = [name.upper() for name in sorted(needed) if name.upper() not in env]
    if missing:
        raise SecretError(
            f"Missing required secrets: {', '.join(missing)}\n"
            f"Add them to {stack_path}/secrets.env or GLOBAL_SECRETS"
        )


def create_versioned_secrets(
    stack_path: Path,
    deploy_version: str,
    env: dict | None = None,
) -> dict:
    """Create versioned Docker secrets for a stack deployment.

    Reads needed secret names from secrets.yml, decrypts secrets.env and
    GLOBAL_SECRETS, creates Docker secrets for each match.

    Args:
        stack_path: Path to the stack directory.
        deploy_version: Version suffix for secret names.
        env: Environment dict (defaults to os.environ).

    Returns:
        {"created": int, "skipped": int, "filtered": int}
    """
    needed = parse_versioned_names(stack_path / "secrets.yml")
    if not needed:
        return {"created": 0, "skipped": 0, "filtered": 0}

    env = env if env is not None else os.environ
    global_secrets = env.get("GLOBAL_SECRETS", "")

    info(f"Creating versioned secrets ({len(needed)} needed)...")
    existing = set(secret_list())
    created = skipped = filtered = 0

    for secret_file in [str(stack_path / "secrets.env"), global_secrets]:
        if not secret_file or not Path(secret_file).is_file():
            continue
        for key, value in sops_decrypt(secret_file):
            lower_key = key.lower()
            if lower_key not in needed:
                filtered += 1
                continue
            secret_name = f"{lower_key}_{deploy_version}"
            if secret_name in existing:
                skipped += 1
            else:
                secret_create(secret_name, value)
                info(f"    + {secret_name}")
                created += 1

    info(f"    Created: {created}, Skipped: {skipped}, Filtered: {filtered}")
    return {"created": created, "skipped": skipped, "filtered": filtered}


def validate_config_files(stack_path: Path) -> None:
    """Verify all config files referenced in configs.yml exist on disk.

    Raises:
        ValidationError: If any referenced files are missing.
    """
    configs_yml = stack_path / "configs.yml"
    if not configs_yml.is_file():
        return
    missing = []
    for line in configs_yml.read_text().splitlines():
        match = re.search(r"file:\s*(.*)", line)
        if match:
            config_file = match.group(1).strip()
            if not (stack_path / config_file).is_file():
                missing.append(config_file)
    if missing:
        raise ValidationError(
            f"Missing config files in {stack_path}:\n"
            + "\n".join(f"  {f}" for f in missing)
        )


def main() -> int:
    setup()
    parser = argparse.ArgumentParser(prog="swarm.secrets")
    sub = parser.add_subparsers(dest="command")

    validate_cmd = sub.add_parser("validate", help="Validate required secrets exist in env")
    validate_cmd.add_argument("stack_path", help="Path to stack directory")

    create_cmd = sub.add_parser("create", help="Create versioned Docker secrets")
    create_cmd.add_argument("stack_path", help="Path to stack directory")
    create_cmd.add_argument("--version", required=True, help="Deploy version string")

    configs_cmd = sub.add_parser("validate-configs", help="Validate config file references")
    configs_cmd.add_argument("stack_path", help="Path to stack directory")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    try:
        stack = Path(args.stack_path)
        if args.command == "validate":
            validate_required_secrets(stack)
            info("All required secrets present.")
        elif args.command == "create":
            create_versioned_secrets(stack, args.version)
        elif args.command == "validate-configs":
            validate_config_files(stack)
            info("All config files present.")
    except (SecretError, ValidationError) as e:
        error(str(e))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
