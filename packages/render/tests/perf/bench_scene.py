"""Warm make_scene timing against the 300 ms budget for parts of at most 200 faces.

Run with:
    uv run --locked --package aad-render --all-extras \
      python packages/render/tests/perf/bench_scene.py --repeat 20

Prints one JSON record per part. Times are measured in this process after the imports and one warm-up
call, because a warm worker has already paid for them. Every run tessellates geometry nothing has seen,
because repeating a part measures the mesh caches instead of the work. Geometry building is excluded.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from build123d import Align, Axis, Box, BuildPart, CounterBoreHole, GridLocations, Hole, Locations, Plane, fillet

from aad_render import make_scene

BUDGET_MS = 300.0


def bracket(step: float = 0.0):
    """The PLAN section 5 part: an L bracket with counterbores, a bore and fillets."""
    with BuildPart() as part:
        Box(60 + step, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, -17, 6)):
            Box(60 + step, 6, 34, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations(Plane.XY.offset(6)):
            with GridLocations(46, 22, 2, 2):
                with Locations((0, 3)):
                    CounterBoreHole(radius=2.2, counter_bore_radius=4, counter_bore_depth=2.2, depth=6)
        with Locations(Plane(origin=(0, -14, 22), x_dir=(1, 0, 0), z_dir=(0, 1, 0))):
            Hole(radius=13, depth=6)
        fillet(part.edges().filter_by(Axis.Y).group_by(Axis.Z)[-1], radius=4)
    return part.part


def hole_plate(columns: int, rows: int, step: float = 0.0):
    """A filleted plate of counterbored through holes: about 3 faces per hole."""
    with BuildPart() as part:
        Box(160 + step, 110, 8)
        fillet(part.edges().filter_by(Axis.Z), radius=6)
        with Locations(Plane.XY.offset(4)):
            with GridLocations(140 / (columns - 1), 90 / (rows - 1), columns, rows):
                CounterBoreHole(radius=2.2, counter_bore_radius=4.2, counter_bore_depth=2, depth=8)
    return part.part


def _context(shape, out: Path) -> SimpleNamespace:
    return SimpleNamespace(
        project_dir=out / "bench", rev=1, kind="part", units="mm", shape=shape, features=[],
        params={}, metrics={}, spec={}, material=None, process=None, job_dir=out,
    )  # fmt: skip


def measure(name: str, make, repeat: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        make_scene(_context(make(0.0), out), out / "warmup")
        times = []
        manifest = None
        shape = None
        for index in range(1, repeat + 1):
            shape = make(0.25 * index)  # Distinct geometry: OCCT and ocp-tessellate both cache meshes.
            started = time.perf_counter()
            manifest = make_scene(_context(shape, out), out / str(index))
            times.append((time.perf_counter() - started) * 1000)
    ordered = sorted(times)
    return {
        "part": name,
        "faces": len(shape.faces()),
        "edges": len(shape.edges()),
        "triangles": manifest.triangles,
        "compressed_bytes": manifest.total_bytes,
        "repeat": repeat,
        "median_ms": round(statistics.median(times), 1),
        "p95_ms": round(ordered[max(0, round(0.95 * len(ordered)) - 1)], 1),
        "sample_sd_ms": round(statistics.stdev(times), 1) if len(times) > 1 else None,
        "budget_ms": BUDGET_MS,
        "within_budget": statistics.median(times) <= BUDGET_MS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps({"python": platform.python_version(), "machine": platform.machine(), "system": platform.platform()}))
    parts = {
        "bracket": bracket,
        "plate_60_holes": lambda step: hole_plate(10, 6, step),
        "plate_96_holes": lambda step: hole_plate(12, 8, step),
    }
    for name, make in parts.items():
        print(json.dumps(measure(name, make, args.repeat)))


if __name__ == "__main__":
    main()
