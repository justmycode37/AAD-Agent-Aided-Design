#!/usr/bin/env python3
"""Measure 100 catalog detailed fasteners through the real AAD build worker."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--component", default="fastener.iso4762.M3-0.5")
    parser.add_argument("--length-mm", type=float, default=8.0)
    parser.add_argument("--lod", choices=("fit", "full"), default="full")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    if args.length_mm <= 0:
        parser.error("--length-mm must be positive")

    source = f"""\
from build123d import Compound, Pos
from aad_quality.components import make_detailed

parts = [
    Pos(index * 12, 0, 0) * make_detailed(
        {args.component!r}, length_mm={args.length_mm!r}, lod={args.lod!r}
    )
    for index in range({args.count})
]
result = Compound(children=parts)
"""
    with tempfile.TemporaryDirectory(prefix="aad-detailed-fasteners-") as temporary:
        project_dir = Path(temporary)
        built = run_build(BuildRequest(
            source=source,
            kind="assembly",
            project_dir=project_dir,
            job_dir=project_dir / "job",
            timeout_s=900,
        ))
    if not built.ok:
        print(json.dumps({"passed": False, "failure": built.failure.message if built.failure else built.summary}, indent=2))
        return 1

    hard_errors = [check for check in built.checks if check.suite == "hard" and check.level == "error"]
    passed = bool(hard_errors) and all(check.status == "pass" for check in hard_errors)
    metadata = built.context.meta.get("aad_component_geometries", []) if built.context else []
    result = {
        "passed": passed,
        "component_id": args.component,
        "count": args.count,
        "length_mm": args.length_mm,
        "lod": args.lod,
        "build_ms": built.build_ms,
        "phase_timings_ms": built.metrics.get("phase_timings_ms", {}),
        "geometry_metrics": {
            key: built.metrics.get(key)
            for key in ("solids", "faces", "edges", "valid", "bbox")
        },
        "metadata_entries": len(metadata),
        "hard_error_checks": {check.id: check.status for check in hard_errors},
        "scope": "one real aad_build worker run including B-rep serialization and hard-gate checks; elapsed time is machine-specific",
    }
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
