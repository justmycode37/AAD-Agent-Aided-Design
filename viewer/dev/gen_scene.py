"""Writes the test scene from a real build123d part into the given directory, through make_scene.

Run from the repository root:
    uv run --locked --package aad-render --all-extras python viewer/dev/gen_scene.py viewer/tests/fixtures/bracket
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from build123d import Align, Axis, Box, BuildPart, CounterBoreHole, GridLocations, Hole, Locations, Plane, fillet

from aad_render import make_scene


def bracket():
    with BuildPart() as bp:
        Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, -17, 6)):
            Box(60, 6, 34, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations(Plane.XY.offset(6)):
            with GridLocations(46, 22, 2, 2):
                with Locations((0, 3)):
                    CounterBoreHole(radius=2.2, counter_bore_radius=4, counter_bore_depth=2.2, depth=6)
        with Locations(Plane(origin=(0, -14, 22), x_dir=(1, 0, 0), z_dir=(0, 1, 0))):
            Hole(radius=13, depth=6)
        fillet(bp.edges().filter_by(Axis.Y).group_by(Axis.Z)[-1], radius=4)
    return bp.part


def main(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        scene_dir = Path(tmp)
        context = SimpleNamespace(project="dev", project_dir=scene_dir, rev=1, kind="part", units="mm", shape=bracket(), features=[], params={}, metrics={}, spec={}, material=None, process=None, job_dir=scene_dir)
        manifest = make_scene(context, scene_dir)
        shutil.copyfile(scene_dir / "scene.bin.gz", out / f"{manifest.scene_id}.gz")
        (out / "manifest.json").write_text((scene_dir / "scene.json").read_text())
    print(json.dumps({"scene_id": manifest.scene_id, "triangles": manifest.triangles, "total_bytes": manifest.total_bytes}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
