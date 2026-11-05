#!/usr/bin/env python3
"""Print the GitHub Actions outputs that drive ci.yml.

PRs skip unrelated package tests only for docs, platform and viewer changes.
Shared CAD changes and main run every package. Every PR keeps the smoke test.

packages=<JSON list of {dir, name}> for each workspace package with tests.
e2e=true; discovery fails if the required smoke tests are missing.
viewer=true|false when viewer/package.json exists.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


def has_tests(path: Path) -> bool:
    return any(path.rglob("test_*.py")) or any(path.rglob("*_test.py"))


def changed_files(root: Path) -> list[str] | None:
    """Files this PR changes, or None to run everything (main, or any doubt)."""
    base = os.environ.get("GITHUB_BASE_REF")
    if not base:
        return None
    done = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", f"origin/{base}...HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        print(f"Cannot diff against {base}, running everything: {done.stderr.strip()}", file=sys.stderr)
        return None
    return [line for line in done.stdout.split("\0") if line]


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    changed = changed_files(root)
    # Only these files are documentation, not packaged runtime assets/playbooks.
    docs = {"README.md", "ARCHITECTURE.md", "CONTRACTS.md"}
    relevant = None if changed is None else [
        path for path in changed if path not in docs and not path.startswith("docs/")
    ]
    # CAD packages call one another dynamically, beyond declared dependencies.
    # Only platform and viewer are leaves; a shared CAD change runs every package.
    scoped = relevant is not None and all(
        path.startswith(("packages/platform/", "viewer/")) for path in relevant
    )

    packages = []
    for pyproject in sorted((root / "packages").glob("*/pyproject.toml")):
        pkg = pyproject.parent
        if not has_tests(pkg / "tests"):
            continue
        if scoped and not any(path.startswith(f"packages/{pkg.name}/") for path in relevant):
            continue
        name = tomllib.loads(pyproject.read_text())["project"]["name"]
        packages.append({"dir": pkg.name, "name": name})

    # PLAN section 8 requires smoke tests on every PR, including documentation.
    if not has_tests(root / "tests" / "e2e"):
        print("Required smoke tests are missing from tests/e2e", file=sys.stderr)
        return 1
    viewer = (root / "viewer" / "package.json").exists() and (
        relevant is None or not scoped or any(path.startswith("viewer/") for path in relevant)
    )
    lines = [
        f"packages={json.dumps(packages, separators=(',', ':'))}",
        "e2e=true",
        f"viewer={'true' if viewer else 'false'}",
    ]
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines), file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
