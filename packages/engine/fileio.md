# Native file I/O

`aiad.engine.fileio` imports without loading build123d or OCP. Call it in a
kernel worker: the platform is responsible for sandboxing, revision promotion,
and deciding whether an unverified revision may be exported.

```python
from pathlib import Path
from aiad.engine.fileio import export, import_file

# ctx is a ModelContext with a live build123d shape and an existing job_dir.
step = export(ctx, "step", ctx.job_dir / "part.step", schema="AP242")
mesh = export(ctx, "glb", ctx.job_dir / "part.glb", tolerance=0.01)

imported = import_file(step, ctx.job_dir, units="in", name="base")
assert imported.context.shape is not None
print(imported.history_notice)
```

`export(ctx, fmt, out, **options) -> Path` accepts `step`, `brep`, `stl`,
`3mf`, and `glb` (case-insensitive), and returns the canonical output path.
The job directory and destination's parent must already exist. A relative
`out` is relative to `ctx.job_dir`. Paths must be lexically and canonically
inside that directory; a destination symlink, including a dangling symlink, is
rejected. The writer uses a unique temporary sibling, checks that file, and
then uses `os.replace`. A failed write or check leaves an existing destination
unchanged and removes the temporary file. These checks assume directory
components are not replaced concurrently by an adversary; they do not replace
an OS sandbox.

Options are shared across formats so the platform can pass one options object
to a set of exports. Unknown options are errors.

| Option | Default | Meaning |
| --- | --- | --- |
| `schema` or `step_schema` | `AP214` | STEP schema: `AP214` or `AP242`. Supply only one of the two names. |
| `step_volume_tolerance` | `1e-6` | Maximum relative physical volume drift after STEP re-import; finite and nonnegative. |
| `tolerance` | `0.001` | Positive linear tessellation deflection in `ctx.units`, for STL, 3MF and GLB. |
| `angular_tolerance` | `0.1` | Positive angular tessellation deflection in radians, for STL, 3MF and GLB. |

These are numerical settings, not manufacturing limits.
Mesh exports use fresh absolute OCCT deflection in the context's coordinate
units, including when a source already has cached triangulation.
STEP export requires a finite, positive-volume solid so the relative comparison is defined. The STEP
writer uses build123d 0.13's XDE document builder and OCP 8's CAF writer:
build123d 0.13's `export_step` was observed to emit an AP214 header even when
OCCT's AP242 setting was selected. The native STEP settings are restored after
each call; a module lock serializes this module's STEP operations, but cannot
serialize unrelated code that changes OCCT's process-global settings.

## Units

`ctx.shape` coordinates are in `ctx.units`, which must be `mm` or `in`.

- STEP: the XDE document declares the shape's unit; the writer is configured
  for millimetres. Re-import through OCCT yields millimetre coordinates. The
  volume check compares that result in mm³ against the original volume
  multiplied by `25.4³` when the context uses inches.
- BREP and binary STL: these formats carry no unit. Export retains the
  context's coordinate numbers; receivers must be told whether those numbers
  represent millimetres or inches.
- 3MF: coordinates retain the context's numbers and the model declares the
  matching `millimeter` or `inch` unit. Root assembly placement is baked into
  separate part resources along with their local placements.
- Binary GLB: the native writer is given the context unit and emits glTF
  coordinates in metres, with the native Z-up to glTF Y-up orientation change.
  The copied root location is adjusted so this rotation also applies to an
  existing root translation.

`import_file(path, job_dir, *, units="mm", source_units=None, name=None,
role="base", project_dir=None) -> ImportedModel` reads STEP (`.step`, `.stp`),
BREP, and binary or ASCII STL. `units` specifies the **actual coordinates** in
the returned context. STEP's embedded units are handled by OCCT, which returns
millimetres; requesting inches scales those coordinates by `1/25.4`.
`source_units` is rejected for STEP. BREP and STL have no embedded unit:
`source_units` can be `mm` or `in`; when omitted, millimetres are assumed and
the result's `source_units_assumed` and `history_notice` say so. Conversion to
the target unit scales B-rep geometry or STL triangle vertices, respectively.
When an imported assembly needs B-rep scaling, its topology is flattened to
avoid build123d 0.13 retaining unscaled child objects. The result reports this;
component names and colors are not retained in the flattened assembly.

`ImportedModel.context` contains the live shape and one `FeatureInfo` with
face and edge indices, named `base` unless `name` is given. `history_notice`
explicitly says the source's feature history is unavailable. STL returns a
triangulated reference face, not an editable solid. `role` accepts `base` or
`reference` and records the caller's intent; this function does not attach it
to another model or create a revision. `project_dir` defaults to `job_dir` if
the caller does not supply it. The live shape is usable only in the worker
process; a JSON caller must persist or reload geometry separately. The current
platform import worker does not pass target/source units or create a revision,
so it uses the millimetre defaults and reports metadata rather than attaching
the imported shape to a project. Source-file authorization and staging are the
caller's responsibility.

## What export checks

- STEP: checks that the file header declares the requested schema, OCCT can
  read it, the returned B-rep is valid and has a solid, and its finite positive
  physical volume, summed over all native solids including nested assemblies,
  differs from the source by at most the configured relative
  tolerance. This compares total volume, not every face or feature.
- BREP: re-imports with OCCT and checks that the resulting B-rep is non-null
  and valid. It does not compare every entity with the source.
- STL: checks the binary header, nonzero triangle count, exact file length,
  and finite normal and vertex values. It does not check watertightness.
- 3MF: reads the package with Lib3MF, checks the declared unit, nonempty
  valid mesh resources and that each build item references a parsed mesh. It
  does not establish watertightness or compare the mesh to the source.
- GLB: checks the glTF 2 binary header, chunk and embedded-buffer lengths,
  JSON, buffer views, triangle position and index accessors, finite positions,
  index bounds, and a scene reference to a mesh. This is not a complete glTF
  schema, material, transform, or geometric fidelity check.

None of these checks establishes design intent, full geometry equivalence, or
`verified` status. Export raises `FileIOError` with `EXPORT_FAILED` or
`IMPORT_FAILED` and a phase when it cannot complete. The caller must apply the
revision's quality gates and `allow_unverified` policy separately.
