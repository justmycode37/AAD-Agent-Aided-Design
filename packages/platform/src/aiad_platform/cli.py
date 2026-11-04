"""The `aiad` command line."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiad", description="CAD for agents.")
    sub = parser.add_subparsers(dest="command", required=True)
    version = sub.add_parser("version", help="print the installed version")
    version.add_argument("--json", action="store_true", help="machine-readable output")

    args = parser.parse_args(argv)
    if args.command == "version":
        print(json.dumps({"version": __version__}) if args.json else __version__)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
