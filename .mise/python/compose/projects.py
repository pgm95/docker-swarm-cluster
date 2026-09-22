"""Project listing for shell completion and scripting.

Prints one project per line to stdout, sorted by host then project. Names
(``host/project``) by default, directory paths with ``--paths``.
"""

import argparse
import sys

from core.cli import cli_main

from ._project import all_projects, project_name


def main() -> int:
    def run() -> int:
        parser = argparse.ArgumentParser(prog="compose.projects")
        parser.add_argument("--paths", action="store_true", help="Print directory paths instead of names")
        args = parser.parse_args()
        for d in all_projects():
            print(d if args.paths else project_name(d))
        return 0
    return cli_main(run)


if __name__ == "__main__":
    sys.exit(main())
