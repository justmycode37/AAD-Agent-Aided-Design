"""An asymmetric smooth outline with rounded edges, in mm."""
from aad import *

P = params(height=Param(12.0, 8, 20))
with part("spline_profile"):
    with feature("organic_outline"):
        with BuildSketch():
            with BuildLine():
                Spline((30, 0), (22, 20), (-5, 26), (-30, 8),
                       (-20, -18), (12, -15), periodic=True)
            make_face()
        extrude(amount=P.height)
    with feature("soft_edges"):
        fillet(edges(), radius=1)
