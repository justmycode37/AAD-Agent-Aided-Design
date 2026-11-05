# Process evidence

`aad_quality.process_checks(ctx)` reads the built B-rep and the requested process. It never treats model parameters or cached metrics as geometry. Each returned check has `heuristic: true`, its assumptions, the tested value, the threshold, and a repair hint. A passing finite screen is not a global manufacturing certificate.

A missing process requests no screen. `spec.process` is honored if the context has no process; conflicting context/spec processes fail. Unknown processes, invalid settings and unexpected evaluator exceptions produce error diagnostics. Expected unavailable evidence produces `skip` at the configured severity.

Settings live in `spec.process_settings`. All length settings ending in `_mm` are millimeters even when the project uses inches. Direction vectors are normalized. Defaults are explicit screening assumptions; they are not sourced process limits or recommendations.

| Profile | Evidence | Required declared assumptions / limits |
| --- | --- | --- |
| `fdm_pla` | Sampled downward surface normals and inward-normal wall rays | Default build direction +Z and minimum downward slope 45°. Wall threshold needs `min_wall_mm`, or `nozzle_diameter_mm` with `wall_line_count` (default 2). No support-span or slicer analysis. |
| `sla` | The same bounded overhang and wall-ray screens | Default +Z and 45° slope; wall threshold needs `min_wall_mm`. Resin, orientation optimization, drainage and support behavior remain unknown. |
| `cnc_3axis` | Coaxial solid-probe intersection at recognized full cylindrical openings; cylinder-axis alignment; partial inward-cylinder corner radii | Declare `approach_probe_radius_mm` or `tool_diameter_mm` / `tool_radius_mm`; otherwise probe radius is recognized bore radius minus assumed `approach_clearance_mm` (default 0.25). `approach_length_mm` defaults to 25. Axis screen needs `tool_axis` and diameter; radius screen needs cutter radius or `minimum_internal_radius_mm`. This is not a holder/fixture or arbitrary-pocket reachability proof. |
| `sheet_metal` | Recognized coaxial partial-cylinder pairs, radius difference and smaller radius | Needs `stock_thickness_mm` and `minimum_inside_bend_radius_mm`. Requires one attributable solid, matching angular phase, opposite inner/outer orientation and two shared axial end faces; flat pattern, relief, forming tooling, springback and material behavior are not established. |
| `laser` | Recognized through-like circular B-rep cuts | Needs `kerf_mm`; diameter screen is `max(2 × kerf_mm, minimum_feature_mm)` with optional minimum. Slots, contours, kerf compensation and the 2D sheet API require separate checks. |
| `injection` | Absolute local draft of sampled planar side faces | Default +Z pull direction and assumed 1° minimum. Override `pull_direction` / `min_draft_deg`. Reverse draft, curved faces, undercuts, slides, shutoffs and mold-half strategy remain unknown. |

Common settings: `level` (`error` by default; explicitly choose `warn` for advisory screening), `tolerance_mm` (default 0.01), `angle_tolerance_deg` (default 0.1), `max_faces` (default 256), and `max_samples` (default 4096). These last two are finite computation caps, not manufacturing limits. Missing samples or reached caps prevent a complete-screen pass. A wall ray starts inside the solid; a very thin wall that the starting offset jumps across is unknown, rather than a measurement of unrelated stock.

At error severity, `skip`, `warn` and `fail` all withhold verification. Explicit warning screens report advisory evidence and do not block readiness for independently measured error requirements. Unavailable equipment/material/process information must never be relabelled as a manufacturing pass.

```yaml
process: cnc_3axis
process_settings:
  level: error
  tool_diameter_mm: 3
  tool_axis: [0, 0, 1]
  approach_length_mm: 25
```

The coaxial test uses a real OCCT cylinder/solid intersection. Collision thresholds equal each actual probe cross-section area times the stated length tolerance; any smaller positive overlap remains a warning rather than a clearance pass. Geometry-query errors invalidate the screen, and bounded cylinder enumeration stops at the configured face cap. Build-plane contact is measured after transforming the actual B-rep into the build frame, including oblique directions. Fresh native wrappers prevent overridden model-instance attributes from being used as measurements. A through bore needs one clear tested approach; the larger counterbore segment still needs its own accessible mouth. A counterbore floor is not an entry mouth; an external boss is not a cavity. Its result is specific to the declared probe and finite approach length. The regression suite compares an open counterbore with the same geometry obstructed by a bridge, plus an oversized tool. It also exercises wall disproofs, units, internal radii, bend geometry, laser cuts, draft, malformed settings and evaluator failures:

```sh
packages/quality/.venv/bin/python -m pytest packages/quality/tests/test_process_parent.py -q
```

Positive screens are limited by coverage. Curved overhang samples and nonrectangular wall rays cannot certify unsampled geometry; their prospective passes become skips. A valid six-face axis-aligned rectangular solid has an analytically recognized minimum thickness and no internal CNC cavities or corners, so its applicable screens can pass, including equality at the wall threshold. Recognized CNC radii do not exclude sharp corners; multiple/complex sheet bends do not exclude additional sharp bends; circular laser cuts do not exclude small slots; absolute local draft does not establish signed mold release. Those incomplete positive screens stay skipped. Measured violations keep their failure status. These limits deliberately leave complex process requests draft rather than promise unmeasured coverage.
