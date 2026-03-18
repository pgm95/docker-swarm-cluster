"""Logging and output formatting for swarm CLI tools.

Convention:
  - Data output (eval-able exports, tables) -> stdout via print()
  - Progress/diagnostics -> stderr via log
"""

import logging
import sys

log = logging.getLogger("swarm")


def setup(verbose: bool = False) -> None:
    """Configure logging. Call once from __main__ entry points."""
    if log.handlers:
        return
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    log.setLevel(level)


def info(msg: str) -> None:
    log.info(msg)


def warn(msg: str) -> None:
    log.warning("WARNING: %s", msg)


def error(msg: str) -> None:
    log.error("ERROR: %s", msg)


def table(headers: list[str], rows: list[list[str]]) -> None:
    """Print an aligned table to stdout."""
    if not rows and not headers:
        return
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))
            else:
                col_widths.append(len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in col_widths)))
    for row in rows:
        padded = [str(row[i]) if i < len(row) else "" for i in range(len(col_widths))]
        print(fmt.format(*padded))
