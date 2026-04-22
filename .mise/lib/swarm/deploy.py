"""Deploy preparation, content hashing, and image builds."""

import argparse
import hashlib
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import SwarmError
from ._docker import build, manifest_exists, push
from ._output import error, info, setup
from ._sops import sops_decrypt
from ._stack import resolve_stack_path, stack_name
from .secrets import create_versioned_secrets, validate_config_files, validate_required_secrets


def compute_content_hash(build_dir: str | Path) -> str:
    """Compute a 12-char content-based hash of a build context.

    Hashes sorted non-.md file paths and their contents.
    """
    build_dir = Path(build_dir)
    h = hashlib.sha256()
    for f in sorted(build_dir.rglob("*")):
        if f.is_file() and f.suffix != ".md":
            rel = str(f.relative_to(build_dir))
            h.update(rel.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def detect_versioning(stack_path: Path) -> bool:
    """Check if any *.yml in stack_path references DEPLOY_VERSION."""
    for yml in stack_path.glob("*.yml"):
        if "DEPLOY_VERSION" in yml.read_text():
            return True
    return False


def generate_deploy_version() -> str:
    """Generate '<git-sha>_<epoch>' version string."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = "local"
    return f"{sha}_{int(time.time())}"


def discover_build_dirs(stack_path: Path, stack_nm: str) -> list[dict]:
    """Find build/<service>/ directories and compute content hashes.

    Returns:
        List of dicts with keys: service, dir, tag, image, var_name.
    """
    registry = os.environ.get("GLOBAL_SWARM_OCI_REGISTRY", "")
    builds = []
    build_root = stack_path / "build"
    if not build_root.is_dir():
        return builds
    for svc_dir in sorted(build_root.iterdir()):
        if not svc_dir.is_dir():
            continue
        service = svc_dir.name
        tag = compute_content_hash(svc_dir)
        image = f"{registry}/{stack_nm}/{service}:{tag}"
        var_name = f"OCI_TAG_{service.upper().replace('-', '_')}"
        builds.append({
            "service": service,
            "dir": svc_dir,
            "tag": tag,
            "image": image,
            "var_name": var_name,
        })
    return builds


def build_and_push(image: str, build_dir: Path) -> bool:
    """Build and push a Docker image. Skips if manifest already exists.

    Returns:
        True if built, False if skipped (already exists).
    """
    if manifest_exists(image):
        info(f"  Exists: {image}")
        return False
    info(f"  Building: {image}")
    build(image, str(build_dir))
    info(f"  Pushing: {image}")
    push(image)
    return True


def prepare(stack_path_str: str) -> None:
    """Full deploy preparation.

    Outputs shell-safe export statements to stdout for bash eval.
    Progress and diagnostics go to stderr.
    """
    stack_path = resolve_stack_path(stack_path_str)
    compose_file = stack_path / "compose.yml"

    if not compose_file.is_file():
        raise SwarmError(f"{compose_file} not found")

    sops_key = os.environ.get("SOPS_AGE_KEY_FILE", "")
    if not sops_key or not Path(sops_key).is_file():
        raise SwarmError(f"SOPS_AGE_KEY_FILE not found: {sops_key or 'unset'}")

    stack_nm = stack_name(stack_path)
    _emit_export("STACK_NAME", stack_nm)
    _emit_export("STACK_PATH", str(stack_path))

    # --- Versioned secrets & configs ---
    deploy_version = ""
    if detect_versioning(stack_path):
        deploy_version = generate_deploy_version()
        _emit_export("DEPLOY_VERSION", deploy_version)
        info(f"Deploy version: {deploy_version}")

    # --- Decrypt and export stack secrets ---
    secrets_env = stack_path / "secrets.env"
    if secrets_env.is_file():
        info(f"Loading secrets: {secrets_env}")
        for key, value in sops_decrypt(str(secrets_env)):
            _emit_export(key, value)

    # --- Versioned secret creation ---
    if deploy_version:
        validate_required_secrets(stack_path)
        create_versioned_secrets(stack_path, deploy_version)
        validate_config_files(stack_path)
        info("")
    else:
        info("Stack uses env vars only")

    # --- Auto-build custom images ---
    builds = discover_build_dirs(stack_path, stack_nm)
    for b in builds:
        _emit_export(b["var_name"], b["tag"])
        info(f"Image: {b['var_name']}={b['tag']}")
        build_and_push(b["image"], b["dir"])
        info("")


def _emit_export(key: str, value: str) -> None:
    """Print a shell-safe export statement to stdout.

    Also sets os.environ so subsequent Python calls (validate, create)
    can see the values within the same prepare() invocation.
    """
    os.environ[key] = value
    print(f"export {key}={shlex.quote(value)}")


def main() -> int:
    setup()
    parser = argparse.ArgumentParser(prog="swarm.deploy")
    sub = parser.add_subparsers(dest="command")

    prep = sub.add_parser("prepare", help="Prepare a stack for deployment")
    prep.add_argument("stack_path", help="Path to stack directory")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "prepare":
            prepare(args.stack_path)
    except SwarmError as e:
        error(str(e))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
