---
id: family.gear.v1
title: Gears
tags: [part-family, gear, spur, helical, teeth, transmission]
candidate_status: unmeasured
---

# Gears

Before modeling, get gear type, tooth counts, normal module, pressure angle,
helix angle and hand, face width, center distance, bore/key interface,
material, load, speed, and backlash target. Do not approximate working teeth
by eye.

AAD exports `gear_profile`, `gear_solid`, `gear_center_distance`,
`helix_angle_for_center_distance`, `gear_mesh_phase`, and
`tapered_bevel_standin` from `aad`. Angles are in degrees; module is the normal
module. Pressure angle defaults to 20°, addendum to 1.0, and dedendum to 1.25.
Profile shift is in module units. Backlash is a circular tooth-thickness
reduction per gear. For an internal gear, set `internal=True`; in center and
phase helpers, gear 2 is the ring gear.

For an unshifted external helical pair, the nominal center distance is
`a = mn * (z1 + z2) / (2 * cos(beta))`. `gear_mesh_phase` returns the phase
for gear 2; external helical pairs need opposite hands.

```python
from aad import *

a = gear_center_distance(2.5, 13, 48, 31)
phase = gear_mesh_phase(13, 48)
pinion = gear_solid(13, 2.5, 8, helix_angle=31, hand="right", backlash=0.05)
wheel = gear_solid(48, 2.5, 8, helix_angle=31, hand="left", backlash=0.05,
                   phase=phase, x=a)
```

This pair was built and checked with the real kernel. Its nominal center
distance is 88.956 mm and gear 2 phase is 183.75°. Intersection volume is zero
at nominal spacing; moving gear 2 0.5 mm closer creates 5.245 mm³ of overlap
across the full 8 mm face. The two 0.05 mm tooth reductions give about
0.10 mm circular backlash.

The transverse flanks are analytic involutes represented by sampled
B-splines. Across module 1, 2.5, and 6 profiles, the maximum sampled
same-radius deviation from the analytic involute was 0.00073 mm. The profile
uses a radial root connector and circular root arc, not a trochoidal root
fillet. A rack-undercut estimate warns by default; use `undercut="error"` to
reject a predicted undercut. Internal cutter interference is not checked.

`tapered_bevel_standin` is only a tapered spur loft for layout. It is not a
true bevel, spiral-bevel, beveloid, or hypoid gear form. A render or a clear
boolean mesh does not prove load capacity, contact stress, noise, life, or
manufacturing fit. See `packages/engine/modeling.md` for the full API notes.

Source: [Boston Gear engineering information, spur-gear nomenclature](https://www.bostongear.com/-/media/Project/Altramotion/shared/files/Literature/brand/boston-gear/catalogs/p-1930-bg.pdf?rev=3d4fcbba1ef047e992cc9fe8a758e65a).
