---
id: spec.evidence.v1
title: Spec evidence and limits
tags: [spec, requirements, checks, verification, units]
candidate_status: unmeasured
---

# Spec evidence and limits

Unmeasured candidate guidance. The current evaluator is described in packages/quality/SPEC.md.

Use unique requirement IDs and explicit units. Lengths default to spec units (mm); model coordinates are not rescaled by units metadata.

Current evaluator evidence:

- bbox measures native B-rep bounds on requested axes, not parameter values.
- solids counts native B-rep solids.
- holes defaults to through bores; through: false includes open blind bores. Count with diameter selects matching bores, so other diameters can coexist. A grid uses the coordinate plane perpendicular to X/Y/Z bores. The all scope checks topology for omitted noncircular through tunnels; cylindrical scope limits coverage to analytic bores. Counts do not prove counterbore depth, screw access, or lack of obstruction.
- clearance compares a model with a project-contained STEP asset for overlap and minimum distance.
- matches_reference compares a model surface with a project-contained STEP, STL, PLY, or OBJ reference using 100,000 fixed-seed area-weighted samples and exact point-to-triangle distances. Registration is proper rigid only (determinant +1), without scale or mirror. Use `f_score.min` and `tau_pct_of_diagonal` in the spec; the default threshold is 0.1% of the reference bbox diagonal. The CLI `aad compare <project> <reference> [--rev N] [--tau-pct 0.1] [--json]` reports the same surface metrics in a sandboxed worker; it is not an MCP tool.
- material_at_point classifies one point as solid or void; it says nothing global about thickness or access.
- mass_g uses a nominal density for exact catalog IDs when the materials provider is installed. Current IDs are scoped to PLA 3D700, AISI 1018, wrought 6061, and stainless 304/4301. The estimate treats the B-rep as fully dense; it is not finished-part mass or a mass guarantee. See the [material source ledger](../../evals/library/material-sources.md).

Treat these as unproven in this tree:

- min_wall can fail with a sampled thin-wall witness; passing samples remain unproven. Bounding boxes and sparse rays are not a general wall-thickness proof.
- symmetric can fail from a separated mirror, but a potential pass remains skip without a certified pointwise error bound.
- measure needs the planned AAD semantic selector and measurement providers, which are absent here.
- Unknown or malformed requirements never pass silently.

A pass is evidence for that requirement only. It does not show that the requirement list matches the entire original request. Error-level skips, missing evidence, or evaluator failures withhold verification.

A high reference score means geometric closeness to the reference only. It says nothing about function, and the score is meaningless if the reference was traced into the model. Scan noise can keep F@0.1%D below 0.99 even when both meshes are scans of one object: the supplied gearbox scan pair scored F=0.958 and IoU=0.925. Scores also depend on which surfaces each side contains, including internal parts, cast detail, and scan coverage. Volumetric IoU is reported as unavailable unless both surfaces pass the closed-solid check.

Source: [AAD spec implementation notes](../../packages/quality/SPEC.md).
