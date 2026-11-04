# AIAD engine bootstrap

This package is the M0 S1 worker bootstrap. It publishes kernel-free contract
dataclasses from `aiad.engine.types`, re-exports build123d names lazily from
`aiad`, and runs each submitted script in a fresh Python subprocess.

## Install and run

The engine pins the kernel versions in `pyproject.toml`. From this directory:

```bash
uv sync --python 3.12
uv run python -m unittest discover -s tests -v
```

A simple request can build a shape directly or use a `BuildPart` context:

```python
from aiad.engine.jobs import run_build
from aiad.engine.types import BuildRequest

result = run_build(BuildRequest(source="result = Box(20, 10, 6)"))
print(result.status, result.metrics, result.artifacts["part.brep"])
```

`BuildRequest` accepts `source`, `project_dir`, `job_dir`, `rev`, `kind`,
`units`, parameter overrides, a spec dictionary, material/process strings,
`timeout_s` (60 seconds by default), and an optional `preview` flag. Model
scripts can use `params(name=value, ...)` to declare named values and apply
overrides. A plain shape assigned to `result`, or a completed `BuildPart`
context, produces one implicit `main` `FeatureInfo`.

`BuildResult` carries the draft/failed/verified status, geometry metrics, hard
and optional checks, a `Failure` with source file and line when available, and
artifact paths. The worker writes `model.py` and `part.brep` under its job
directory. `BuildResult.context.shape` is `None` in the parent process by
design; downstream code can load the preserved `part.brep` in its own kernel
process. Inside optional worker hooks, `ModelContext.shape` is the live shape.

## Current limits

- The hard gate checks build123d B-rep validity and one or more solids. It does
  not check tiny edges, slivers, STEP round trips, clearances, or requirements.
- No requirement set is assumed. Without successful requirement checks, a
  successful model is `draft`; the engine does not mark validity as proof of
  design intent. Promotion requires unique IDs and complete, finite results;
  malformed requirements or error-level skips/warnings remain `draft`.
- `aiad_quality` and `aiad_render` hooks are optional. Missing hooks produce
  explicit `skip` checks; hook exceptions produce `warn` checks and retain the
  BREP. An unavailable or skipped spec check cannot produce `verified`.
- Scene and render hook records use the existing `Check` shape with IDs such as
  `scene_generation_unavailable` and `render_not_requested`; they describe
  pipeline availability, not additional geometry checks.
- `kind="2d"` is not implemented in this bootstrap. `kind="part"` and
  `kind="assembly"` use a single build123d shape.
- Jobs use one subprocess each and are killable on timeout. This is not a warm
  worker pool, an OS sandbox, a memory limit, or a revision store. Arbitrary
  Python in a model can still access inherited environment variables, files,
  and network resources before the process exits.
- Units are carried as request metadata. The model's numeric coordinates are
  not rescaled or inferred by the worker.

See `fixtures/models/fan_mount.py` for a small parametric context model and the
JSON fixtures under `fixtures/contexts/` and `fixtures/results/` for example
cross-package data.
