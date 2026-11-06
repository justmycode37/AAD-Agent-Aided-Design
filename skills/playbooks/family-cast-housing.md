---
id: family.cast_housing.v1
title: Cast gearbox housings
tags: [part-family, cast-housing, gearbox, loft, shell, bearing, flange, rib, boss, blend]
candidate_status: unmeasured
---

# Cast gearbox housings

Use this recipe for a hollow gearbox or pump casing with curved walls and
functional bearing, mounting and split-line interfaces. Ask for the bearing
envelopes, shaft centers, gear sweep, joint plane, fastener pattern, wall
thickness, material, process and access needs. Keep sourced dimensions separate
from assumptions.

## Runnable construction order

Build the outer loft from several smooth sections, then subtract a larger inner
loft so the shell opens at its service faces. The inner loft's radial offset is
only a starting envelope: it does not prove constant normal thickness. Inspect a
section and classify points on the wall and in the cavity.

```python
from aad import *

P = params(wall=4.0, seat_bore=10.0, bolt_d=4.0)
with part("gearbox_housing") as body:
    with feature("outer_loft", intent="curved casting envelope"):
        for z, rx, ry in ((0, 50, 38), (30, 45, 34), (60, 40, 30)):
            with BuildSketch(Plane.XY.offset(z)):
                Ellipse(rx, ry)
        loft()

    with feature("shell", intent="open cavity; verify sections"):
        inner = Solid.make_loft([
            Wire.make_ellipse(46, 34, Plane.XY.offset(-1)),
            Wire.make_ellipse(41, 30, Plane.XY.offset(30)),
            Wire.make_ellipse(36, 26, Plane.XY.offset(61)),
        ], ruled=False)
        add(inner, mode=Mode.SUBTRACT)

    with feature("bearing_seat", intent="representative bearing carrier"):
        boss(body.part, (0, 0, 18), 14, 8, hole=P.seat_bore,
             root_fillet=1.0, hole_depth=4.0)

    with feature("bearing_ribs", intent="connect seat to inner wall"):
        profile = Face(Wire.make_polygon([
            Vector(13, -2, 23), Vector(42, -2, 23),
            Vector(42, 2, 23), Vector(13, 2, 23),
        ], close=True))
        rib(body.part, profile, (0, 0, 4))

    with feature("split_flange", intent="paired joint rails and bolt pattern"):
        seam = Edge.make_line((40, 0, 20), (40, 0, 40))
        split_flange(body.part, seam, width=5, thickness=3,
                     bolt_diameter=P.bolt_d, bolt_count=3,
                     bolt_axis=(1, 0, 0), hole_depth=4)

    with feature("mounting_boss", intent="hollow lug with a root blend"):
        boss(body.part, (38, 12, 34), 6, 7, axis=(1, 0, 0),
             hole=3.2, root_fillet=1.0, hole_depth=7)

    with feature("finish_edges", intent="retry explicit edge blend radii"):
        selected = edge(kind="line", where="parallel_z", nth=0)
        blend(body.part, selected, radius=0.5, fallback=(0.25, 0.1))

result = body.part
```

The `draft_faces(shape, faces, angle, neutral_plane=Plane.XY)` helper adds a degree-
based draft to selected planar, cylindrical or conical faces of one solid. Apply
it before adding small interfaces, and only to faces supported by the kernel.
`rib(shape, profile, direction)` extrudes a closed Face or Wire by the supplied
direction vector and fuses it to the housing. `boss()` revolves the root profile
to form its optional root fillet; `hole_depth` extends the bore into the parent
wall. `split_flange()` takes a straight seam Edge, builds paired rails, and
places matching bolt holes along both rails.

The helpers accept a Shape or BuildPart. Inside an active BuildPart they replace
that current part through the feature tracker, so `feature()` ownership remains
available. Outside a builder, assign the returned shape. `blend()` returns a
`BlendResult`; use `.shape`/`.part` for the geometry or unpack `(shape, report)`.
Flat edge selections are attempted as short-first singleton groups. Nested
edge iterables are atomic groups in their supplied order. Each group tries the
requested radius and then only the explicit decreasing `fallback` values. Its
report lists every requested edge as `applied`, `reduced` or `failed`. A failed
edge remains unchanged and appears in feature metadata and a build warning.

Order of operations: outer loft; shell cavity; draft suitable faces; bearing
seats and bores; connecting ribs and webs; flange rails and bolt holes; external
bosses and service holes; broad edge blends; small edge groups and local
finishing. If blends fail, inspect the report, narrow the group or provide a
tested smaller fallback. Do not remove requested blends silently.

Use `material_at_point` checks for specific wall/cavity points and inspect one or
more B-rep sections. These are local evidence, not a global wall-thickness
proof. Process checks are heuristic. This recipe does not establish
castability, strength, sealing, bearing fit or operational performance.
