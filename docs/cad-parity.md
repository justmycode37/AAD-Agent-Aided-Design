# AAD compared with CAD

What a CAD package such as SolidWorks, Fusion or NX does, and how an agent does it in AAD. Status comes from running each capability through the real tools or the kernel on 2026-11-06, not from the commercial packages' behavior, which was compared from general knowledge of them. Agents get the how-to from `skills/aad/SKILL.md` and playbook `workflow.capabilities.v1`.

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
| Large assemblies (thousands of parts) | Partial | A 247-solid DSG build took 111.6–114.4 s in two runs; validation without scene took 67.5–67.9 s and the clean scene 4.9–5.0 s. Thousands-part performance remains unvalidated. |

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
| Independent verification | Added | One sandboxed validator loads saved BREP once, checks hard gate/spec/process and geometry-only checks, then builds the scene from that same shape. Metrics, checks and proven IDs are bound to the revision receipt. |
| STEP, BREP, STL import | Existing | `aad_import` |
| Using an imported file in a model | Added | `asset()`: the sandbox had no way to read it before |
| IGES import | Added | `aad_import`, `asset()` |
| DXF, SVG import through the tool | Added | The parsers existed, but the tool refused these files |
| DWG, SLDPRT, F3D, CATIA, Parasolid, JT | Missing | Need licensed SDKs |

## Not CAD gaps

PDM, PLM and collaboration are out of scope. Interactive picking and sliders stay unbuilt by user direction (issue #35).

## Known limits in what exists

- **Verification is conservative.** A sandboxed validator checks the saved BREP without running model code or trusting worker checks, warnings, metrics, feature records or scenes. It loads the BREP once, recomputes hard/spec/process and registered geometry-only checks, then builds the viewer scene from the same shape. A complete finite 3D scene is required for `verified`; scene failure retains the BREP as draft with a reason. The hard gate limits absolute world coordinates to 100,000 mm: Float32 spacing there is about 0.0078 mm, below the 0.01 mm edge tolerance. `verified` requires at least one error-level requirement with an independent `pass`; other error-level requirements may be `not_applicable`. No-requirement builds and specs with only warning-level requirements remain `draft`. Error-level `skip` blocks verification; `not_applicable` means the validator established from its own geometry that the check's subject is absent. Warning-level requirements must be evaluated; a failing advisory `warn` may still be verified when error-level requirements pass, and the build and export summaries name those requirements. Build-time automatic assembly-interference warnings appear in the build summary. `min_wall` and `symmetric` remain disproof-only. Named-body `fit` cannot be proven from saved BREP because component labels do not survive import and the validator cannot trust worker metadata as a name-to-body map. Feature-bound `position` remains unproven when it relies on worker feature records. Verified summaries name the requirements proven, and the receipt rechecks saved BREP/spec/kernel/scene plus metrics, checks and proven IDs before reporting verified or exporting. These receipts catch artifact changes while intact; they are not signed against someone who can rewrite the project files and receipt.
- **Validator geometry-check hook.** Automatic assembly interference is recomputed from saved BREP through `validator_geometry_checks(ctx)` and is advisory on a build, with a 15-second budget. An incomplete result is not evidence that the assembly is clear. PR #97 must run `interference_checks` from this trusted hook (or an equivalent registered validator hook); running it only in the model worker is not evidence because worker checks are discarded. Explicit interference requirements remain evaluated at their declared severity.
- **2D verification remains draft.** A clean process recomputes native checks, supported spec requirements and metrics from the saved `sheet.json`, then generates the SVG scene from that same snapshot. Sheet revisions remain `draft` because 2D verification receipts are not supported yet. Unsupported measures remain skipped with an explanation.
- **Standard parts use simple threads** unless a record says otherwise, so thread fit needs its own check.
