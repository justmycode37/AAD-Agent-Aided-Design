# Requirement evidence

`spec_checks(ctx)` evaluates each requested requirement against `ctx.shape`. `load_spec(text_or_bytes_or_dict)` validates the safe YAML/data format; it raises `SpecValidationError` for malformed input. Evaluation instead returns explicit diagnostics so a built revision can remain draft. No supplied spec or an empty requirement list is unproven, rather than a failed geometric build.

Duplicate YAML keys and requirement IDs, recursive aliases, non-string keys, booleans used as measurements, non-finite values, overflowing unit conversions and malformed ranges are rejected. Unknown requirement types produce `skip`. Mixed known/unknown fields cannot silently drop the unknown condition. IDs and declared levels are retained; input diagnostics are error-level skips, so malformed specs stay draft.

Lengths default to `spec.units` (`mm` by default or `in`). The geometry's native coordinate unit is `ctx.units`; the engine/export boundary must preserve that convention. Explicit `mm`/`in` suffixes, `nominal ± tolerance`, `{nominal, tolerance}`, and inclusive `{min, max}` bounds are accepted. An exact number has no extra comparison tolerance. Dimension powers apply for area/volume. `mass_g` is in grams. Angle values are dimensionless degrees when an angle provider becomes available.

| Requirement | Evidence and limits |
| --- | --- |
| `bbox: {x, y, z}` | Native OCCT optimal bounds, not model parameters, metrics or overridden Python methods. Only requested axes are checked. |
| `solids: n` | Native B-rep solid enumeration. |
| `holes: {count, diameter, pattern: {grid: [pitch_x, pitch_y]}}` | Full-revolution, inward analytic cylindrical walls; connected coaxial segments are grouped. Other nonplanar/noncylindrical geometry can hide unrecognized holes and makes this evaluator skip. An XY grid needs vertical axes, every Cartesian position exactly once, and the requested uniform pitch. Hole count/diameter does not prove a through bore, counterbore dimensions or approach access. |
| `clearance: {with: relative.step, min, max}` | Fresh project-contained STEP import, solid-overlap boolean and minimum B-rep distance. Penetration cannot satisfy even zero requested clearance. Traversal, escaping symlinks and non-STEP assets are unsupported. |
| `mass_g: {min, max}` | Native solid volume times a sourced material's nominal `density_g_cm3`; no mass without a named known material. Context/spec material conflicts remain unproven. The material catalog is a separate module. |
| `symmetric: {plane: XY|XZ|YZ, tolerance}` | Volumetric mirror/symmetric-difference comparison. Zero difference at kernel resolution supports equality; nonzero volume cannot establish a pointwise distance bound and returns skip. Zero-volume surfaces cannot prove symmetry through a volume test. |
| `measure: {kind, a, b?}` plus `equals` or `min/max` | S1 semantic resolver, exactly one entity per selector, then native geometry. Distance, analytic circle/cylinder/sphere radius, edge/wire length, area and solid volume are supported when the resolver is available. Selector text is never `eval`ed. Angles remain unsupported. |
| `material_at_point: {point: [x,y,z], expected: solid|void}` | B-rep point classification in the declared frame. Boundary points are unknown. This proves one point, not global access or thickness. |
| `min_wall` | Explicit skip. A bbox dimension or sparse ray minimum cannot prove a global minimum wall. Process ray screens remain labelled heuristics. |

`process` and `process_settings` are accepted alongside requirements; the process evaluator validates its own declared assumptions. [PROCESS.md](PROCESS.md) describes those screens.

`verification_ready(spec, checks, hard_checks)` is a conservative integration helper, not a source of trusted evidence. It requires a valid nonempty specification, exact unique per-requirement coverage, matching severity, meaningful measured evidence, a nonempty passing error hard gate and no unproven error results. Skipped spec/process checks also withhold readiness, including optional requirements whose evaluation is unknown. A measured warning may remain advisory. A declared process needs process evidence. The caller must bind checks to the actual revision/spec/kernel/artifact and protect evaluation from model code.

The current S1 worker's trust boundary has an independently reproduced counterexample in [issue #31](https://github.com/justmycode37/AIAD-AI-Aided-Design-/issues/31). The S5 helper cannot secure mutable checker state in the interpreter executing model Python. `evals/quality/probe_worker_evidence.py` deliberately exits nonzero while reported geometry or verified claims disagree with a fresh BREP audit. This is a release blocker, not a sampled agent false-verified-rate estimate.

Run the real-geometry, malformed-input, coverage, process and actual-worker tests using the commands in [README.md](README.md). These tests do not establish that an agent's spec captured the user's complete request. The hidden eval oracle and independent sampled audit must check that gap separately.
