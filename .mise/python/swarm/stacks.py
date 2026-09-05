"""Stack listing for shell completion and scripting.

Prints one stack per line to stdout in deploy order (namespaces
alphabetically, ``NN_`` folder order within each). Names by default,
directory paths with ``--paths``.
"""

import argparse
import sys

from ._cli import cli_main
from ._stack import all_stacks, stack_name


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="swarm.stacks")
        parser.add_argument("--paths", action="store_true", help="Print directory paths instead of names")
        args = parser.parse_args()
        for d in all_stacks():
            print(d if args.paths else stack_name(d))
        return 0
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
