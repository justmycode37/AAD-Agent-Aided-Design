# Native geometry measurement

`aad.engine.measure.measure(ctx, kind, a=None, b=None)` runs inside a trusted
kernel worker with a live 3D `ModelContext`. Importing the module stays
kernel-free. It reads native BREP geometry rather than `ctx.metrics` or stored
entity measurements. The function itself does not establish an OS boundary;
call it after loading the revision in a separate sandboxed worker, without model
code sharing the interpreter. It does not promote a revision or prove a spec.

The result is a finite JSON-compatible dictionary containing `kind`, `value`,
`unit`, `definition`, `scope` and `entities`. Entities are the actual resolved
records with their revision-local indices; private native bindings are excluded.
They describe what was measured and must not become persistent raw-index
selectors. Native OCCT operations use BREP surfaces and numerical tolerances;
no certified global error bound or mesh approximation is asserted.

| Kind | Definition and units | Arguments |
| --- | --- | --- |
| `distance` | Minimum distance between the two trimmed selected entities; mm/in. Includes one native closest-point witness pair in world coordinates. Several equal minima may exist. | a and b |
| `clearance` | Minimum gap between the selected entities; mm/in. Same native operation as distance, with explicit selected-entity scope. | a and b |
| `angle` | Smaller unoriented angle between two planes or two straight lines, 0–90 degrees; independent of face-normal or edge-traversal reversal. Mixed or curved pairs fail. | a and b |
| `radius` | One constant analytic radius of a circular edge, cylindrical face or spherical face; mm/in. A torus has multiple radii and is unsupported. | a only |
| `area` | Trimmed face area if selected, otherwise summed native surface area; mm²/in². Whole area includes cavity surfaces and assembly interfaces. | optional a, no b |
| `volume` | Sum of independently enumerated native solid volumes, including nested bodies; mm³/in³. | no selectors |
| `inertia` | Central volume inertia tensor in world XYZ; mm⁵/in⁵. Includes the volume centroid as `reference_point` and `axes=world_xyz`. | no selectors |

Volume and inertia validate each native body and require a finite positive
signed volume before aggregation. A forward body cannot mask a reversed body.
No absolute-value normalization or orientation repair is performed. Legitimate
inward cavity shells belonging to one valid positive-volume solid are retained.
The `aggregation=sum_of_native_solids` field means additive body accounting:
overlapping independent solids are counted separately, not boolean-unioned.
Inertia uses native property aggregation and its parallel-axis contributions;
adding only each body's central tensor would lose their relative placement.

Geometric inertia always has length⁵ units. It is not density-weighted mass
inertia, and its meaning does not change when `ctx.material` is set. Mass needs
a material plus a sourced finite density and provenance. That integration is
not implemented in this module; it explicitly fails `mass` instead of inventing
a density. Integration with S5's material catalog remains required.

`thickness` explicitly fails: no universal wall path, normal ray or opposed
wall definition is specified by this entry point. Distance between chosen faces
can provide a local separation, without claiming a global wall thickness.
Likewise, a positive selected-face clearance does not certify body separation.
A face on an outer box can be four units from a face on an enclosed box while
the occupied bodies overlap. A zero distance cannot by itself distinguish
contact, overlap, containment or separation below native resolution. This API
does not infer penetration depth, global clearance or an interference verdict.

Coordinates are already expressed in the declared `ctx.units` (`mm` or `in`).
Changing the unit label does not rescale geometry or infer a BREP source unit.
For the same physical geometry represented numerically in inches, conversions
to millimetres use powers 1 for length, 2 for area, 3 for volume and 5 for this
geometric inertia. No material mass is computed from a unitless default.

Selected operations depend on the actual semantic resolver from PR #41:
`resolve(ctx, selector, expect=1)` followed by `entity_shape(ctx, entity)` in
the same unchanged live context. It fails on empty/ambiguous selections rather
than choosing a candidate or recovering a shape from an index. Its actual
`Failure` and candidates are preserved through `MeasurementError`, which also
exposes code/phase/selector/candidates for the current S2 classifier. When the
selector package is absent, selected operations fail explicitly. Whole-shape
area, volume and inertia remain usable. Revision loaders must hydrate nested
`Entity` records from metadata belonging to that BREP; measurement does not
repair ownership metadata. S2's loader hydration is addressed by its PR #57.

Tests run against main exercise whole-shape operations and explicit missing
dependency failures. The selected-operation test class is visibly skipped until
the selector dependency is present; this is not evidence of selector integration.
The parent also runs that same test file with the actual immutable PR41 selector
source loaded from a temporary file into `aad.engine.select`, without a fake
resolver or a repository edit. After #41 merges, all cases run normally:

```sh
uv run --locked --all-packages --all-extras --with pytest --with pytest-timeout \
  python -m pytest packages/engine/tests/test_measure.py -q
```
