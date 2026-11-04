# Named modeling and semantic selection

`from aiad import *` re-exports build123d and adds `Param`, `params`, `meta`,
`part`, `feature`, `face`, `edge`, and `vertex`. Kernel imports remain lazy.
[The named mount fixture](../../fixtures/models/named_mount.py) builds a Box,
four through holes, and four real fillets with the pinned kernel.

Declare adjustable values in one `params()` call per build. Values may be
finite numbers, strings, or nested tuples/lists of those values. Overrides
must preserve the declaration's structure and scalar types; numeric int/float
overrides are interchangeable. Unknown overrides, overrides without a
declaration, nonfinite values, and boolean numeric values fail with `BAD_INPUT`.
Parameter names are public Python identifiers.

`Param(value, min=None, max=None, unit=None)` adds inclusive scalar numeric
bounds and a descriptive unit. Both bounds and defaults must be finite and
valid before an override is applied. Bounds are declarations, not inferred
kernel limits. Units do not convert coordinates. Applied values are in
`ctx.params`; live slider records are in `ctx.param_definitions`.

`meta(**fields)` records finite JSON data. `units` must be `mm` or `in`;
`name`, `material`, and `process`, when present, are nonempty strings. Physical
declarations in the model, build request, and spec must agree. A conflict is a
`BAD_INPUT` failure before BREP export. A model/spec material or process fills
an unspecified request value. An inch model requires an inch request; numeric
geometry is never silently rescaled. The same effective units/material/process
reach metrics, gates, and optional hooks.

`with part("name") as bp:` opens a BuildPart and labels its output. There is
one named part per build. With no explicit `result`, it supplies the output
even if later helper builders exist. This is a single-part helper; assemblies
are not implemented here.

`with feature("name", intent="..."):` surrounds operations in the active
BuildPart. Names are unique per build; `main` and `unknown` are reserved, and
feature blocks cannot nest. Records contain intent, source start/end lines,
dependencies, and the final faces, edges, and vertices owned by each feature.
Empty declarations remain visible after a later operation removes their
topology. Live selectors see changes before the feature block closes.

Tracking compares topology before/after each worker builder operation and at
feature boundaries. OCP `IsSame` identity retains ownership. Changed topology
belongs to the block that changed it; geometric proximity or equal dimensions
never prove inherited ownership. Dependencies come from selected feature
references and reported modified/generated/removed operation history. There
is no claim of complete ancestry when the operation supplies no history.

For example, a hole can replace the plate's top/bottom faces, and a fillet can
replace every remaining plate face. `face(feature='plate', ...)` can then be
empty or ambiguous; it does not silently follow the closest face. A separate
result without proven topology identity to the named builder has `unknown`
ownership. Plain build123d scripts keep their implicit `main` feature.

`aiad.engine.select.resolve(ctx, selector, expect=None)` returns `Entity`
metadata. Its grammar is one literal call to `face`, `edge`, or `vertex` with
these optional keywords:

- `feature`: exact owning name, or `unknown` for unassigned topology.
- `kind`: analytic geometry type, such as `plane`, `cylinder`, `line`, or `circle`.
- `where`: `max_x`, `min_x`, `max_y`, `min_y`, `max_z`, `min_z`, or
  `parallel_x`, `parallel_y`, `parallel_z`.
- `max_min_xyz`: an extreme string, `(axis, 'max'/'min')`, or a mapping such as
  `{'axis': 'z', 'extreme': 'max'}`. This is an alternative to `where`.
- `parallel_xyz`: an axis string or a finite nonzero three-number vector.
  It matches plane normals and straight-edge directions; direction sign is
  immaterial. This is an alternative to `where` and `max_min_xyz`.
- `nth`: a zero-based position in semantic order, never kernel enumeration.

Selectors use `ast.parse` and `ast.literal_eval`, with no expression execution.
Calls inside arguments, attributes, indexing, arithmetic, expansions, and raw
`index=` selectors are rejected. `expect` is a separate nonnegative integer
argument. Omission requires one result; an explicit count permits a group,
and `expect=0` permits an empty result. Live helpers accept the same keyword
filters and `expect`, returning one build123d topology object or a list.

Ordering uses feature, analytic type, mass centroid (position for a vertex),
plane normal/unsigned line direction, bounds, radius, area, and length, rounded
to nine decimal places. Extreme filters compare centroid coordinates with an
absolute tolerance of `1e-9` model units. Tied semantic keys cause
`SELECTOR_AMBIGUOUS`, including with `nth` or a matching group count; there is
no raw-index tie breaker. Missing geometry properties stay absent.

Count failures return `SELECTOR_EMPTY` or `SELECTOR_AMBIGUOUS` and actual
candidate metadata. Empty matches include the available topology of that
kind as alternatives, rather than pretending those alternatives matched.
Candidate selectors use semantic filters/order. A unique selection keeps its
count guard instead of adding an unnecessary `nth=0`.

For exact measurement, resolve in a live context and pass its entity to
`entity_shape(ctx, entity)`. The returned topology is bound to that context
and shape. Serialized entities, another context, or a replaced context shape
require a fresh resolution. `Entity.index` is only revision-local metadata,
not a persistent selector or an exact measurement handle.

The worker wire retains params, physical and generic metadata, part name,
parameter range declarations, feature intent, named faces/edges/vertices,
source lines, dependencies, and measured entity metadata. Parent contexts have
no live shape: semantic queries can use their metadata, while exact geometry
operations require loading and resolving against a live shape in a worker.

Validation covers the named Box/hole/fillet fixture, edits with retained names
or explicit candidates, topology identity/replacement, source/dependency
tracking, declarations, injection, ties, and same-context measurement. This
is not the 30-part regression corpus. Pool, sandbox, revision, I/O, gate,
assembly, and status work is outside this change.

```sh
UV_PROJECT_ENVIRONMENT=/tmp/aiad-s1-modeling uv run --locked --python 3.12 \
  --package aiad-engine python -m unittest discover -s packages/engine/tests -v
```
