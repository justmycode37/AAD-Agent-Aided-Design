# Native 2D export and scene packages

`aiad_drafting.io2d.export_2d(ctx, fmt, out, *, layers=None)` writes one raw
SVG or R2013 DXF file and returns its resolved path. `out` names a file. The
context is structural: it exposes `sheet`, `units`, and may expose
`job_dir`. `sheet` is either a `Sheet2D` or the JSON-native mapping returned
by `Sheet2D.to_dict()`. Context and sheet units must both be `mm` or `in` and
must match.

Exports read the source entity records directly. They do not call the drawing
layout API, inspect context metrics, or change sheet history. Coordinates and
physical units stay in the model's coordinate system. A supplied `layers`
filter selects source layers; otherwise all layers and entities are exported.

## Supported source entities

| Sheet2D entity | SVG | DXF |
|---|---|---|
| Line | `<line>` | `LINE` |
| Circle | `<circle>` | `CIRCLE` |
| Circular arc | Analytic `A` path | `ARC` (clockwise arcs are emitted as the same counter-clockwise locus with reversed endpoints) |
| Open or closed polyline / rectangle | `<polyline>` / `<polygon>` | `LWPOLYLINE` |
| Quadratic or cubic Bezier | `Q` / `C` path commands | Clamped degree-2 / degree-3 `SPLINE`, with unit weights |
| Full or trimmed ellipse | `<ellipse>` / analytic `A` path | `ELLIPSE` |
| Text | Editable `<text>` with separate `<tspan>` baselines for multiline content | Editable `TEXT`, one entity per line |

Ellipse rotation and text `metadata["rotation_deg"]` are retained. DXF ellipses
store the longer semiaxis as their major axis; when source `rx < ry`, the DXF
parameter is shifted by -90 degrees so the source endpoints and curve remain
the same. SVG text is escaped as text content. Imported source attributes,
scripts, links, and other markup are never copied into the generated SVG.
Multiline text uses baselines spaced by 1.3 source text heights in both formats,
including blank lines. CRLF and CR are normalized to line breaks, tabs expand
to eight-column stops, and rotation applies to the complete baseline layout.

An active arbitrary transform in entity metadata is rejected. The exporter does
not flatten transforms. The SVG importer may keep original transform fields as
provenance after resolving them into exact Sheet2D coordinates; those provenance
fields are not applied a second time. Sheet history and constraints stay on the
source sheet and are not turned into dimension labels or DXF dimension entities.

Raw exports contain geometry and text only. They do not add a page border,
title block, dimensions, kerf, or drawing annotations. Text remains editable
text; it is not converted to cutting paths.

## SVG viewport and geometry bounds

SVG `width` and `height` use the sheet's physical unit (`mm` or `in`), and the
`viewBox` maps the source coordinate extent into SVG's y-down viewport while
keeping the original coordinates in the element geometry. The small positive
viewport fallback for a zero-width or zero-height sheet is only a viewBox span;
it does not add geometry.

`make_scene(ctx, out)` writes exactly `scene.bin.gz` and `scene.json` in `out`.
The gzip payload follows `svg-1`:

```json
{"svg":"...","sheet":{"w":120,"h":80,"units":"mm"},"layers":["cut"],"entities":[]}
```

Here `sheet.w` and `sheet.h` are SVG viewport spans. The manifest's `bbox` is
the exact axis-aligned bound of native geometric entities in source units;
editable text is omitted from that bound because its glyph layout is not native
geometry. Bezier extrema and rotated ellipse extrema are calculated from their
analytic curves. A text-only scene has a zero geometry bbox, even though the SVG
viewport can still show its insertion point.
The viewport includes a conservative text envelope so annotation is not clipped
at its insertion point. This visual padding never changes the manifest's native
geometry bbox or a part measurement.

Scene gzip bytes use `mtime=0`; `scene_id` is their SHA-256. Scene status is
always `draft`. Metrics, status claims, and verification claims on the caller
context do not promote it. The manifest's `parts` list contains one stable entry
per source entity.

## Output safety

When `ctx.job_dir` is present, the resolved output file or scene directory must
be inside it. File exports use a same-directory temporary file and atomic
replace. Scene packages stage both files before publishing and restore existing
artifacts if publication fails. No output path is inferred from project state.
Confined writes on macOS/Linux walk directories without following symlinks and
keep staging, replacement and rollback relative to pinned directory handles.
Staged regular files remain open until publication is checked against their
original inode, so replacing a staged name with a symlink triggers rollback.
Replacing a directory during creation or publication cannot redirect writes
outside the pinned worker directory. Platforms without POSIX directory-handle
operations reject confined exports explicitly; unconfined local exports remain
available. This protection complements the worker's OS sandbox.

The module imports neither the engine nor the geometry kernel. DXF writing
loads the pinned permissive `ezdxf` dependency only when requested.
