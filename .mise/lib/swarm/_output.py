"""Logging and output formatting for swarm CLI tools.

Strict I/O contract:
  - stdout: machine-parseable data only (tables, lists, network names).
  - stderr: everything humans read (progress, warnings, errors, summaries).

`debug`/`info`/`warn`/`error` route to stderr via the module logger. `print()`
in `table()` is the only function that writes to stdout, and only with data
that is meaningful to pipe.

When a single Python invocation is operating on a known stack, call
`init_stack_prefix(name)` once at entry. `info`/`warn`/`error` will then
prepend `[<name>] ` to every line, making sequential mise-task loops easy
to follow visually.

The prefix is held in a `contextvars.ContextVar`, which gives proper
context-local isolation for the (currently hypothetical) cases of nested
CLI calls or per-thread/per-asyncio-task scoping. For today's
single-stack-per-process model the behavior is identical to a global,
but the shape is the right one for any future concurrency.
"""

import contextvars
import logging
import sys

log = logging.getLogger("swarm")

_stack_prefix: contextvars.ContextVar[str] = contextvars.ContextVar(
    "swarm_stack_prefix", default="",
)


def setup(verbose: bool = False) -> None:
    """Configure logging. Call once from __main__ entry points."""
    if log.handlers:
        return
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    log.setLevel(level)


def init_stack_prefix(name: str) -> None:
    """Set a per-context stack-name prefix prepended to every info/warn/error line."""
    _stack_prefix.set(f"[{name}] " if name else "")


def get_stack_prefix() -> str:
    """Return the current stack prefix (empty string if unset).

    Public accessor so callers (notably `_docker.stream(line_prefixed=True)`)
    don't reach into the ContextVar object directly.
    """
    return _stack_prefix.get()


def debug(msg: str) -> None:
    log.debug("%s%s", _stack_prefix.get(), msg)


def info(msg: str) -> None:
    log.info("%s%s", _stack_prefix.get(), msg)


def warn(msg: str) -> None:
    log.warning("%sWARNING: %s", _stack_prefix.get(), msg)


def error(msg: str) -> None:
    log.error("%sERROR: %s", _stack_prefix.get(), msg)


def table(headers: list[str], rows: list[list[str]], wrap_width: int = 3, file=None) -> None:
    """Print an aligned table. Last column wraps at `wrap_width` words per line.

    Defaults to stdout (the table is pipeable data, e.g. for `swarm.status`).
    Pass `file=sys.stderr` for tables that are progress output rather than data
    (e.g. the post-deploy services snapshot).
    """
    if not rows and not headers:
        return
    out = file if file is not None else sys.stdout
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))
            else:
                col_widths.append(len(str(cell)))
    # Don't right-pad the last column
    parts = [f"{{:<{w}}}" for w in col_widths[:-1]] + ["{}"]
    fmt = "  ".join(parts)
    prefix_width = sum(col_widths[:-1]) + 2 * (len(col_widths) - 1)

    print(fmt.format(*headers), file=out)
    sep_widths = col_widths[:-1] + [len(headers[-1])]
    print(fmt.format(*("-" * w for w in sep_widths)), file=out)
    indent = " " * prefix_width
    for row in rows:
        padded = [str(row[i]) if i < len(row) else "" for i in range(len(col_widths))]
        last = padded[-1]
        words = last.split()
        if len(words) > wrap_width:
            for i in range(0, len(words), wrap_width):
                chunk = " ".join(words[i:i + wrap_width])
                if i == 0:
                    padded[-1] = chunk
                    print(fmt.format(*padded), file=out)
                else:
                    print(f"{indent}{chunk}", file=out)
        else:
            print(fmt.format(*padded), file=out)
