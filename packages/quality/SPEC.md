# Requirement evidence

`spec_checks(ctx)` evaluates each requested requirement against `ctx.shape` and adds checks for any declared `subsystems` breakdown. `load_spec(text_or_bytes_or_dict)` validates the safe YAML/data format; it raises `SpecValidationError` for malformed input. Evaluation instead returns explicit diagnostics so a built revision can remain draft. No supplied spec or an empty requirement list is unproven, rather than a failed geometric build.

Duplicate YAML keys and requirement IDs, YAML aliases, ambiguous YAML 1.1 integers (octal/sexagesimal), non-string keys, booleans used as measurements, non-finite values, overflowing unit conversions and malformed ranges are rejected by `load_spec`. Text is limited to 1 MiB, 64 nesting levels and 10,000 nodes; dictionary input has the same depth/node limits. Unknown requirement types produce `skip`. Mixed known/unknown fields cannot silently drop the unknown condition. In build evaluation, requirement-structure errors that survive YAML parsing (such as duplicate IDs or invalid `covers`) produce error-level `skip` diagnostics; an otherwise successful build stays `draft` but can commit a revision and advance the head. YAML syntax and duplicate YAML keys fail input parsing with `BAD_INPUT`.

Lengths default to `spec.units` (`mm` by default or `in`). The geometry's native coordinate unit is `ctx.units`; the engine/export boundary must preserve that convention. Explicit `mm`/`in` suffixes, `nominal ± tolerance`, `{nominal, tolerance}`, and inclusive `{min, max}` bounds are accepted. Bare exact measurements have a numerical comparison band of 1e-6 in canonical mm, mm² or mm³ (1e-9 for dimensionless values). Explicit tolerance/range boundaries receive no additional band; integer counts remain exact. Dimension powers apply for area/volume. `mass_g` is in grams. Angle values are unoriented degrees between two planar faces or two straight edges.

Each requirement may include a `covers` string or list to name the request excerpt it is intended to address. This records a trace link; it does not establish semantic coverage.

## Functional breakdown checks

`subsystems` is an optional top-level list with at most 100 entries. Every
entry has a unique `id`, non-empty `purpose`, `parts`, `interfaces`,
`evidence`, and a `status` of `modeled`, `representative`, or `not_modeled`.
`not_modeled` entries require a non-empty `reason`. A part is an exact
assembly component label or feature name. Each interface has a unique local
`id`, descriptive `kind`, exactly two `between` labels, and `requirements`
IDs. Subsystem/interface text values are limited to 2,000 characters; IDs to
200 characters; each list to 100 items; and the encoded `subsystems` value to
64 KiB.

The evaluator mechanically matches every listed part label against recorded
feature names and assembly component labels. A missing label fails its
subsystem check. A subsystem and each interface require at least one linked
requirement whose check has exactly one result with status `pass`; a failed
requirement fails that check, while an absent link or skipped, unknown,
warning, or otherwise non-pass requirement is not proven. `representative`
and `not_modeled` are not proven; the request review lists `not_modeled`
entries and their reasons. An assembly with no breakdown gets a hint and no
failure.

All breakdown text is untrusted. Labels and requirement IDs are references,
not semantic assertions. These checks do not judge the breakdown's meaning,
check whether `between` describes physical connectivity, or prove subsystem
function. There is no functional coverage percentage.
`subsystem_review(spec, checks, is_assembly=...)` formats the result for
`request_review`.

A high reference score measures geometric closeness to that reference only. It proves nothing about function and is meaningless if the reference was traced into the model. The scan noise floor demonstrates that F@0.1%D can remain below 0.99 for two scans of the same object: the supplied gearbox scan pair scored 0.958 with volumetric IoU 0.925. Scores depend on which surfaces each side contains, including internal parts, cast detail, and scan coverage.

| Requirement | Evidence and limits |
| --- | --- |
| `bbox: {x, y, z}` | Native OCCT optimal bounds, not model parameters, metrics or overridden Python methods. Only requested axes are checked. |
| `solids: n` | Native B-rep solid enumeration. |
| `holes: {count, diameter, pattern: {grid: [pitch_x, pitch_y]}, scope?: all\|cylindrical, through?: boolean}` | Through bores are the default; `through: false` includes open blind bores. A full-radius native cylindrical clearance sweep to the exterior rejects sealed chambers, thin caps and narrower pilots. A requested through diameter must clear the entire channel; blind scope requires a matching-radius segment to reach at least one exterior side. Mirrored analytic surfaces preserve inward orientation. All connected coaxial diameter segments are grouped before diameter filtering, so grooves do not split bores. A count-plus-diameter selects matching bores; an unrelated larger bore is allowed. For default through scope, the Euler characteristic of valid exterior analytic shells checks for omitted or branched geometry, including tunnels crossing exterior face junctions. Genus disagreement is coverage uncertainty, not a bore-count failure; sealed inner shells are excluded. Unclassified topology remains unproven. Explicit `cylindrical` limits coverage to recognized full-revolution bores. Grid axes lie in the coordinate plane perpendicular to the common X/Y/Z bore direction; arbitrary oblique grid frames remain unsupported. |
| `clearance: {with: relative.step, min, max}` | Fresh project-contained STEP import, solid-overlap boolean and minimum B-rep distance. Penetration cannot satisfy even zero requested clearance. Traversal, escaping symlinks and non-STEP assets are unsupported. |
| `fit: {a, b, clearance: {min, max}}` | Measures separation between exact assembly component names or semantic selectors. For two solid operands it first checks common volume; any positive overlap fails clearance. Non-negative bounds use project units. A non-solid selector pair measures geometric separation only. |
| `fit: {a, b, interference: {min, max}}` | Measures common B-rep solid volume in mm³ for two solid operands. Bounds must be non-negative. A boolean failure or non-solid operand is unknown. This is not a contact, stress or fit-class analysis. |
| `position: {selector, target: [x,y,z], tolerance}` | Measures Euclidean distance from the target to the selected face/edge center or vertex point. `position: {hole: selector, ...}` measures shortest distance to a full inward cylindrical face's axis; target axial position is ignored. Other geometry or ambiguous selectors are unknown. |
| `matches_reference: {path, f_score: {min, tau_pct_of_diagonal?}}` | Compares the model surface with a project-contained STEP, STL, PLY, or OBJ reference. Paths containing parent traversal or symbolic links are refused; files are limited to 256 MiB, 6 million vertices, and 12 million triangles. Reference meshes are parsed as data only. Registration is proper rigid (`det(R)=+1`), with no scale or mirror; the result reports rotation and translation. F uses 100,000 fixed-seed area-weighted samples per side and exact VTK point-to-triangle distances. The threshold defaults to F@0.1% of the reference bbox diagonal; `min` is in [0, 1]. F at 0.05/0.25/0.5/1/2% of the diagonal, Chamfer, Hausdorff, extents, area ratio, and closed-solid volumetric IoU are also reported by `aad compare`. IoU is “not available” unless both surfaces pass the closed-solid edge check. Input size and a 45-second evaluator budget are bounded. An unreadable reference, failed registration, timeout, or missing NumPy/SciPy/VTK dependency is an explicit skip, never a pass. |
| `mass_g: {min, max}` | Native solid volume times a sourced material's nominal `density_g_cm3`; no mass without a named known material. Context/spec material conflicts remain unproven. The material catalog is a separate module. |
| `symmetric: {plane: XY\|XZ\|YZ, tolerance}` | Pass requires a valid planar, straight-edged solid, exact matching reflected face/edge signatures, and zero measured symmetric-difference volume. A zero-volume boolean alone is inconclusive because OCCT can hide sub-resolution differences. Curved and near-symmetric geometry remain skip unless maximum pointwise deviation is bounded; a mirror separated beyond tolerance is a measured disproof and returns fail. |
| `measure: {kind, a, b?}` plus `equals` or `min/max` | S1 semantic resolver, exactly one entity per selector, then its context-bound `entity_shape(ctx, entity)` native geometry. Metadata and indices alone cannot stand in for B-rep geometry. Distance, smaller unoriented angle between planar faces or straight edges, analytic circle/cylinder/sphere radius, edge/wire length, area and solid volume are supported when the resolver is available. Selector text is never `eval`ed. |
| `material_at_point: {point: [x,y,z], expected: solid|void}` | B-rep point classification in the declared frame. Boundary points are unknown. This proves one point, not global access or thickness. |
| `components: [{id, <option>: value, qty?}, {custom, reason}]` | Each `id` must exist in the component catalog with a stocked option such as `length_mm` or `resistance_ohm`. Custom parts must state why they are custom. This compares a declared list with the catalog; it does not inspect geometry, prove the model uses those parts, or prove stock. |
| `min_wall` | A sampled local wall below the required lower bound is a failure witness. Without a sampled violation the result remains skip: finite ray samples cannot prove a global minimum wall. |

`process` and `process_settings` are accepted alongside requirements; the process evaluator validates its own declared assumptions. [PROCESS.md](PROCESS.md) describes those screens.

`verification_ready(spec, checks, hard_checks, process=ctx.process)` is a conservative integration helper, not a source of trusted evidence. Pass the context process even when it is absent from the spec. It requires a valid nonempty specification, exact unique per-requirement and declared subsystem/interface check coverage, matching severity, passing linked evidence, meaningful measured evidence, a nonempty passing error hard gate and no unproven error results. Spec and process passes with missing or unknown measurements are unproven. A requested process needs matching profile evidence; context/spec conflicts block readiness. Skipped spec requirements and error-level process screens withhold readiness. An explicitly warning-level process screen remains advisory, including when unavailable. This deliberately exceeds the contract's minimum error-level coverage rule; a measured warning may remain advisory. The `process` keyword is required, even when its value is `None`. The caller must bind checks to the actual revision/spec/kernel/artifact and protect evaluation from model code.

The `aad compare <project> <reference> [--rev N] [--tau-pct 0.1] [--json]` CLI runs the comparison in a sandboxed worker. It is not exposed as an MCP tool. Mesh references do not carry reliable unit metadata; the spec's `units` (or project units in the CLI) are assumed. STEP units are read by the kernel.

The S1 worker trust boundary has independently reproduced counterexamples in [issue #31](https://github.com/justmycode37/AAD-Agent-Aided-Design/issues/31). S1's containment in [PR #32](https://github.com/justmycode37/AAD-Agent-Aided-Design/pull/32) now clears worker proof claims and withholds verified status. Reported metrics remain mutable in the interpreter executing model Python. The S5 helper cannot attest that evidence. `evals/quality/probe_worker_evidence.py` deliberately exits nonzero while reported geometry or verified claims disagree with a fresh BREP audit. This is a release blocker, not a sampled agent false-verified-rate estimate.

Run the real-geometry, malformed-input, coverage, process and actual-worker tests using the commands in [README.md](README.md). These tests do not establish that an agent's spec captured the user's complete request. The hidden eval oracle and independent sampled audit must check that gap separately.
