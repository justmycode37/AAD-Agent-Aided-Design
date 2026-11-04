#!/usr/bin/env python3
"""Print the GitHub Actions outputs that drive ci.yml.

On a PR, only what the change can affect runs, because macOS runners are scarce. On main, everything runs.

packages=<JSON list of {dir, name}> for each workspace package with tests.
e2e=true|false when tests/e2e holds at least one test module.
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
        ["git", "diff", "--name-only", f"origin/{base}...HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        print(f"Cannot diff against {base}, running everything: {done.stderr.strip()}", file=sys.stderr)
        return None
    return [line for line in done.stdout.splitlines() if line]


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    changed = changed_files(root)
    # Documentation changes run nothing. Anything else outside one package can affect them all.
    docs_only = lambda path: path.endswith(".md") or path.startswith("docs/")  # noqa: E731
    relevant = None if changed is None else [path for path in changed if not docs_only(path)]
    scoped = relevant is not None and all(path.startswith(("packages/", "viewer/")) for path in relevant)

    packages = []
    for pyproject in sorted((root / "packages").glob("*/pyproject.toml")):
        pkg = pyproject.parent
        if not has_tests(pkg / "tests"):
            continue
        if scoped and not any(path.startswith(f"packages/{pkg.name}/") for path in relevant):
            continue
        name = tomllib.loads(pyproject.read_text())["project"]["name"]
        packages.append({"dir": pkg.name, "name": name})

    # The smoke test exercises every package together, so any code change runs it.
    e2e = has_tests(root / "tests" / "e2e") and (relevant is None or bool(relevant))
    viewer = (root / "viewer" / "package.json").exists() and (
        relevant is None or not scoped or any(path.startswith("viewer/") for path in relevant)
    )
    lines = [
        f"packages={json.dumps(packages, separators=(',', ':'))}",
        f"e2e={'true' if e2e else 'false'}",
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
