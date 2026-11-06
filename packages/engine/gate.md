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
  An assembly is copied and remeshed once; the topology is walked once to build
  separate vertex and triangle arrays for each solid. Those arrays are
  validated independently, so touching bodies are never welded together. If
  the batched path cannot produce a passing result, the gate retries through
  the established per-body path.
  Vertices are welded to the nearest
  existing representative within the configured distance; zero tolerance
  welds only exactly coincident coordinates. After welding, triangles must be
  non-degenerate and unique, each undirected edge must occur twice with
  opposite directions, and each used vertex must have one connected cyclic
  link. Counts and thresholds appear in the check result.
  An `assembly` with several solids runs this check on each solid, because
  bodies may legitimately touch. Faces outside any solid fall back to the
  whole-shape check.

The optional `interference_checks(ctx)` provider is selected explicitly with
`aad_check(suites=["interference"])`; it is not part of the default hard gate.
It enumerates solid pairs, skips pairs whose axis-aligned boxes are disjoint,
and runs an OCCT common on every remaining pair. A common volume strictly above
`volume_threshold_mm3` (default `1e-6 mm³`) is an interference; zero-volume
contact and volumes at or below the threshold are clear. Overlaps include both
solid indexes and available labels, common volume, and a location in mm. The
check reports the total overlap count and keeps at most the 20 largest pair
details. It has a 60-second default budget (at most 120 seconds for direct
provider calls). A failed boolean is an unknown pair, and a budget-expired run
reports how many pairs were unchecked; neither case can produce a pass. If
some pairs are confirmed overlapping, the check fails while retaining unknown
and unchecked counts in its result.

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

### Assembly scaling before and after the 2026-11-05 mesh-path change

These are three warm-process runs per size on macOS arm64, Python 3.12.10,
build123d 0.13.0 and OCP 8.0.1.1.0. Each synthetic body was a 1 × 1 × 1 mm
box spaced 3 mm apart. `Model` is direct shape construction (`Box`, `Pos`, and
`Compound`), not worker startup. `Hard gate` is `hard_checks()` and includes
STEP export/re-import and the watertight mesh check; it excludes spec/process
checks and viewer scene generation. Values are median seconds (min–max).

| Parts | Model before | Gate before | Total before | Model after | Gate after | Total after |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 0.033 (0.030–0.041) | 2.743 (2.695–2.773) | 2.776 (2.726–2.815) | 0.021 (0.021–0.022) | 1.949 (1.904–2.140) | 1.970 (1.925–2.161) |
| 500 | 0.182 (0.171–0.268) | 14.926 (14.699–15.151) | 15.108 (14.967–15.322) | 0.140 (0.138–0.143) | 10.109 (9.939–10.338) | 10.249 (10.077–10.481) |
| 1,500 | 1.015 (0.820–1.239) | 45.378 (36.050–50.023) | 46.393 (36.869–51.262) | 0.904 (0.731–0.949) | 34.409 (32.319–44.470) | 35.358 (33.049–45.374) |

The medians fell by 29%, 32%, and 24% for 100, 500, and 1,500 bodies. The
1,500-run ranges overlap, so the single-machine result is suggestive rather
than conclusive. The default hard gate does not run pairwise interference
checks; the explicitly requested interference suite is pairwise. The STEP
round trip runs once per build and is retained as a required check, not
repeated per body.

Before the scene-index fix, one 1,500-part end-to-end worker build completed
in 74.5 s with `timeout_s=280`. Its phase timings were model 2.6 s, all hard
checks 31.0 s (mesh 24.9 s, STEP 4.6 s), spec 0.04 s, process 0.01 s, scene
37.9 s, write 0.09 s, and worker startup/transport 2.9 s. The summary named
`scene` as the slowest phase. Three full worker builds per size after the
scene-index fix measured these medians and ranges in seconds:

| Parts | Total build | Model | Hard gate | Scene | Worker startup/transport |
|---:|---:|---:|---:|---:|---:|
| 100 | 5.949 (5.128–8.052) | 0.252 (0.236–0.304) | 2.725 (2.482–3.338) | 0.105 (0.098–0.122) | 2.822 (2.255–4.226) |
| 500 | 15.420 (15.324–16.605) | 1.122 (1.003–1.263) | 11.519 (11.277–11.957) | 0.423 (0.421–0.439) | 2.527 (2.310–2.872) |
| 1,500 | 42.684 (42.547–46.244) | 3.066 (2.935–3.111) | 34.609 (33.553–37.327) | 2.009 (1.565–2.614) | 3.310 (3.309–3.635) |

The earlier end-to-end values are one sample per size, not a repeated
baseline. Worker startup and transport varied, so use the repeated ranges
above and the direct model/gate table rather than treating that single run as
a controlled comparison.

A cProfile of `make_scene()` on 1,500 separated boxes exposed a quadratic
feature-index rebuild: `_index_by_hash` ran 3,000 times, consuming 77.7 s
cumulative, while the profiler counted about 40.5 million wrapper and hash
calls. Each part rebuilt face and edge indexes over the full assembly. The
fix builds those two indexes once per scene. The post-fix profile made 3.6
million calls, used `_index_by_hash` twice (0.04 s cumulative), and completed
in 4.0 s wall (1.5 s in cProfile). Three additional unprofiled scene runs took
3.022–3.278 s (median 3.054 s). The worker phase took 1.565–2.614 s in its
three 1,500-part runs.

A cProfile run with wall timers around each gate check found that C-extension
calls dominated the mesh phase; Python-only cProfile time alone missed most
of that cost. Before the assembly mesh change, a 1,500-part profile measured
25.7 s for `mesh_watertight`, 6.2 s for `step_volume_drift`, and 1.1 s for
`brep_valid`; after it measured 26.8 s, 6.7 s, and 1.2 s respectively. The
gate makes one private assembly remesh and retains isolated checks, but the
profile did not show a meaningful reduction in its mesh phase. That remains
the slowest hard check at 26.8–28.3 s in the repeated full worker builds. The
reported 422 s outlier was not reproduced; without its original profile or
job artifacts, its cause remains unknown.

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
from build123d import Box, Compound, Cylinder, Pos
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

    for count in (100, 500, 1500):
        model_s, gate_s = [], []
        for repeat in range(3):
            job_dir = Path(job) / f"assembly-{count}-{repeat}"
            job_dir.mkdir()
            start = perf_counter_ns()
            shape = Compound(children=[Pos(index * 3, 0, 0) * Box(1, 1, 1) for index in range(count)])
            middle = perf_counter_ns()
            checks = hard_checks(ModelContext(shape=shape, kind="assembly", job_dir=job_dir))
            end = perf_counter_ns()
            assert all(c.status == "pass" for c in checks if c.level == "error")
            model_s.append((middle - start) / 1e9)
            gate_s.append((end - middle) / 1e9)
        for label, times in (("model", model_s), ("gate", gate_s),
                             ("total", [a + b for a, b in zip(model_s, gate_s)])):
            print("assembly", count, label, "median", median(times), "range", min(times), max(times), "s")
PY
```
