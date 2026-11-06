"""Representative cast gearbox housing used to exercise finishing helpers."""

from aad import *

meta(name="cast_gearbox_housing_fixture", units="mm")

SECTIONS = ((0.0, 50.0, 38.0), (28.0, 45.0, 34.0), (60.0, 40.0, 30.0))
WALL = 4.0


def ellipse_wire(z, rx, ry):
    return Wire.make_ellipse(rx, ry, Plane.XY.offset(z))


def rib_profile(points):
    return Face(Wire.make_polygon(points, close=True))


with part("cast_housing") as housing:
    with feature("outer_loft", intent="smooth cast envelope"):
        for z, rx, ry in SECTIONS:
            with BuildSketch(Plane.XY.offset(z)):
                Ellipse(rx, ry)
        loft()

    with feature("shell_walls", intent="open cavity with nominal radial wall offset"):
        inner_sections = [(-1.0, SECTIONS[0][1] - WALL, SECTIONS[0][2] - WALL)]
        inner_sections.extend((z, rx - WALL, ry - WALL) for z, rx, ry in SECTIONS[1:-1])
        inner_sections.append((61.0, SECTIONS[-1][1] - WALL, SECTIONS[-1][2] - WALL))
        inner = Solid.make_loft([ellipse_wire(z, rx, ry) for z, rx, ry in inner_sections], ruled=False)
        add(inner, mode=Mode.SUBTRACT)

    with feature("bearing_seat", intent="representative central bearing carrier with bore"):
        boss(
            housing.part,
            center=(0, 0, 18),
            radius=14,
            height=8,
            hole=10,
            root_fillet=1,
        )

    with feature("radial_ribs", intent="webs connect the bearing carrier to the cavity wall"):
        z0, z1, half = 23.0, 27.0, 2.0
        profiles = (
            [Vector(13, -half, z0), Vector(42, -half, z0), Vector(42, half, z0), Vector(13, half, z0)],
            [Vector(-42, -half, z0), Vector(-13, -half, z0), Vector(-13, half, z0), Vector(-42, half, z0)],
            [Vector(-half, 13, z0), Vector(half, 13, z0), Vector(half, 31, z0), Vector(-half, 31, z0)],
            [Vector(-half, -31, z0), Vector(half, -31, z0), Vector(half, -13, z0), Vector(-half, -13, z0)],
        )
        for points in profiles:
            rib(housing.part, rib_profile(points), (0, 0, z1 - z0))

    with feature("split_flange", intent="paired rails along the case joint with through bolt pattern"):
        seam = Edge.make_line((40.5, 0, 20), (40.5, 0, 40))
        split_flange(
            housing.part,
            seam,
            width=5,
            thickness=3,
            bolt_diameter=3.5,
            bolt_count=3,
            bolt_axis=(1, 0, 0),
            end_margin=4,
            gap=0.4,
            hole_depth=4,
        )

    with feature("mounting_boss", intent="hollow mounting boss with root blend"):
        boss(
            housing.part,
            center=(38, 12, 34),
            radius=6,
            height=7,
            axis=(1, 0, 0),
            hole=3.2,
            root_fillet=1,
            hole_depth=7,
        )

    with feature("edge_blends", intent="explicit small edge blend with retry"):
        requested = edge(kind="line", where="parallel_z", nth=0)
        blend(housing.part, requested, radius=0.5, fallback=(0.25, 0.1))

result = housing.part
