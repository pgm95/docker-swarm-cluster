"""Shared boilerplate for module CLI entry points.

Every public module's `main()` follows the same pattern:
  1. configure logging via `_output.setup()`
  2. parse argparse arguments
  3. dispatch to business logic
  4. catch `ToolError` -> format to stderr, exit 1

`cli_main(work)` collapses (1) and (4) into a single wrapper. Modules call it
with their no-arg work callable; the callable parses args and returns an
exit code. `ToolError` raised anywhere inside `work` is caught and printed
through the standard `error()` formatter.

Usage:
    def main() -> int:
        return cli_main(_run)

    def _run() -> int:
        parser = argparse.ArgumentParser(...)
        args = parser.parse_args()
        return do_thing(args.stack)
"""

from collections.abc import Callable

from . import ToolError
from .output import error, setup


def cli_main(work: Callable[[], int]) -> int:
    """Wrap a CLI entry-point callable with `setup()` + `ToolError` formatting.

    `work` parses args, dispatches, and returns an exit code. Any
    `ToolError` raised inside is caught and converted to exit code 1
    with its message routed through `_output.error()`.
    """
    setup()
    try:
        return work()
    except ToolError as e:
        error(str(e))
        return 1
