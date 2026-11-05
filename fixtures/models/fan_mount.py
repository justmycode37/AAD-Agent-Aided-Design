"""A four-hole fan mounting plate built with plain build123d contexts."""

from aad import *

P = params(
    width=60.0,
    height=40.0,
    thickness=6.0,
    hole_diameter=4.4,
    hole_spacing_x=46.0,
    hole_spacing_y=26.0,
)

with BuildPart() as fan_mount:
    Box(
        P.width,
        P.height,
        P.thickness,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    with Locations(
        (-P.hole_spacing_x / 2, -P.hole_spacing_y / 2, -1),
        (P.hole_spacing_x / 2, -P.hole_spacing_y / 2, -1),
        (-P.hole_spacing_x / 2, P.hole_spacing_y / 2, -1),
        (P.hole_spacing_x / 2, P.hole_spacing_y / 2, -1),
    ):
        Cylinder(
            P.hole_diameter / 2,
            P.thickness + 2,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
            mode=Mode.SUBTRACT,
        )
