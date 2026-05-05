"""Self-contained per-stack deploy pipeline.

`python3 -m swarm.deploy <stack> [--update]` runs the full pipeline for one
stack: prepare (resolve, decrypt, version, build) -> compose preprocess ->
docker stack deploy -> convergence verify -> exit code.

Returns 0 on success, 1 on any failure. The failing function emits an
explicit `error()` line describing the cause before returning. Multi-stack
iteration lives in mise tasks' bash, not here.
"""

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

from . import SwarmError, _docker
from . import convergence as _convergence
from ._cli import cli_main
from ._compose import compose_config, compose_json
from ._docker import build, manifest_exists, push, stack_services
from ._output import error, info, init_stack_prefix, table
from ._sops import sops_decrypt
from ._stack import oci_tag_var, resolve_stack_path, stack_name
from .secrets import (
    create_versioned_secrets,
    required_versioned_secrets,
    validate_config_files,
    validate_required_secrets,
)


def compute_content_hash(build_dir: str | Path) -> str:
    """Compute a 12-char content-based hash of a build context.

    Hashes sorted non-.md file paths, their contents, and file mode bits.
    Including the mode bits ensures that ``chmod +x`` on an entrypoint
    script invalidates the cache even when the file content is unchanged
    — Docker's ``COPY`` preserves the executable bit, so two builds with
    identical content but different perms would produce different images
    yet hash the same without this.
    """
    build_dir = Path(build_dir)
    h = hashlib.sha256()
    for f in sorted(build_dir.rglob("*")):
        if f.is_file() and f.suffix != ".md":
            rel = str(f.relative_to(build_dir))
            mode = f.stat().st_mode
            h.update(rel.encode())
            h.update(mode.to_bytes(4, "big"))
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


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
        var_name = oci_tag_var(service)
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


def _prepare_stack(stack_path: Path) -> dict:
    """Prepare a stack for deployment: env, secrets, builds, render.

    Order of operations:
      1. Validate inputs (compose.yml exists, SOPS key available)
      2. Set STACK_NAME / STACK_PATH / DEPLOY_VERSION in env
      3. Decrypt secrets.env, export each key into env (compose
         interpolation needs them)
      4. Discover build dirs, set OCI_TAG_*, build+push images
      5. Render the compose document — by this point every env var
         compose might interpolate is set
      6. Inspect the rendered JSON to discover what versioned secrets
         to create and what config files to validate. Both are
         compose-native: the user organizes secrets/configs however
         they like (inlined, ``include:``, anchors), the lib only sees
         the merged result.

    Returns:
        Context dict with ``stack_name``, ``stack_path``, ``deploy_version``,
        ``stack_secrets``, ``builds``, ``compose_yaml`` (the rendered YAML
        string ready to feed to ``docker stack deploy``), and
        ``compose_json`` (the same rendering parsed for discovery).
    """
    compose_file = stack_path / "compose.yml"
    if not compose_file.is_file():
        raise SwarmError(f"{compose_file} not found")

    sops_key = os.environ.get("SOPS_AGE_KEY_FILE", "")
    if not sops_key or not Path(sops_key).is_file():
        raise SwarmError(f"SOPS_AGE_KEY_FILE not found: {sops_key or 'unset'}")

    stack_nm = stack_name(stack_path)
    # `compose_config` runs `docker compose config` as a subprocess that
    # interpolates ${VAR} references. We populate os.environ so those vars
    # are visible to that subprocess via inheritance.
    os.environ["STACK_NAME"] = stack_nm
    os.environ["STACK_PATH"] = str(stack_path)

    # Generate DEPLOY_VERSION speculatively. If the rendered compose ends
    # up referencing it (any name with the ``_<version>`` suffix), the
    # discovery step below will pick those up. If not, the version is just
    # an unused string in env — harmless.
    deploy_version = generate_deploy_version()
    os.environ["DEPLOY_VERSION"] = deploy_version

    # Decrypt stack-local secrets once. Values feed both compose
    # interpolation (via env vars) and create_versioned_secrets below.
    stack_secrets: list[tuple[str, str]] = []
    secrets_env = stack_path / "secrets.env"
    if secrets_env.is_file():
        info(f"Loading secrets: {secrets_env}")
        stack_secrets = sops_decrypt(secrets_env)
        for key, value in stack_secrets:
            os.environ[key] = value

    builds = discover_build_dirs(stack_path, stack_nm)
    for b in builds:
        os.environ[b["var_name"]] = b["tag"]
        info(f"Image: {b['var_name']}={b['tag']}")
        build_and_push(b["image"], b["dir"])
        info("")

    # Render the compose — the YAML form goes to docker stack deploy,
    # the JSON form drives discovery (versioned secrets, configs to validate).
    try:
        compose_yaml = compose_config(compose_file)
        rendered = compose_json(compose_file)
    except Exception as e:
        raise SwarmError(f"compose preprocessing failed: {e}") from e

    used_version = _provision_versioned_resources(
        rendered, deploy_version, stack_secrets,
    )

    return {
        "stack_name": stack_nm,
        "stack_path": stack_path,
        "deploy_version": deploy_version if used_version else "",
        "stack_secrets": stack_secrets,
        "builds": builds,
        "compose_yaml": compose_yaml,
        "compose_json": rendered,
    }


def _provision_versioned_resources(
    compose_json: dict,
    deploy_version: str,
    stack_secrets: list[tuple[str, str]],
) -> bool:
    """Create versioned Docker secrets and validate config files for a stack.

    Two independent concerns happen here:

      * **Versioned secrets** — only relevant if the rendered compose has
        ``secrets.<x>.name`` entries suffixed with ``_<deploy_version>``.
        When present: validate each base name resolves to a value (in
        ``stack_secrets`` or the environment) and create the Docker secrets.
      * **Config-file existence** — runs unconditionally. A stack can
        declare ``configs:`` with ``file:`` references regardless of whether
        it uses versioned secrets, and Docker resolves those file paths at
        deploy time. Catching missing files here gives a precise error
        location instead of a generic ``docker stack deploy`` failure.

    Returns:
        True if the stack used the deploy version (versioned secrets were
        discovered and processed), False if it's a plain env-var stack.
    """
    # Config-file existence is independent of versioning — always validate.
    validate_config_files(compose_json)

    needed = required_versioned_secrets(compose_json, deploy_version)
    if not needed:
        info("Stack uses env vars only")
        return False

    info(f"Deploy version: {deploy_version}")
    validate_required_secrets(compose_json, deploy_version, stack_secrets=stack_secrets)
    create_versioned_secrets(compose_json, deploy_version, stack_secrets=stack_secrets)
    info("")
    return True


def _docker_stack_deploy(stack_nm: str, preprocessed: str, *, update: bool) -> int:
    """Stream `docker stack deploy` against the cluster. Returns exit code."""
    resolve_image = "always" if update else "changed"
    args = [
        "stack", "deploy",
        "--detach",
        "--prune",
        "--with-registry-auth",
        "--resolve-image", resolve_image,
        "-c", "-",
        stack_nm,
    ]
    try:
        # line_prefixed=True so docker's own progress lines ("Creating service X",
        # "Updating config Y") get the stack prefix and match the formatting of
        # our info() calls.
        _docker.stream(*args, input=preprocessed, line_prefixed=True)
    except Exception as e:
        error(f"docker stack deploy failed: {e}")
        return 1
    return 0


def _print_services(stack_nm: str) -> None:
    """Render the post-deploy service replica snapshot to stderr.

    The data is informational, not pipeable, so it goes to stderr per the
    strict I/O contract — only `swarm.status` outputs tables on stdout.
    """
    services = stack_services(stack_nm)
    if not services:
        return
    rows = [[name, replicas] for name, replicas in services]
    table(["SERVICE", "REPLICAS"], rows, file=sys.stderr)


def deploy_stack(stack_path: Path, *, update: bool = False) -> int:
    """Run the full deploy pipeline for one stack. Returns 0 on success, 1 otherwise."""
    init_stack_prefix(stack_name(stack_path))

    try:
        ctx = _prepare_stack(stack_path)
    except SwarmError as e:
        error(str(e))
        return 1

    info(f"Deploying: {ctx['stack_name']}")

    if _docker_stack_deploy(ctx["stack_name"], ctx["compose_yaml"], update=update) != 0:
        return 1

    timeout = int(os.environ.get("CONVERGE_TIMEOUT", "180"))
    max_interval = int(os.environ.get("CONVERGE_MAX_INTERVAL", "15"))
    converged, unhealthy = _convergence.verify(
        ctx["stack_name"], timeout=timeout, max_interval=max_interval,
    )

    _print_services(ctx["stack_name"])

    if ctx.get("deploy_version"):
        info(f"Deployed with DEPLOY_VERSION={ctx['deploy_version']}")

    if not converged:
        error(f"Convergence timeout after {timeout}s")
        return 1
    if unhealthy:
        names = ", ".join(u["name"] for u in unhealthy)
        error(f"Unhealthy services after convergence: {names}")
        return 1
    return 0


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="swarm.deploy")
        parser.add_argument("stack", help="Stack name or path")
        parser.add_argument(
            "--update",
            action="store_true",
            help="Re-resolve mutable image tags (e.g. latest, release) against the registry",
        )
        args = parser.parse_args()
        return deploy_stack(resolve_stack_path(args.stack), update=args.update)
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
