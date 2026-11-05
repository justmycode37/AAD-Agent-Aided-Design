"""Tapered, hollow casting concept; dimensions in mm, not a production gearbox."""
from aad import *

P = params(length=Param(120.0, 100, 160), wall=Param(4.0, 3, 6))
meta(units="mm", name="Curved housing concept")

with part("curved_housing") as housing:
    # Several curved sections control the silhouette instead of a box extrusion.
    with feature("outer_casting", intent="smooth tapered casting envelope"):
        for z, rx, ry in ((0, 52, 42), (35, 48, 38), (P.length, 30, 27)):
            with BuildSketch(Plane.XY.offset(z)):
                Ellipse(rx, ry)
        loft()
    with feature("cavity", intent="open ends and explicit inner envelope"):
        # Extend the cutter beyond both ends to avoid coincident caps.
        for z, rx, ry in ((-1, 52, 42), (35, 48, 38), (P.length + 1, 30, 27)):
            with BuildSketch(Plane.XY.offset(z)):
                Ellipse(rx - P.wall, ry - P.wall)
        loft(mode=Mode.SUBTRACT)
    with feature("mounting_flange", intent="round bolted interface"):
        with BuildSketch():
            Ellipse(61, 51)
            Ellipse(52 - P.wall, 42 - P.wall, mode=Mode.SUBTRACT)
        extrude(amount=6)
        with Locations((56, 0, 0), (-56, 0, 0), (0, 46, 0), (0, -46, 0)):
            Cylinder(2.5, 6, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)

result = housing.part
