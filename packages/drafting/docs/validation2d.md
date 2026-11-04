# Native 2D geometry validation

`aiad_drafting.validation2d` validates native cut paths and reports measured
geometry from an editable `Sheet2D`. Importing the module does not import
build123d or OCCT. The kernel loads only when a check or measurement is
requested.

```python
from aiad_drafting.validation2d import checks, measurement_evidence

result_checks = checks(ctx)
evidence = measurement_evidence(ctx)
```

`ctx.sheet` must be a `Sheet2D` or the JSON-native mapping returned by
`Sheet2D.to_dict()`. `ctx.units` must be `mm` or `in` and match
`ctx.sheet.units`; the module does not convert between them. A missing or
malformed sheet and a unit mismatch produce a failed input check and invalid
evidence.

## Geometry source and checks

The checker creates native OCCT edges from the named sheet entities. It reads
line endpoints, circular centers and radii, arc sweeps, edge lengths, wire
closure, face areas, nesting, and boundary distances from those native edges.
It does not use context metrics, params, or rendered bounds as evidence.

Lines, circles, circular arcs, rectangles, and polylines are supported. A
polyline or rectangle is measured as its native line edges. A closed cut path
may be one circle or multiple connected entities. Cut paths must form closed,
valid OCCT wires and must not self-intersect, duplicate, overlap, cross, or
touch another cut contour. Open paths on `score` and `engrave` layers are not
cut contours. Text stays annotation even if it is assigned to the `cut` layer.
Custom layers participate as cut geometry when their layer metadata has
`{"role": "cut"}`.

Checks use the shared `Check` fields: `id`, `suite: "2d"`, `level`, `status`,
and `message`. IDs are stable and unique per result:

| ID | What it checks |
|---|---|
| `2d.input` | Sheet shape and matching units |
| `2d.closed_contours` | Every cut path forms a closed wire |
| `2d.self_intersections` | A cut contour does not cross or touch itself |
| `2d.overlaps` | No duplicate or partially overlapping cut edges |
| `2d.contour_intersections` | Separate cut contours do not cross or touch |
| `2d.measurement` | Native evidence is complete, partial, invalid, or unavailable |

No cut geometry yields `skip` for geometry checks. Unsupported native geometry
or unavailable OCCT measurements are reported as `warn` or `skip`; they never
become a vacuous pass.

## Measurement evidence

`measurement_evidence(ctx)` returns finite JSON-native data with:

- `units`, `bounds`, and `bbox`. Bounds use
  `[min_x, min_y, 0, max_x, max_y, 0]`; `bbox` is
  `[width, height, 0]`. Both describe native cut geometry only.
- `cut_entities`: named source records, actual native curve types, analytic
  line/circle/arc geometry, and edge lengths. A polyline or rectangle is a
  `WIRE` with its measured analytic edges.
- `closed_contours`: source entity names, measured area and perimeter,
  boundary bounds, nesting depth, parent contour, and edge provenance. An
  invalid contour is retained with `valid: false` and no proven area.
- `holes`: true circular interior cutouts at odd nesting depth, with center,
  diameter, and source entity names. A standalone exterior circle is not a
  hole. A full circle made from true circular arcs is recognized only when
  their native arcs share a center/radius and reliably cover 360 degrees.
- `boundary_clearances`: OCCT minimum distances between distinct valid closed
  cut contours.
- `open_cut_paths`, `annotations`, `unsupported`, and `status` explain what
  could not be proven or what geometry was excluded.

The evidence status is `complete` when every cut path is supported and valid,
`partial` when a native measurement is unsupported or unavailable, `invalid`
when the sheet units or cut topology fail, and `unavailable` when no cut-layer
geometry exists. `topology_tolerance` records the small native-unit tolerance
used only to join endpoints and classify numerical contacts.

Invalid, open, crossing, touching, or overlapping paths are never used to
prove holes or boundary clearances. Bounds and named source edges may still be
reported for diagnosis, while the top-level status is `invalid`.

## Laser limits and known limits

Laser checks accept only caller-provided `kerf` and `min_slot` values, in the
sheet's native units:

```python
result_checks = checks(ctx, limits={"kerf": 0.15, "min_slot": 1.0})
```

There are no defaults. Missing values produce `skip`; booleans, non-finite,
zero, and negative values fail limit validation. Unknown keys and non-mapping
inputs also fail. Laser checks run when `ctx.process` is `laser` (or a laser
alias) or when the caller supplies `limits`.

- `2d.laser.limits` reports whether the caller supplied valid limits.
- `2d.laser.kerf` compares exact distances between valid separate contours
  with the caller's kerf. This is a heuristic: it assumes those contours are
  nominal boundaries and treats one kerf as the total gap consumed by adjacent
  cuts. It is a contour-clearance screen, not a toolpath simulation.
- `2d.laser.circular_openings` compares measured circular cutout diameters
  with the caller's `min_slot` as a narrow opening screen. The check states
  that assumption and reports each diameter and source entity. It says nothing
  about non-circular slots.
- `2d.laser.min_slot` measures circular, rectangular and obround interior
  cutouts from actual native edges. Rectangles may be rotated. An obround must
  have two equal semicircular caps and two matching parallel tangent segments.
  The evidence includes `openings` with widths, lengths and source entity names,
  and `unsupported_openings` containing unrecognized interior contour IDs.
  A known narrow opening fails; otherwise any unrecognized cutout prevents a
  full pass. No interior cutouts or missing limits yields `skip`.

The implementation does not infer material limits, kerf compensation,
minimum webs or manufacturability from geometry alone. Minimum slot is
interpreted narrowly as the nominal width of closed interior cutouts; outside
notches and general irregular openings remain unsupported.
Ellipses, splines, arbitrary curves, dimension/text layout, drawing margins,
and text overflow are outside this module. It validates only native 2D cut
geometry; S5 remains responsible for specification and process requirement
dispatch.
