# Independent BREP inspection

`aiad.engine.validation.inspect_brep(path, candidate_dir=..., kind=..., units=...,
timeout_s=60, spec=None)` produces `GeometryEvidence` without executing the model
or trusting its metrics. The parent module imports only the standard library
and kernel-free engine modules. Native parsing and geometry checks run in a
fresh process through `sandbox.popen`, with no unsandboxed retry.

The trusted caller must first wait for the producing model process and its
descendants to exit, and prevent concurrent mutation of the candidate directory.
It supplies `kind` (`part` or `assembly`), coordinate units (`mm` or `in`) and
the spec snapshot. These inputs must not be replaced by model-reported values.
Inputs are detached finite plain JSON. BREP has no units: coordinates are
reported in the declared unit, with no rescaling or guessed source unit.

The input must be a nonempty regular file inside the candidate directory.
Final symlinks, multiply linked files, outside paths and the filesystem root
as candidate are rejected before launch. The parent opens without following a
final symlink, checks the descriptor, and copies the file to a newly created
private inspection directory. It checks file identity/size/timestamps around
the copy. Concurrent hostile directory replacement is outside this API's
precondition; path checks alone do not replace the OS sandbox.

The inspector reads only this snapshot, its installed code and runtime. It has
no read grant to the candidate's model.py or other job-authored Python files.
Its job directory is the only writable tree. No model source, model module path,
claimed metrics, feature ownership, parameter declarations or claimed checks
are accepted. Native import computes bbox, validity, body/topology counts,
surface area, solid volume and center of mass. Volumes and weighted centers
enumerate all solids, including nested assemblies. Native BRepCheck_Analyzer
and build123d's `is_valid` property both contribute to validity. The child runs
the installed `hard_checks` against caller inputs and its reloaded shape.

`snapshot_sha256` identifies the copied bytes. `input_sha256` identifies this
canonical JSON record (UTF-8, sorted keys, compact separators, finite numbers):

```json
{"protocol":1,"snapshot_sha256":"...","inputs":{"kind":"part","units":"mm","spec":{}}}
```

The parent checks these hashes and a fresh per-inspection nonce in the child
response, then rehashes the snapshot. It validates finite metric types, unit
agreement, unique hard-check records and success/failure consistency before
accepting metrics. A digest binds inputs; it does not prove design intent,
protect against a compromised native library, or replace independent audits.
Evidence applies to the snapshot, not to a later mutation of the original BREP.
The snapshot is retained under `inspection_dir/part.brep` even on failure when
the copy completed. The caller owns retention and cleanup of inspection dirs.

`ok=True` means this inspection read geometry that passed native validity,
positive volume/body count, and all evaluated error-level hard checks. It does
**not** assert full gate coverage. Error-level skips remain visible. Main's M0
gate currently returns `hard_gate_incomplete`; the complete gate in PR #46 is
a separate change. This API evaluates no spec/process requirements and exposes
neither `verified` nor `requirements_proven`. Integration with public run_build,
reconciliation with model output and promotion policy remain follow-up work
under issue #31. This standalone foundation does not close that issue.

The timeout covers snapshotting, launch, native work and response validation.
Cleanup uses the existing process-group kill and bounded pipe drain, which can
add up to two seconds after timeout. Synchronous local filesystem operations
are checked between chunks and cannot be forcibly interrupted by this API.
Stdout and stderr are retained. An unexplained native exit is `WORKER_CRASH`,
without guessing OOM, a kernel operation or a sandbox denial. A missing boundary
or failed OS launch reports `SANDBOX_DENIED`. A native parsing/check exception
reports the observed exception and `INVALID_SOLID`/`validate`, with feature
`unknown`. A memory/output budget and warm pool are separate requirements.

Run native tests with Python 3.12 and the pinned workspace:

```sh
uv run --locked --all-packages --all-extras --with pytest --with pytest-timeout \
  python -m pytest packages/engine/tests/test_validation.py -q
```

Linux requires bubblewrap and a host allowing its namespaces (issue #50).
Functional inspection tests are enabled there; simulated argv tests cannot
establish Linux runtime behavior. Windows remains fail-closed until an OS
filesystem/network boundary is implemented.
