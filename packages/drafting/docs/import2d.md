# Editable 2D import

`aiad_drafting.import2d` reads planar DXF and static SVG into an editable,
JSON-serializable record set. The parser keeps source curves analytic. It does
not tessellate Beziers, ellipses, elliptical arcs, or polyline bulges to fit a
backend that cannot represent them.

```python
from aiad_drafting.import2d import parse_dxf, parse_svg

dxf = parse_dxf("drawing.dxf", units="mm", layer_map={"Score Lines": "score"})
svg = parse_svg("drawing.svg", units="in")

payload = dxf.to_dict()  # JSON-safe, including provenance and warnings
```

The public functions are:

```python
parse_dxf(path, units=None, layer_map=None) -> Imported2D
parse_svg(path, units="mm", layer_map=None) -> Imported2D
```

`Imported2D` has `units`, `entities`, `layers`, `source`, and `warnings`
attributes, plus `to_dict()`, `from_dict()`, and `to_sheet(name=None)`. The
`source_metadata` property aliases `source`. The source record includes the
resolved source path, filename, SHA-256 digest, format details, units, and
source layer details. `from_dict()` restores coordinate pairs as tuples.

## Entity records

Each entity is a dictionary with `name`, `kind`, `layer`, `points`, `radius`,
`start_angle`, `end_angle`, `closed`, `text`, `height`, and `metadata` fields.
`points` are `(x, y)` tuples in `Imported2D.units`. Lines contain start and end
points; circles and arcs contain their center; polylines contain ordered
vertices; Bezier records contain endpoints and control points.

DXF modelspace support includes `LINE`, `CIRCLE`, `ARC`, `LWPOLYLINE`, `TEXT`,
and `MTEXT`. Entity handles, source type, source layer, source angles, OCS
normal, widths, and bulges are retained in metadata. Arc angles are degrees in
the imported XY plane; `metadata["ccw"]` records the sweep direction after OCS
conversion. A reflected OCS changes the sign of transformed bulges while the
original values remain in `source_bulges`. Unsupported modelspace entities,
tilted OCS planes, and nonplanar lines are rejected with `Import2DError`.
DXF paper-space layouts are outside the import scope; geometry is read from
modelspace.

SVG supports mechanical primitives, text data, and paths with line, cubic,
quadratic, and elliptical-arc segments. An SVG circular arc is recorded as an
`arc`; a noncircular arc is an `elliptical_arc`. Exact Bezier control points
and ellipse center, radii, axis vectors, rotation, parameters, and sweep are
retained in metadata. The original path `d` value and element attributes are
also kept. Nested affine transforms are applied to the records, including the
final Y-axis conversion into the y-up drawing plane. Ellipses transformed by a
nonuniform scale remain ellipses.

Unsupported SVG elements, active content, external references, DTDs, entity
declarations, hidden geometry, and unknown or percentage-only physical scales
are rejected. Text content is imported as data and is never executed or treated
as instructions.

## Units and scale

For DXF, `$INSUNITS` determines the output units when `units` is omitted. Inch
drawings default to inches; other recognized physical DXF units default to
millimeters. Passing `units="mm"` or `units="in"` converts a drawing with
declared units to the requested output unit. A unitless DXF requires `units`;
that value interprets its raw coordinate values and produces a warning. The
importer does not guess a scale for unitless coordinates.

For SVG, root `width` and `height` establish the physical viewport. Supported
length units are CSS pixels (`px` and unitless root lengths), inches, mm, cm,
quarter-millimeters (`Q`/`q`), points, and picas. CSS pixels use the SVG/CSS
rule of 96 pixels per inch. A `viewBox` maps its coordinates into that
viewport, and `preserveAspectRatio` is applied by the parser. One physical
dimension is sufficient when a `viewBox` supplies the aspect ratio. A
`viewBox` alone or percentage dimensions without an outer physical viewport do
not establish scale and are rejected. Output is converted to the requested
millimeters or inches.

## Layers

DXF layer names and SVG IDs/group IDs are retained in provenance. SVG layer
names come from `data-layer`, Inkscape layer labels, or inherited group layers.
`layer_map` maps source names to imported layer names before normalization;
matching is case-insensitive when an exact key is absent. Common cut, score,
and engrave names normalize to `cut`, `score`, and `engrave`. Source layer `0`
and the SVG default `layer0` map to `cut`.

## Sheet2D adapter

`Imported2D.to_sheet(name=None)` imports `Sheet2D` lazily. Once
`aiad_drafting.model2d.Sheet2D` is available, it creates a sheet using the
imported units and layers, then calls its `line`, `circle`, `arc`, `polyline`,
and `text` methods with coordinate tuples. Source entity names and parser
provenance are attached to entity metadata where supported.

The current adapter can convert lines, circles, counterclockwise circular
arcs, straight-segment polylines, and unrotated text. It raises
`NotImplementedError` for Beziers, ellipses, elliptical arcs, bulged
polylines, clockwise arcs, rotated or nonuniformly transformed text, and any
other record type the backend cannot preserve exactly. Parsing and JSON
serialization do not require the Sheet2D backend. In this prepared checkout
`model2d.py` is not present, so integration of supported records with the live
backend must be verified when that implementation lands.

## Runtime dependencies

DXF parsing uses `ezdxf==1.4.5`. SVG parsing uses the MIT-licensed
`svgelements` parser; this implementation was exercised with version 1.9.6.
`svgelements` is currently present transitively in the local environment and
should be declared as a direct runtime dependency by the package owner. It is
imported lazily, so DXF parsing and the `Imported2D` data model do not require
it.
