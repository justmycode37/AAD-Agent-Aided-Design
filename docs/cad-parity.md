# AAD compared with CAD

What a CAD package such as SolidWorks, Fusion or NX does, and how an agent does it in AAD. Status comes from running each capability through the real tools or the kernel on 2026-11-05, not from the commercial packages' behavior, which was compared from general knowledge of them. Agents get the how-to from `skills/aad/SKILL.md` and playbook `workflow.capabilities.v1`.

**Added** means this change added it. **Existing** means it already worked. **Partial** means the common case works and the stated limit remains. **Missing** means an agent cannot do it.

## Modeling

| Capability | Status | In AAD |
|---|---|---|
| Sketching: lines, arcs, splines, slots, offset, trim by boolean | Existing | build123d sketches, `Sheet2D` |
| Sketch constraints with degrees of freedom | Existing | `Sheet2D.add_constraint`, `solve` |
| Extrude, revolve, loft, sweep, helix | Existing | build123d |
| Holes: simple, counterbore, countersink | Existing | `Hole`, `CounterBoreHole`, `CounterSinkHole` |
| Tapped and clearance holes by fastener | Added to the default install | `TapHole`, `ClearanceHole`, `ThreadedHole` from `bd_warehouse` |
| Fillet, chamfer, shell, draft | Existing | build123d |
| Variable-radius fillet | Added | `variable_fillet`, linear start to end |
| Linear, polar, mirror patterns | Existing | `GridLocations`, `PolarLocations`, `mirror` |
| Pattern along a curve | Added | `pattern_along` |
| Split, section, project, thicken, scale | Existing | build123d |
| Parametric history and rollback | Existing | `params()`, `feature()`, `aad_history` |
| Configurations and design tables | Existing | `aad_build params`, `dry_run`, branches |
| Direct editing: press-pull | Added | `press_pull`, planar faces only |
| Defeature, delete face | Added | `delete_faces` |
| Surfacing: boundary patch, ruled, Gordon, sew | Added `sew_solid`; rest existing | `Face.make_surface*`, `sew_solid` |
| Class-A surfacing, G2/G3 tools | Missing | The kernel lacks them |
| Sheet metal: flanges, bend table, flat pattern | Partial, added | `sheet_metal`: one strip with parallel bends. No general unfold. |
| Weldments, mold tools, routing, harness | Missing | Sweeps and booleans only, no workbench |

## Assemblies

| Capability | Status | In AAD |
|---|---|---|
| Named components with placement | Added | `assembly()`, `at=` |
| Joints | Existing | build123d joints, `connect_to` |
| Global mate solver | Missing | Placement is explicit |
| BOM | Added | `aad_inspect what: "bom"`, `aad_drawing bom: true` |
| Interference | Existing, PR #77 | `aad_check suites: ["interference"]`, `Assembly.interference()` |
| Exploded views | Added | `exploded`, `Assembly.exploded` |
| Motion checks | Partial, added | `motion_clearance` samples chosen poses. No dynamics. |
| Standard part library | Added 19 families | `aad_library kind: "part"`, `make_part` |
| Gears | Existing, PR #77; added catalog | `aad.gears`, `gear.spur`, `gear.helical`, `gear.rack`, `gear.worm` |
| Large assemblies (thousands of parts) | Partial | Tested at 247 solids in PR #77 |

## Measure and analysis

| Capability | Status | In AAD |
|---|---|---|
| Distance, angle, radius, area, volume, inertia | Existing | `aad_measure` |
| Edge length, bounds, center of mass, principal inertia | Added | `aad_measure` |
| Mass from material | Added | Nominal, per component for assemblies |
| Wall thickness | Added | Sampled minimum, not proof |
| Sections, thickness, overhang, draft, curvature maps | Existing | `aad_render` |
| Manufacturing checks | Existing | `aad_check`, heuristic screens for six processes |
| FEA, CFD, thermal, dynamics | Missing | Not CAD, out of scope |
| CAM toolpaths | Missing | Not CAD, out of scope |

## 2D and drawings

| Capability | Status | In AAD |
|---|---|---|
| 2D projects through the tools | Added | `kind: "2d"` used to fail at build. Build, check, show, render and export now work. |
| 2D sheet specification checks | Added | Measured XY bounds, closed circular cut-hole counts and diameters (including pattern copies), filled cut area and component catalog checks. Unsupported 3D-only requirements skip explicitly; successful sheets remain draft. |
| Drawing views, sections, details, auto dimensions | Improved | `aad_drawing`; geometry-derived counterbore symbols, axial cylinder sizes, single-solid open-cavity wall/depth dimensions, section arrows, and AAD revision title fields. Ambiguous or multi-solid cavity measurements are omitted; section/detail labels omit a scale suffix at 1:1. |
| Notes, revision table, BOM on the sheet | Added to the tool | They existed in the library, but the tool did not expose them |
| Tolerances on explicit dimensions | Existing | `DimensionSpec` |
| GD&T and PMI | Missing | Out of scope; drawings are not shop-ready certification |

## Exchange

| Capability | Status | In AAD |
|---|---|---|
| STEP AP214/AP242, BREP, STL, 3MF, GLB, DXF, SVG, PDF export | Existing | `aad_export` |
| IGES, OBJ, PLY export | Added | `aad_export` |
| Standalone viewer export | Added | `viewer_html` used to fail as not installed |
| STEP, BREP, STL import | Existing | `aad_import` |
| Using an imported file in a model | Added | `asset()`: the sandbox had no way to read it before |
| IGES import | Added | `aad_import`, `asset()` |
| DXF, SVG import through the tool | Added | The parsers existed, but the tool refused these files |
| DWG, SLDPRT, F3D, CATIA, Parasolid, JT | Missing | Need licensed SDKs |

## Not CAD gaps

PDM, PLM and collaboration are out of scope. Interactive picking and sliders stay unbuilt by user direction (issue #35).

## Known limits in what exists

- **Status stays `draft`.** The worker cannot attest its own verification and no trusted validator is wired, so `aad_export` needs `allow_unverified` for every revision.
- **2D requirements are unproven.** Spec requirements are not evaluated on sheets yet.
- **Standard parts use simple threads** unless a record says otherwise, so thread fit needs its own check.
