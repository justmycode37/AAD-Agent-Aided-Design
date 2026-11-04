"""Reproduce the agent render samples without opening a window."""

from pathlib import Path
from types import SimpleNamespace
import sys

from build123d import Box, Cylinder, Pos, Sphere
from aiad_render import render


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


if __name__ == "__main__":
    main(Path(sys.argv[1]))
