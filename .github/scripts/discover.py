#!/usr/bin/env python3
"""Print the GitHub Actions outputs that drive ci.yml.

packages=<JSON list of {dir, name}> for each workspace package with tests.
e2e=true|false when tests/e2e holds at least one test module.
viewer=true|false when viewer/package.json exists.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path


def has_tests(path: Path) -> bool:
    return any(path.rglob("test_*.py")) or any(path.rglob("*_test.py"))


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    packages = []
    for pyproject in sorted((root / "packages").glob("*/pyproject.toml")):
        pkg = pyproject.parent
        if has_tests(pkg / "tests"):
            name = tomllib.loads(pyproject.read_text())["project"]["name"]
            packages.append({"dir": pkg.name, "name": name})

    lines = [
        f"packages={json.dumps(packages, separators=(',', ':'))}",
        f"e2e={'true' if has_tests(root / 'tests' / 'e2e') else 'false'}",
        f"viewer={'true' if (root / 'viewer' / 'package.json').exists() else 'false'}",
    ]
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines), file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
