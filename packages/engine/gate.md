# Geometric hard gate

`aad.engine.gate.hard_checks(ctx)` returns hard-suite `Check` objects. The
worker requires every error-level hard check to pass before a revision can be
`verified`; a failed check fails the build, and an error-level skip leaves it
unverified. Verification also requires the separate specification and process
checks described in `CONTRACTS.md`. This gate does not establish design intent
or manufacturability.

A failed hard check returns `HARD_GATE_FAILED` and names the check. This does
not label every rejected shape as kernel-invalid: a valid B-rep can fail a
configured small-topology threshold. The artifact and measured checks remain
available in the engine result.

The gate checks:

- B-rep validity using `BRepCheck_Analyzer` and the build123d `is_valid`
  property, and every enumerated shell using `BRep_Tool.IsClosed_s`.
- The solid count. A `part` must contain exactly one solid; an `assembly` must
  contain at least one. Multiple solids also produce a heuristic warning:
  count alone cannot establish their intended relationship or clearance. Other
  kinds have no 3D body-count policy and receive an error-level skip.
- All unique underlying OCCT edges, including degenerate edges that
  `Shape.edges()` filters out. A degenerate edge or an edge at or below the
  configured length threshold fails. Each face must exceed both an area
  threshold and a sliver-width proxy: `2 × face area / sum of distinct,
  non-degenerate face-edge lengths`. These size thresholds and the width
  proxy are heuristics; they are not manufacturing limits or measured minimum
  wall thicknesses.
- A real STEP export and re-import. Source and imported volume must be
  summed over their native solids, including deeper assembly levels, and
  positive, and absolute drift must be at most the greater of the configured
  absolute tolerance and the relative tolerance times source volume. The
  measured volumes, drift, and comparison values are reported. A non-positive
  source volume, missing output, failed export/import, or non-finite
  comparison is an error-level skip rather than evidence of a pass.
- A tessellation of the whole shape.
  A private native topology copy is cleaned and remeshed with absolute OCCT
  deflection, so pre-existing linear or angular triangulation cannot replace
  the configured settings or mutate the source shape's cached mesh.
  Vertices are welded to the nearest
  existing representative within the configured distance; zero tolerance
  welds only exactly coincident coordinates. After welding, triangles must be
  non-degenerate and unique, each undirected edge must occur twice with
  opposite directions, and each used vertex must have one connected cyclic
  link. Counts and thresholds appear in the check result.

The STEP file is created in a unique temporary directory under `ctx.job_dir`
and cleaned up on success or failure. Existing files in the job directory are
not reused. The job directory's parent must already exist.

## Tolerances and units

All length, area, and volume settings below are in mm, mm², and mm³ regardless
of whether `ctx.units` is `mm` or `in`. Shape coordinates are interpreted in
the declared units; inch values are converted by 25.4. STEP is exported in
the declared unit and build123d re-imports it in mm. Unsupported units fail
the unit check and skip threshold-based checks.

| `GateTolerances` field | Default | Comparison or use |
|---|---:|---|
| `min_edge_length_mm` | 0.01 mm | Each edge must be strictly longer. |
| `min_face_area_mm2` | 0.001 mm² | Each face must be strictly larger. |
| `min_face_width_mm` | 0.001 mm | Each face's sliver proxy must be strictly larger. |
| `step_volume_abs_mm3` | 1e-6 mm³ | Absolute component of allowed drift. |
| `step_volume_rel` | 1e-6 | Fraction of source volume in allowed drift. |
| `mesh_vertex_weld_mm` | 1e-6 mm | Maximum distance to a weld representative, inclusive. |
| `mesh_triangle_area_mm2` | 1e-12 mm² | A welded triangle must be strictly larger. |
| `mesh_deflection_mm` | 0.1 mm | Tessellation linear deflection. |
| `mesh_angular_deflection_rad` | 0.35 rad | Tessellation angular deflection. |

Callers can pass `GateTolerances(...)` or a mapping of field names as the
optional second argument to `hard_checks`; omitted mapping fields use the
defaults. For example, `hard_checks(ctx, {"min_edge_length_mm": 0.005})`
changes just that threshold. Values must be finite and non-negative, with
positive mesh deflections. Invalid or unknown settings fail the tolerance
check and skip dependent checks. The current build worker calls the gate with
defaults; `BuildRequest` does not yet expose overrides.

## Limits

The configured small-topology thresholds can reject a valid design. In
particular, OCCT can mark the pole edges of a valid sphere as degenerate; this
gate reports and rejects them rather than inferring that they are intended.
The face-width proxy depends on trimming and is not a general sliver detector.
Welding and tessellation are tolerance-dependent: nearby vertices can merge,
small defects can be missed, and touching assembly components can be rejected
when their meshes are welded together. The mesh checks do not test triangle
self-intersections or prove exact B-rep closure. STEP volume comparison does
not establish equal topology, placement, or shape. Interference, dimensions,
manufacturing suitability, and conformance to the request require other
checks and review.

## Sample timing

On 2026-11-04, Apple M4, macOS 26.6.2, Python 3.12.10, build123d 0.13.0,
and OCP 8.0.1.1.0, a warm process measured ten fresh shapes per case. The gate
column includes STEP file I/O and tessellation; shape construction is measured
separately. Times are milliseconds, median with minimum–maximum in parentheses.

| Shape | Faces / edges | Build | Hard gate |
|---|---:|---:|---:|
| Box 20 × 10 × 6 | 6 / 12 | 0.25 (0.23–0.29) | 24.78 (24.49–25.35) |
| Cylinder, radius 5, height 12 | 3 / 3 | 0.21 (0.18–0.23) | 15.99 (15.73–16.26) |
| 20 × 20 × 6 box with radius 3 through-hole | 7 / 15 | 2.14 (2.11–2.18) | 30.91 (30.12–31.43) |

These are direct calls using a temporary job directory, not end-to-end worker
or sandbox timings; cold imports and complex models were not measured. To
repeat the measurement from the repository root:

```sh
UV_PROJECT_ENVIRONMENT=/tmp/aad-s1-gate-benchmark \
  uv run --locked --python 3.12 --package aad-engine --group dev python - <<'PY'
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from build123d import Box, Cylinder
from aad.engine.gate import hard_checks
from aad.engine.types import ModelContext

cases = {
    "box": lambda: Box(20, 10, 6),
    "cylinder": lambda: Cylinder(5, 12),
    "holed_part": lambda: Box(20, 20, 6) - Cylinder(3, 10),
}
with TemporaryDirectory(prefix="aad-s1-benchmark-") as job:
    for name, factory in cases.items():
        hard_checks(ModelContext(shape=factory(), job_dir=Path(job)))
        build_ms, gate_ms = [], []
        for _ in range(10):
            start = perf_counter_ns()
            shape = factory()
            middle = perf_counter_ns()
            checks = hard_checks(ModelContext(shape=shape, job_dir=Path(job)))
            end = perf_counter_ns()
            assert all(c.status == "pass" for c in checks if c.level == "error")
            build_ms.append((middle - start) / 1e6)
            gate_ms.append((end - middle) / 1e6)
        for label, times in (("build", build_ms), ("gate", gate_ms)):
            print(name, label, "median", median(times), "range", min(times), max(times), "ms")
PY
```
