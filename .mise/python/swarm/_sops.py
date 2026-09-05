"""SOPS decryption helpers."""

import json
import subprocess
from pathlib import Path

from . import SopsError
from ._output import log


def _scalar_to_str(key: str, value: object, path_str: str) -> str:
    """Coerce a decrypted scalar to the string form env vars and Docker secrets expect."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float, str)):
        return str(value)
    raise SopsError(
        f"{path_str}: {key} must be a scalar, got {type(value).__name__} "
        "(nested values have no env var or Docker secret form)"
    )


def sops_decrypt(file_path: str | Path) -> list[tuple[str, str]]:
    """Decrypt a SOPS-encrypted file and return its flat key-value pairs.

    The file is rendered through ``sops decrypt --output-type json`` so the
    on-disk store (YAML, dotenv, JSON) does not matter and multi-line values
    survive intact. Only a flat mapping of scalars is accepted.

    Args:
        file_path: Path to the SOPS-encrypted file.

    Returns:
        List of (key, value) tuples in document order, values as strings.
    """
    path_str = str(file_path)
    cmd = ["sops", "decrypt", "--output-type", "json", path_str]

    log.debug("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SopsError(f"Failed to decrypt {path_str}: {result.stderr.strip()}")

    stdout = result.stdout.strip()
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise SopsError(f"Unexpected sops output for {path_str}: {e}") from e
    if not isinstance(data, dict):
        raise SopsError(f"{path_str}: expected a mapping at the top level")

    pairs = []
    for key, value in data.items():
        # `sops decrypt` strips its own metadata block, but never export it
        # as a secret if a store ever leaves it in place.
        if key == "sops":
            continue
        pairs.append((key, _scalar_to_str(key, value, path_str)))
    return pairs
