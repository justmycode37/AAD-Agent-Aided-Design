"""One named plate, four through holes, and four rounded outside corners."""

from aad import *

P = params(
    width=Param(60.0, 40.0, 100.0, "mm"),
    depth=40.0,
    thickness=Param(6.0, 2.0, 12.0, "mm"),
    hole_d=Param(4.4, 2.0, 8.0, "mm"),
    spacing=(46.0, 26.0),
    fillet_r=Param(2.0, 0.5, 4.0, "mm"),
)
meta(name="fan_mount", units="mm", material="steel_1018", process="cnc_3axis")

with part("bracket") as bp:
    with feature("plate", intent="rectangular stock"):
        Box(P.width, P.depth, P.thickness)

    with feature("mount_holes", intent="M4 clearance"):
        top = face(feature="plate", where="max_z", expect=1)
        with Locations(top):
            with GridLocations(*P.spacing, 2, 2):
                Hole(radius=P.hole_d / 2)

    with feature("edge_round", intent="round outside corners"):
        fillet(edge(feature="plate", where="parallel_z", expect=4), radius=P.fillet_r)
