# AAD engine bootstrap

This package is the M0 S1 worker bootstrap. It publishes kernel-free contract
dataclasses from `aad.engine.types`, re-exports build123d names lazily from
`aad`, and runs each submitted script in a fresh OS-sandboxed Python subprocess
through `aad.engine.sandbox.popen`.

## Install and run

The engine pins the kernel versions in `pyproject.toml`. macOS requires
`sandbox-exec`; Linux requires bubblewrap (`sudo apt-get install -y bubblewrap`
on Ubuntu/Debian) and a host that permits its sandbox launch. Windows is not
implemented. Missing or unsupported boundaries return `SANDBOX_DENIED` with
phase `sandbox`; there is no opt-out or unsandboxed fallback.

From this directory:

```bash
uv sync --locked --python 3.12
uv run --locked --python 3.12 python -m unittest discover -s tests -v
```

A simple request can build a shape directly or use a `BuildPart` context:

```python
from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest

result = run_build(BuildRequest(source="result = Box(20, 10, 6)"))
print(result.status, result.metrics, result.artifacts["part.brep"])
```

`BuildRequest` accepts `source`, `project_dir`, `job_dir`, `rev`, `kind`,
`units`, parameter overrides, a spec dictionary, material/process strings,
`timeout_s` (60 seconds by default), and an optional `preview` flag. Model
scripts can use `params(name=value, ...)` to declare named values and apply
overrides. A plain shape assigned to `result`, or a completed `BuildPart`
context, produces one implicit `main` `FeatureInfo`. Without an explicit
`result`, the worker selects the last successfully completed outermost,
non-private `BuildPart`. Nested helpers, unentered builders and failed contexts
are ineligible. Assign `result = helper.part` to select a helper deliberately.

`BuildResult` carries the draft/failed/verified status, geometry metrics, hard
and optional checks, a `Failure` with source file and line when available, and
artifact paths. The worker writes `model.py` and `part.brep` under its job
directory. `BuildResult.context.shape` is `None` in the parent process by
design; downstream code can load the preserved `part.brep` in its own kernel
process. Inside optional worker hooks, `ModelContext.shape` is the live shape.

Model source must not do I/O (`CONTRACTS.md` section 3). The launcher supplies a
sanitized environment, job-local HOME/temp/cache paths, closed descriptors,
pipes, and the OS filesystem/network policy together. `project_dir` is metadata,
not a read grant. A staged `asset(...)` API remains required; this package does
not add one. See [sandbox.md](sandbox.md) for the boundary and its tests.

## Current limits

- The [geometric hard gate](gate.md) checks native B-rep validity, closed
  shells, body count, small topology, STEP volume drift and mesh manifoldness.
  Configured small-topology thresholds are heuristics. Clearances and design
  requirements are separate quality checks. These checks still share a model
  process; a separate trusted validator is required to attest their results.
- No requirement set is assumed. Without successful requirement checks, a
  successful model is `draft`; the engine does not mark validity as proof of
  design intent. Promotion requires unique IDs and complete, finite results;
  malformed requirements or error-level skips/warnings remain `draft`.
  The parent also withholds `verified` from worker pipe results: model code can
  write that pipe. A separate trusted validator is required before promotion.
  Private, nonempty regular job files are required for a reported success;
  symlink/hardlink artifacts are rejected and accepted paths are canonical.
  Reusing a job's existing `part.brep` is refused. These checks do not
  independently prove BREP validity. Worker proof claims and scene status stay
  draft pending a trusted validator, including binding its evidence to inputs
  and artifacts.
- `aad_quality` and `aad_render` hooks are optional. Missing hooks produce
  explicit `skip` checks; hook exceptions produce `warn` checks and retain the
  BREP. An unavailable or skipped spec check cannot produce `verified`.
  Editable optional-package imports receive a directory-only search grant.
  Native previews run after the model exits in a separate sandboxed process,
  with at most 10 seconds and the remaining build budget. Preview hangs and
  native exits become warnings while preserving the BREP and geometry checks.
  Successful VTK rendering inside the Mac policy remains unproven; a native
  import probe hung. See the observed limits in `sandbox.md`.
- Scene and render hook records use the existing `Check` shape with IDs such as
  `scene_generation_unavailable` and `render_not_requested`; they describe
  pipeline availability, not additional geometry checks.
- `kind="2d"` builds a `Sheet2D` and saves `sheet.json` instead of `part.brep`. `kind="part"` and
  `kind="assembly"` use a single build123d shape.
- Jobs use one sandboxed subprocess each, with a bounded timeout and
  process-group/pipe cleanup, including pipes held after the leader exits.
  Native launcher diagnostics are retained as sandbox failures; unexplained
  worker exits stay crashes, without inferring OOM or a kernel cause.
  A warm worker pool, memory budgets and a revision store remain required.
- S2's trusted post-build operations still need to adopt this launcher under
  [issue #21](https://github.com/justmycode37/AAD-Agent-Aided-Design/issues/21).
  Linux package/e2e CI must install bubblewrap under
  [issue #50](https://github.com/justmycode37/AAD-Agent-Aided-Design/issues/50);
  public Linux build tests are not skipped when that dependency is absent.
- Units are carried as request metadata. The model's numeric coordinates are
  not rescaled or inferred by the worker.

See `fixtures/models/fan_mount.py` for a small parametric context model and the
JSON fixtures under `fixtures/contexts/` and `fixtures/results/` for example
cross-package data.
