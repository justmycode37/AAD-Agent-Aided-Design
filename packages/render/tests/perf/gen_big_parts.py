"""Generate real high-triangle and near-5-MB scenes for viewer profiling.

Run with:
    uv run --locked --package aad-render --all-extras \
      python packages/render/tests/perf/gen_big_parts.py /tmp/aad-render-perf

The generated STEP-free scene directories are performance fixtures; keep them
outside the repository and do not commit them.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace

from build123d import (
    Align,
    Axis,
    Box,
    BuildPart,
    CounterBoreHole,
    GeomType,
    GridLocations,
    Locations,
    Plane,
    fillet,
)

from aad_render import make_scene


def many_hole_plate(columns: int, rows: int):
    """A plate with hundreds of bored holes and edge rounds, not a synthetic mesh."""
    width = 320.0
    height = 220.0
    thickness = 12.0
    with BuildPart() as plate:
        Box(width, height, thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
        fillet(plate.edges().filter_by(Axis.Z), radius=8.0)

        with Locations(Plane.XY.offset(thickness)):
            with GridLocations(
                (width - 50) / (columns - 1),
                (height - 50) / (rows - 1),
                columns,
                rows,
            ):
                CounterBoreHole(
                    radius=2.2,
                    counter_bore_radius=4.2,
                    counter_bore_depth=2.5,
                    depth=thickness,
                )

        top_rims = [
            edge
            for edge in plate.edges().filter_by(GeomType.CIRCLE)
            if abs(edge.center().Z - thickness) < 1e-5
        ]
        pitch = min((width - 50) / (columns - 1), (height - 50) / (rows - 1))
        rim_radius = min(0.25, max(0.02, (pitch - 2 * 4.2) * 0.15))
        fillet(top_rims, radius=rim_radius)
    return plate.part


def _context(shape, output_dir: Path, revision: int, deviation: float, angular_tolerance: float):
    return SimpleNamespace(
        project_dir=output_dir / "project",
        rev=revision,
        kind="part",
        units="mm",
        shape=shape,
        features=[],
        params={},
        metrics={},
        spec={},
        material=None,
        process=None,
        job_dir=output_dir,
        scene_options={"deviation": deviation, "angular_tolerance": angular_tolerance},
    )


def _generate_case(
    output_dir: Path,
    name: str,
    columns: int,
    rows: int,
    deviation: float,
    angular_tolerance: float,
    revision: int,
) -> dict[str, object]:
    build_start = time.perf_counter()
    shape = many_hole_plate(columns, rows)
    build_ms = (time.perf_counter() - build_start) * 1000

    # Build123d/OCCT is already loaded by the modeling worker; import it before
    # the timer to record warm scene generation rather than process startup.
    import ocp_tessellate.convert  # noqa: F401

    scene_dir = output_dir / name
    scene_start = time.perf_counter()
    manifest = make_scene(
        _context(shape, output_dir, revision, deviation, angular_tolerance),
        scene_dir,
    )
    scene_ms = (time.perf_counter() - scene_start) * 1000
    return {
        "name": name,
        "faces": len(shape.faces()),
        "triangles": manifest.triangles,
        "compressed_bytes": manifest.total_bytes,
        "build_ms": round(build_ms, 1),
        "scene_ms": round(scene_ms, 1),
        "scene_dir": str(scene_dir),
        "deviation": deviation,
        "angular_tolerance": angular_tolerance,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="directory for generated scene fixtures")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    results = [
        _generate_case(args.out, "fine-500k", 32, 16, 0.0008, 0.025, 1),
        _generate_case(args.out, "near-5mb", 24, 12, 0.012, 0.055, 2),
    ]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
