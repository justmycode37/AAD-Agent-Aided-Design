# Native 2D model primitives

`aiad_drafting.model2d` provides a small editable 2D model whose geometry is
represented by build123d and OCCT. Import the API directly:

```python
from aiad_drafting.model2d import Sheet2D, latest_sheet, sheet2d

panel = sheet2d("laser_panel", units="mm")
panel.rectangle("outline", 100, 60, origin=(-50, -30))
panel.circle("mount_hole", (-40, -20), 2.2)
panel.text("label", "AIAD", (-5, 3), height=3)
```

`sheet2d()` returns a `Sheet2D` directly, and that object can also be used in a
`with` statement. `latest_sheet()` returns the active sheet inside a context or
the most recently created sheet in the current execution context, which lets a
worker capture a sheet when a script does not return it explicitly.

## Geometry and names

`Point2D` stores `name`, `x` and `y`. `Entity2D` stores a kind, layer, point
names, optional radius and arc angles, a closed flag, optional editable text and
height, and JSON-native metadata. Supported kinds are `line`, `circle`, `arc`,
`polyline`, `rectangle`, and `text`.

Coordinate arguments accept an existing point name, a `Point2D`, or an `(x, y)`
sequence. Tuple coordinates create stable automatic points: line endpoints use
`<entity>.start` and `<entity>.end`, circle and arc centers use
`<entity>.center`, and polyline vertices use `<entity>.p0`, `<entity>.p1`, and
so on. Explicitly shared point names remain shared in each entity's
`points: tuple[str, ...]`.

Primitive methods are `point`, `line`, `circle`, `arc`, `polyline`,
`rectangle`, and `text`. The default units are millimeters; inches are also
accepted. Coordinates are planar at z=0 and are interpreted in the sheet's
units. Zero-length lines, zero-area closed polylines, zero or non-finite
radii/dimensions, and zero or non-finite extrusion heights raise `ValueError`.

Layers `cut`, `score`, and `engrave` exist on every new sheet. Additional layer
names are added when used. `text()` creates editable text on `engrave` by
default. Text is never treated as a cutting contour: selecting a text-bearing
layer in `to_shape`, `to_face`, `offset`, boolean, or `extrude` raises a clear
error. Text outlining is not part of this primitive API.

## Exact geometry operations

- `to_shape(layers=("cut",))` returns a build123d compound of native edges and
  wires. Lines, circles, arcs, and polygon segments stay analytic.
- `to_face(layers=("cut",))` combines connected edges into closed contours,
  finds nested contours, and constructs faces with holes. `bounding_box()`
  returns build123d's native bounding box for selected geometry.
- `offset(distance, layers=("cut",), join="arc")` uses OCCT's planar offset
  operation. Positive distance expands each selected closed contour and
  negative distance contracts it. `join` accepts `"arc"` or `"intersection"`.
- `union(other)`, `difference(other)`, and `intersection(other)` use exact OCCT
  B-rep booleans on closed faces. Internally, profiles are extruded by one
  model unit for the boolean, same-domain faces are unified, and the top planar
  result is extracted. The default layer is `cut`; `layers`,
  `other_layers`, `layer`, and `name` can be supplied. Both sheets must use the
  same units.
- `linear_pattern(entities, count, spacing=(dx, dy))` and
  `radial_pattern(entities, count, center=(x, y), angle=360)` return a derived
  sheet containing the originals and exact transformed copies. A full radial
  turn omits the duplicate endpoint; a partial turn includes both endpoints.
- `extrude(height)` returns a real build123d `Solid` or `Compound`. It exposes
  `aiad_history` and the JSON-native editable source as `aiad_source_sheet`.

Derived operations add a history entry with source sheet/entity names, source
geometry, parameters, result names, and—where available—result geometry.
Patterned entities receive deterministic suffixes such as
`hole.linear1` and `hole.radial2`. Boolean and offset results preserve exact
OCCT lines and circular arcs. If OCCT returns a curve outside those supported
types, the operation raises instead of approximating it with a polyline.

## Serialization

`sheet.to_dict()` and `Sheet2D.from_dict(data)` round-trip points, entities,
layers, history, and constraints using JSON-native values. They perform no file
I/O. Metadata, history, and constraints must be finite JSON data; non-serializable
objects are rejected when the sheet is serialized or used as operation
provenance.

The `constraints` list is preserved for the separate constraint solver. This
module does not solve or reinterpret constraints. Offset and boolean-derived
sheets start with an empty active constraint list because source constraints
cannot be mapped reliably to changed topology; their source geometry and
history remain available. Pattern copies leave the original constraints
attached to the original named points and entities.

## Runtime dependencies and limits

The package already declares the direct runtime dependencies used here:
`build123d==0.13.0` and `cadquery-ocp-novtk==8.0.1.1.0`. No new direct
dependency or project-file change is required. The implementation uses only
the existing permissively licensed stack and does not outline text, import
paid-standard content, or use a GPL solver.

The model is planar in XY, and booleans require equal units. The editable output
schema covers exact lines, circular arcs, and circles. General ellipses and
freeform splines are not converted to tessellated entities; operations that
produce those curves report an unsupported native result. Offsetting operates
on the selected paths independently, so a positive offset expands an inner
circle contour as well as an outer boundary.
