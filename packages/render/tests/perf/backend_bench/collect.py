"""Run the one-day renderer comparison, with bounded independent processes."""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    packages = []
    for name in ("build123d", "cadquery-ocp-novtk", "numpy", "pillow", "vtk", "moderngl", "glcontext"):
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        packages.append({
            "name": name,
            "version": distribution.version,
            "installed_file_bytes": sum(
                path.stat().st_size for file in distribution.files or []
                if (path := distribution.locate_file(file)).is_file()
            ),
        })
    report = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "packages": packages,
        "runs": [],
    }
    runner = Path(__file__).with_name("run.py")
    failed = False
    for backend in ("ocp", "moderngl", "numpy", "vtk"):
        for phase in ("import", "a", "b"):
            command = [sys.executable, str(runner)]
            if phase == "import":
                command += ["--probe-import", backend]
            else:
                command += ["--backend", backend, "--output", str(out / f"{backend}-{phase}.png")]
            row = {"backend": backend, "phase": phase}
            try:
                result = subprocess.run(command, text=True, capture_output=True, timeout=90)
                row["returncode"] = result.returncode
                if result.returncode:
                    row["stderr"] = result.stderr[-4000:]
                    failed = True
                else:
                    row.update(json.loads(result.stdout))
                    row.pop("output", None)  # keep the report portable between checkouts
            except subprocess.TimeoutExpired:
                row["timeout_s"] = 90
                failed = True
            report["runs"].append(row)
            print(json.dumps(row), flush=True)
            (out / "measurements.json").write_text(json.dumps(report, indent=2) + "\n")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
