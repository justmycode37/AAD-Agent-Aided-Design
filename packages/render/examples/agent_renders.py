"""Reproduce the agent render samples without opening a window."""

from pathlib import Path
from types import SimpleNamespace
import sys

from build123d import Axis, Box, Cylinder, Pos, Rot, Sphere, fillet
from aad_render import render


def housing():
    part = fillet(Box(100, 70, 30).edges().filter_by(Axis.Z), 8)
    part -= Pos(0, 0, 4) * Box(84, 54, 26)
    part += Pos(0, 0, -5) * Cylinder(14, 20)
    part -= Pos(0, 0, -5) * Cylinder(8, 40)
    for x in (-38, 38):
        for y in (-23, 23):
            part -= Pos(x, y, 0) * Cylinder(2.5, 60)
    part += Pos(0, -35, 0) * Rot(90, 0, 0) * Cylinder(7, 14)
    part -= Pos(0, -35, 0) * Rot(90, 0, 0) * Cylinder(4, 40)
    return part


def main(out: Path) -> None:
    # This counterbore intentionally clips the upright: a visual review should catch it.
    bracket = Box(80, 50, 6) + Pos(0, 22, 25.5) * Box(80, 6, 54)
    bracket -= Pos(0, 19, 1) * Cylinder(2.5, 12)
    bracket -= Pos(0, 19, 2) * Cylinder(5, 4)
    ctx = SimpleNamespace(shape=bracket, units="mm", features=[])
    render(ctx, {"views": ["sheet"]}, out / "sheet")
    render(ctx, {"views": ["iso"], "section": {"plane": "YZ", "offset": 0}}, out / "section")
    render(ctx, {"views": ["iso"], "heatmap": "thickness"}, out / "thickness")
    render(ctx, {"views": ["iso"], "labels": "faces"}, out / "labels")
    ctx.shape = Sphere(20)
    render(ctx, {"views": ["sheet"], "heatmap": "overhang"}, out / "overhang")
    ctx.shape = Box(60, 40, 6)
    render(ctx, {"views": ["iso"], "section": {"plane": "XY", "offset": -100}}, out / "empty-section")
    ctx.shape = housing()
    for name, request in {
        "housing-sheet": {"views": ["sheet"]},
        "housing-section": {"views": ["iso"], "section": {"plane": "YZ", "offset": 0}},
        "housing-thickness": {"views": ["iso"], "heatmap": "thickness", "min_wall": 3},
        "housing-curvature": {"views": ["iso"], "heatmap": "curvature"},
        "housing-draft": {"views": ["front"], "heatmap": "draft", "direction": [0, 0, 1]},
        "housing-focus": {"views": ["top"], "focus": {"min": [28, 13, -5], "max": [48, 33, 20]}},
        "housing-probe": {
            "views": ["sheet"],
            "probes": [{"point": [0, -100, 0], "direction": [0, 1, 0]}],
            "measure": [{"a": "point:(0,0,0)", "b": "point:(38,23,0)"}],
        },
    }.items():
        render(ctx, request, out / name)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
