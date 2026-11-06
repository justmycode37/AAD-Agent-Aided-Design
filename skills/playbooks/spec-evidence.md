---
id: spec.evidence.v1
title: Spec evidence and limits
tags: [spec, requirements, checks, verification, units]
candidate_status: unmeasured
---

# Spec evidence and limits

Unmeasured candidate guidance. The current evaluator is described in packages/quality/SPEC.md.

Use unique requirement IDs and explicit units. Lengths default to spec units (mm); model coordinates are not rescaled by units metadata.

## Functional breakdowns

Assemblies may declare `subsystems` in `spec.yaml`. Each entry has a unique
`id`, descriptive `purpose`, `parts` (exact assembly component labels or
feature names), `interfaces`, `evidence` requirement IDs, and `status`:
`modeled`, `representative`, or `not_modeled`. `not_modeled` requires a
`reason`. Each interface has a unique local `id`, descriptive `kind`, two
labels in `between`, and requirement IDs in `requirements`.

The `spec` suite checks that every declared part label matches a recorded
assembly component or feature name. It also checks that each subsystem and
interface links to at least one known requirement and that every linked check
passes. Missing labels fail. Empty, unknown, skipped, warning or failed
evidence is not a pass; skipped or unknown evidence is reported as not proven.
An assembly without a breakdown gets a hint and does not fail for that alone.

This is mechanical bookkeeping only. Labels and requirement IDs are references
and all text is untrusted data. AAD does not judge whether the breakdown
matches the requested object, whether `between` describes a real connection,
or whether a subsystem works. `representative` is not full modeled geometry;
`not_modeled` entries and their reasons appear in
`aad_inspect(what="summary").request_review.functional_breakdown`. No
functional coverage percentage is computed.

Limits: at most 100 subsystem entries, parts, evidence links and interfaces per
subsystem; text up to 2,000 characters, IDs up to 200 characters, and 64 KiB
total for the encoded `subsystems` value.

An optional requirement `covers` string or list names the request excerpt it
is intended to address. It is a trace link only. Request review reports
requirements without links, open questions and `not_checked` items with their
reasons. It does not judge meaning or compute a request-coverage percentage.

Current evaluator evidence:

- bbox measures native B-rep bounds on requested axes, not parameter values.
- solids counts native B-rep solids.
- holes defaults to through bores; through: false includes open blind bores. Count with diameter selects matching bores, so other diameters can coexist. A grid uses the coordinate plane perpendicular to X/Y/Z bores. The all scope checks topology for omitted noncircular through tunnels; cylindrical scope limits coverage to analytic bores. Counts do not prove counterbore depth, screw access, or lack of obstruction.
- clearance compares a model with a project-contained STEP asset for overlap and minimum distance.
- measure supports unoriented angle in degrees between two planar faces or two straight edges; other entity combinations remain unknown.
- position measures the center of one selected semantic entity, or the shortest distance from a target to one inward-facing full cylindrical hole axis. Feature positions use a selector naming the feature. Hole target's axial coordinate is ignored.
- fit measures minimum separation between two exact assembly body names or semantic selectors. Clearance and interference bounds must be non-negative. A clearance range is in project units and cannot pass when selected solids overlap. An interference range measures common solid volume in mm³ and requires two solid operands. It does not establish a manufacturing fit class, contact pressure, or stress.
- matches_reference compares a model surface with a project-contained STEP, STL, PLY, or OBJ reference using 100,000 fixed-seed area-weighted samples and exact point-to-triangle distances. Registration is proper rigid only (determinant +1), without scale or mirror. Use `f_score.min` and `tau_pct_of_diagonal` in the spec; the default threshold is 0.1% of the reference bbox diagonal. The CLI `aad compare <project> <reference> [--rev N] [--tau-pct 0.1] [--json]` reports the same surface metrics in a sandboxed worker; it is not an MCP tool.
- material_at_point classifies one point as solid or void; it says nothing global about thickness or access.
- mass_g uses a nominal density for exact catalog IDs when the materials provider is installed. Current IDs are scoped to PLA 3D700, AISI 1018, wrought 6061, and stainless 304/4301. The estimate treats the B-rep as fully dense; it is not finished-part mass or a mass guarantee. See the [material source ledger](../../evals/library/material-sources.md).

Evidence limits:

- min_wall can fail with a sampled thin-wall witness; without a sampled violation it remains skip. Finite ray samples cannot prove a global minimum.
- symmetric passes only when a valid planar, straight-edged B-rep exactly matches its reflected face and edge signatures and the measured symmetric-difference volume is zero. A zero-volume boolean alone can hide sub-resolution differences. Curved boundaries and near-symmetric geometry remain skip unless a maximum pointwise deviation is established; a mirror separated by more than tolerance is a fail.
- measure resolves semantic selectors to exactly one entity each and measures native geometry.
- Unknown or malformed requirements never pass silently.

A pass is evidence for that requirement only. It does not show that the requirement list matches the entire original request. Error-level skips, missing evidence, or evaluator failures withhold verification.

A high reference score means geometric closeness to the reference only. It says nothing about function, and the score is meaningless if the reference was traced into the model. Scan noise can keep F@0.1%D below 0.99 even when both meshes are scans of one object: the supplied gearbox scan pair scored F=0.958 and IoU=0.925. Scores also depend on which surfaces each side contains, including internal parts, cast detail, and scan coverage. Volumetric IoU is reported as unavailable unless both surfaces pass the closed-solid check.

Source: [AAD spec implementation notes](../../packages/quality/SPEC.md).
