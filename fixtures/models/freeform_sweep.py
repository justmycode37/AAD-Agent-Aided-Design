"""A circular section following a spatial spline, in mm."""
from aad import *

P = params(radius=Param(3.0, 1, 5))
with BuildLine() as path:
    Spline((0, 0, 0), (20, 0, 10), (35, 20, 25), (60, 15, 40))
with part("freeform_sweep"):
    with feature("curved_tube"):
        with BuildSketch(Plane(origin=path.line @ 0, z_dir=path.line % 0)):
            Circle(P.radius)
        sweep(path=path.line)
