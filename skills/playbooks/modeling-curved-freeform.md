---
id: modeling.curved-freeform.v1
title: Curved and freeform shapes, lofts and sweeps
tags: [modeling, curved, freeform, spline, bezier, loft, sweep, revolve, casting, gearbox, housing, smooth]
candidate_status: unmeasured
---

# Curved and freeform modeling

AAD exposes the full build123d namespace through `from aad import *`.
It is not restricted to boxes or straight extrusions. These recipes run on
the pinned kernel; their effect on agent design quality has not been measured.

Choose the construction from the desired shape:

- Tapered cast housings: loft several circular, elliptical or spline sections.
  Place intermediate sections where the envelope changes, not only at its ends.
  Use smooth lofts (`loft()` or `Solid.make_loft(wires, ruled=False)`).
  `Wire.make_polygon` with `ruled=True` creates actual flat panels; increasing
  the vertex count only makes smaller panels. Use a periodic spline wire or
  analytic arcs for a continuous curved surface instead.
- Irregular outlines: close a BuildLine containing arcs, Bezier or Spline edges,
  call make_face in BuildSketch, then extrude or loft the profile.
- Bent tubes and flowing ribs: sweep a section along a spatial spline. Orient
  the initial sketch normal along the path's starting tangent.
- Turned shafts, domes and bell profiles: revolve a closed axial section.
- Cavities: subtract an inner envelope or use offset/shell with selected openings.
- Cast details: add bearing bosses, flange profiles and ribs; subtract bores and
  bolt holes; blend selected edges with fillet after major booleans.

## Hollow tapered envelope

This runnable concept uses true curved B-rep surfaces, not polygonal circles.
The two lofts specify envelopes; their radial difference is not a guarantee of
constant normal wall thickness. Inspect sections and measure the actual result.

```python
from aad import *

P = params(length=120.0, wall=4.0)
with BuildPart() as housing:
    for z, rx, ry in ((0, 52, 42), (35, 48, 38), (P.length, 30, 27)):
        with BuildSketch(Plane.XY.offset(z)):
            Ellipse(rx, ry)
    loft()
    for z, rx, ry in ((-1, 52, 42), (35, 48, 38), (P.length + 1, 30, 27)):
        with BuildSketch(Plane.XY.offset(z)):
            Ellipse(rx - P.wall, ry - P.wall)
    loft(mode=Mode.SUBTRACT)
result = housing.part
```

## Spatial spline sweep

```python
from aad import *

with BuildLine() as path:
    Spline((0, 0, 0), (20, 0, 10), (35, 20, 25), (60, 15, 40))
with BuildPart() as body:
    with BuildSketch(Plane(origin=path.line @ 0, z_dir=path.line % 0)):
        Circle(3)
    sweep(path=path.line)
result = body.part
```

## Asymmetric closed spline

```python
from aad import *

with BuildPart() as body:
    with BuildSketch():
        with BuildLine():
            Spline((30, 0), (22, 20), (-5, 26), (-30, 8),
                   (-20, -18), (12, -15), periodic=True)
        make_face()
    extrude(amount=12)
    fillet(edges(), radius=1)
result = body.part
```

Use parameters for section positions, radii and control points. Keep section
ordering and orientation consistent to avoid twisted lofts. A smooth spline
does not guarantee a smooth join to another edge: specify tangency where needed.
Avoid self intersections, tiny radii and a sweep section wider than the path's
local curvature permits. Reduce a failed fillet radius or select a smaller edge
set; do not silently replace the requested curved form with a box.

Build and check validity, solid count, bounds and functional clearances. Review
each physical casting as one connected solid: fuse touching features with
boolean union, not merely a Compound of overlapping pieces. Check
`len(result.solids()) == 1` for each intended single part; a curved solid can
still have several legitimate faces around holes, flanges and blends. Keep
separate manufactured parts separate in an assembly.

Review
the silhouette and sections against the user's reference. A faceted viewer mesh
may reflect tessellation settings even when the B-rep is smooth; inspect the
underlying geometry before changing the design. These operations cover broad
families of shapes, but do not promise every arbitrary surface is a valid solid.
