"""Generate the S4 layout review sheets and their source contexts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from build123d import Box, Compound, Cylinder, Pos

from aiad_drafting import DrawingRequest, make_drawing


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_ROOT = PACKAGE_ROOT / "samples" / "layout"


def build_dense_mixed_hole_plate() -> SimpleNamespace:
    plate = Box(120.0, 84.0, 8.0)
    holes = (
        (-45.0, -30.0, 3.0),
        (-15.0, -30.0, 3.0),
        (15.0, -30.0, 4.0),
        (45.0, -30.0, 4.0),
        (-45.0, 30.0, 3.0),
        (-15.0, 30.0, 3.0),
        (15.0, 30.0, 4.0),
        (45.0, 30.0, 4.0),
        (0.0, 0.0, 2.5),
        (35.0, 0.0, 2.5),
    )
    for x, y, radius in holes:
        plate = plate - (Pos(x, y, 0.0) * Cylinder(radius, 10.0))
    return SimpleNamespace(
        shape=plate,
        units="mm",
        name="Dense mixed-hole fixture plate",
        process="cnc_3axis",
        children=[],
    )


def build_simple_turned_part() -> SimpleNamespace:
    turned = Cylinder(20.0, 8.0)
    turned += Pos(0.0, 0.0, 8.0) * Cylinder(14.0, 12.0)
    turned += Pos(0.0, 0.0, 24.0) * Cylinder(10.0, 22.0)
    turned += Pos(0.0, 0.0, 40.0) * Cylinder(16.0, 10.0)
    return SimpleNamespace(
        shape=turned,
        units="mm",
        name="Stepped turned shaft",
        process="cnc_3axis",
        children=[],
    )


def build_two_part_assembly() -> SimpleNamespace:
    base = Box(80.0, 50.0, 8.0) - Pos(20.0,0.0,0.0)*Cylinder(5.1,10.0)
    pin = Pos(20.0, 0.0, 9.0) * Cylinder(5.0, 12.0)
    assembly_shape = Compound(children=[base, pin])
    return SimpleNamespace(
        shape=assembly_shape,
        units="mm",
        name="Two-part locating fixture",
        process="cnc_3axis",
        children=[
            {
                "name": "Base plate",
                "part_number": "BASE-01",
                "description": "Machined base plate",
                "shape": base,
            },
            {
                "name": "Locating pin",
                "part_number": "PIN-01",
                "description": "Vertical locating pin",
                "shape": pin,
            },
        ],
    )


def _generate(name: str, context: Any, request: DrawingRequest, output: Path):
    return make_drawing(context, request, output / name)


def generate_layout_samples(output: Path = SAMPLES_ROOT) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    results = {
        "dense_mixed_hole_plate": _generate(
            "dense-mixed-hole-plate",
            build_dense_mixed_hole_plate(),
            DrawingRequest(
                sheet="A3",
                views=("front", "top", "right", "iso"),
                dimensions="auto",
                style="iso",
                title_block={"title": "Dense mixed-hole fixture plate", "drawing_no": "AIAD-S4-PLATE-02"},
                notes=("Hole diameters and ordinate values are measured from the projected solid.",),
                formats=("svg", "dxf", "pdf"),
            ),
            output,
        ),
        "simple_turned_part": _generate(
            "simple-turned-part",
            build_simple_turned_part(),
            DrawingRequest(
                sheet="A4",
                views=("front", "top", "iso"),
                dimensions="auto",
                style="iso",
                title_block={"title": "Stepped turned shaft", "drawing_no": "AIAD-S4-TURN-01"},
                formats=("svg", "dxf", "pdf"),
            ),
            output,
        ),
        "two_part_assembly": _generate(
            "two-part-assembly",
            build_two_part_assembly(),
            DrawingRequest(
                sheet="A3",
                views=("front", "top", "right", "iso"),
                dimensions="auto",
                style="asme",
                title_block={"title": "Two-part locating fixture", "drawing_no": "AIAD-S4-ASM-01"},
                notes=("Assembly children shown as listed in the source assembly tree.",),
                revisions=(
                    {"revision": "A", "date": "2026-11-04", "description": "Initial drawing layout", "by": "AIAD"},
                ),
                bom=True,
                formats=("svg", "dxf", "pdf"),
            ),
            output,
        ),
    }
    return results


if __name__ == "__main__":
    for name, result in generate_layout_samples().items():
        print(f"{name}: {result.summary}")
        print(f"  preview: {result.preview}")
