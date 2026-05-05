"""SOPS decryption helpers."""

import base64
import subprocess
from pathlib import Path

from . import SopsError
from ._output import log


def sops_decrypt(file_path: str | Path) -> list[tuple[str, str]]:
    """Decrypt a SOPS-encrypted file and return clean key-value pairs.

    Handles both dotenv (.env) and YAML (.yml/.yaml) formats.
    Processes _B64 suffix: base64-decodes value and strips suffix from key.

    Args:
        file_path: Path to the SOPS-encrypted file.

    Returns:
        List of (key, value) tuples with _B64 suffix already resolved.
    """
    path_str = str(file_path)
    cmd = ["sops", "decrypt"]
    if path_str.endswith((".yml", ".yaml")):
        cmd.extend(["--output-type", "dotenv"])
    cmd.append(path_str)

    log.debug("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SopsError(f"Failed to decrypt {path_str}: {result.stderr.strip()}")

    pairs = []
    for line in result.stdout.splitlines():
        # Skip blanks, comments, and any line that isn't a `key=value` pair.
        # The previous `line.startswith("sops")` filter would have dropped
        # legitimate user keys like `sops_recipient`; the `=` check is what
        # actually distinguishes data lines from anything else sops might emit.
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if not key:
            continue

        if key.endswith("_B64"):
            key = key[:-4]
            try:
                value = base64.b64decode(value).decode()
            except Exception as e:
                raise SopsError(f"base64 decode failed for {key}_B64: {e}") from e

        pairs.append((key, value))
    return pairs
