"""Generate a small, asymmetric drilled plate drawing for the S4 first sheet."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from build123d import Box, Cylinder, Pos

from aad_drafting import DrawingRequest, make_drawing


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_OUTPUT = PACKAGE_ROOT / "samples" / "first-sheet"
HOLE_CENTERS = (
    (-27.0, -15.0),
    (23.0, -15.0),
    (-27.0, 18.0),
    (23.0, 18.0),
)


def build_sample_context() -> SimpleNamespace:
    plate = Box(80.0, 50.0, 6.0)
    for x, y in HOLE_CENTERS:
        plate = plate - (Pos(x, y, 0.0) * Cylinder(3.0, 8.0))
    return SimpleNamespace(
        shape=plate,
        units="mm",
        name="Asymmetric drilled plate",
        project="first-sheet-sample",
        features=[],
        params={},
        metrics={},
        spec={},
        material=None,
        process="cnc_3axis",
    )


def generate(output: Path = SAMPLE_OUTPUT):
    context = build_sample_context()
    request = DrawingRequest(
        sheet="A4",
        views=("front", "top", "right", "iso"),
        dimensions="auto",
        style="iso",
        title_block={
            "title": "Asymmetric drilled plate",
            "drawing_no": "AAD-S4-001",
            "revision": "A",
        },
        formats=("svg", "dxf", "pdf"),
    )
    return make_drawing(context, request, output)


if __name__ == "__main__":
    result = generate()
    print(result.summary)
    print(f"Preview: {result.preview}")
    for format_name, artifact in result.artifacts.items():
        print(f"{format_name.upper()}: {artifact}")
