"""Platinum-tone deployant clasp for the Jaeger-LeCoultre Q3946420 Reverso.

The fragment uses the Reverso watch frame: X is the 18 mm strap width, Y is
the loop tangent, and Z points away from the reverse face.  The clasp datum is
the underside of the closed loop at (0, 0, -44 mm).  Its 32 mm local bend
radius is a fit assumption for a visual assembly, not a measured wrist size.

The blades, interleaved hinge, strap attachment eyes, pushbuttons and cover are
explicit separate parts.  The detail is representative geometry: a sampled
equal-and-opposite blade swing has near-zero overlap through 60 degrees, but
the blades interfere substantially by 90 degrees.  Full opening, button
travel, latch geometry, strap fit and load capacity are not validated.
The parent fragment runner provides emit(name, shape, hexcolor).
"""
from build123d import *
import math


STRAP_WIDTH = 18.0
LOOP_RADIUS = 32.0
LOOP_CENTER_Z = -12.0
CLASP_BOTTOM_Z = LOOP_CENTER_Z - LOOP_RADIUS  # -44 mm at y=0

PLATINUM = "#D9DBD9"
POLISHED = "#F1F2EF"
BRUSHED = "#B6BBB9"
DARK = "#565C5B"


def _arc_point(radius, angle, x):
    """Point on the lower wrist arc in the YZ plane."""
    return Vector(x, radius * math.sin(angle),
                  LOOP_CENTER_Z - radius * math.cos(angle))


def _curved_band(inner_radius, outer_radius, angle_start, angle_end, width):
    """Make a constant-width annular arc swept across X."""
    x0 = -width / 2
    mid = (angle_start + angle_end) / 2
    points = (
        _arc_point(outer_radius, angle_start, x0),
        _arc_point(outer_radius, mid, x0),
        _arc_point(outer_radius, angle_end, x0),
        _arc_point(inner_radius, angle_end, x0),
        _arc_point(inner_radius, mid, x0),
        _arc_point(inner_radius, angle_start, x0),
    )
    edges = [
        Edge.make_three_point_arc(points[0], points[1], points[2]),
        Edge.make_line(points[2], points[3]),
        Edge.make_three_point_arc(points[3], points[4], points[5]),
        Edge.make_line(points[5], points[0]),
    ]
    profile = Face(Wire.combine(edges)[0])
    band = extrude(profile, amount=width, dir=(1, 0, 0))
    # Small edge breaks soften the exposed widthwise corners without changing
    # the curved fit surface or the pin interfaces.
    return fillet(band.edges().filter_by(Axis.X), radius=0.10)


def _x_cylinder(x, y, z, radius, length):
    """Cylinder centered at (x,y,z) with its axis parallel to the strap width."""
    return Pos(x, y, z) * Rot(0, 90, 0) * Cylinder(radius, length)


def _x_tube(x, y, z, outer_radius, bore_radius, length):
    return (_x_cylinder(x, y, z, outer_radius, length)
            - _x_cylinder(x, y, z, bore_radius, length + 0.04))


# The two blades each cover one half of the lower loop.  Their central hinge
# barrels alternate across X in six short knuckles (2.55 mm long, 0.45 mm gaps).
hinge_knuckles = [
    (-7.55, -1), (-4.55, 1), (-1.55, -1),
    (1.45, 1), (4.45, -1), (7.45, 1),
]
knuckle_length = 2.55
hinge_bore = 0.40
hinge_outer = 0.72
# The two leaves occupy offset radial layers to represent a stacked deployant
# pair around the common hinge axis.  Full articulation remains unvalidated.
blade_radii = {
    -1: (LOOP_RADIUS - 0.60, LOOP_RADIUS + 0.15),
    1: (LOOP_RADIUS + 0.25, LOOP_RADIUS + 1.00),
}
end_y = 22.0
end_angle = math.asin(end_y / LOOP_RADIUS)

for side, label in ((-1, "Left"), (1, "Right")):
    blade_inner, blade_outer = blade_radii[side]
    if side < 0:
        blade = _curved_band(blade_inner, blade_outer,
                             -end_angle, 0.0, STRAP_WIDTH - 0.35)
    else:
        blade = _curved_band(blade_inner, blade_outer,
                             0.0, end_angle, STRAP_WIDTH - 0.35)

    # Add this blade's hinge knuckles and clear the alternate knuckles from
    # its end face so the two leaves interleave around one continuous pin.
    for x, owner in hinge_knuckles:
        if owner == side:
            blade += _x_tube(x, 0.0, CLASP_BOTTOM_Z,
                             hinge_outer, hinge_bore, knuckle_length)
        else:
            blade -= _x_cylinder(x, 0.0, CLASP_BOTTOM_Z,
                                 hinge_outer + 0.05, knuckle_length + 0.08)
    blade -= _x_cylinder(0.0, 0.0, CLASP_BOTTOM_Z,
                         hinge_bore, STRAP_WIDTH + 0.12)

    # A transverse eye at each blade tip accepts the 18 mm strap end and its
    # removable spring bar.  The eye follows the same wrist arc as the blade.
    end_z = LOOP_CENTER_Z - LOOP_RADIUS * math.cos(side * end_angle)
    blade += _x_tube(0.0, side * end_y, end_z, 0.72, 0.40, STRAP_WIDTH - 0.15)
    blade -= _x_cylinder(0.0, side * end_y, end_z,
                         0.40, STRAP_WIDTH + 0.12)
    emit(f"Deployant Curved Folding Blade {label}", blade, PLATINUM)

    end_pin = _x_cylinder(0.0, side * end_y, end_z,
                          0.34, STRAP_WIDTH - 0.45)
    emit(f"Strap End Spring Bar {label}", end_pin, POLISHED)

# One hinge pin runs through the alternating center knuckles.  The measured
# CAD clearance is represented by the 0.40 mm knuckle bore and 0.34 mm pin.
emit("Deployant Central Hinge Pin",
     _x_cylinder(0.0, 0.0, CLASP_BOTTOM_Z, 0.34, STRAP_WIDTH - 0.35),
     POLISHED)

# Curved outer shell.  It sits radially outside both blades and spans the
# central joint; side buttons remain exposed beyond the 18 mm strap width.
cover_half_angle = math.asin(14.0 / (LOOP_RADIUS + 2.0))
cover = _curved_band(LOOP_RADIUS + 1.25, LOOP_RADIUS + 2.0,
                     -cover_half_angle, cover_half_angle, STRAP_WIDTH + 0.8)
button_z = CLASP_BOTTOM_Z - 1.60
for side in (-1, 1):
    # Radial button bores open through the two short X-end walls of the shell.
    cover -= _x_cylinder(side * 9.55, 0.0, button_z, 1.25, 1.8)
emit("Platinum Deployant Clasp Cover", cover, PLATINUM)

# A fine raised wordmark lies on the underside of the cover, facing outward
# toward -Z.  Arial is used as a readily available typographic stand-in.
logo_z = LOOP_CENTER_Z - (LOOP_RADIUS + 2.0) + 0.01
logo = (Pos(0, 0, logo_z) * Rot(180, 0, 0)
        * extrude(Text("Jaeger-LeCoultre", font_size=1.30, font="Arial"),
                  amount=0.045))
emit("Jaeger LeCoultre Clasp Wordmark", logo, POLISHED)

# Paired lateral pushbuttons and their stems.  Both axes are parallel to X;
# release action is represented, but no locking pawl is modeled.
for side, label in ((-1, "Left"), (1, "Right")):
    # The slender plunger runs through the cap while leaving radial room for
    # its coaxial return spring.
    stem = _x_cylinder(side * 9.55, 0.0, button_z, 0.20, 1.7)
    cap = _x_cylinder(side * 9.55, 0.0, button_z, 1.18, 0.72)
    emit(f"Clasp Release Button Stem {label}", stem, BRUSHED)
    emit(f"Clasp Release Button Cap {label}", cap, POLISHED)

    # Compact return coil around the plunger; its spring rate is not tested.
    coil_path = Helix(0.28, 0.84, 0.30)
    coil_section = Plane(origin=(0, 0.30, 0), z_dir=coil_path % 0) * Circle(0.055)
    return_coil = (Pos(side * 9.55, 0, button_z)
                   * Rot(0, -side * 90, 0)
                   * sweep(coil_section, path=coil_path))
    emit(f"Clasp Button Return Spring {label}", return_coil, POLISHED)
