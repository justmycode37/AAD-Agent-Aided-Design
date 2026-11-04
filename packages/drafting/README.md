# aiad-drafting

S4 creates composed drawing sheets from a build123d shape or an assembly
context. It writes SVG, R2013 DXF, vector PDF, and a PNG preview. The public
entry point is:

    from aiad_drafting import DrawingRequest, make_drawing

    result = make_drawing(ctx, DrawingRequest(), output_dir)

ctx is read structurally and should expose a build123d shape as shape.
units may be mm or in; no import from aiad.engine.types is needed.
Requests may also be dictionaries. DrawingResult.dimensions includes measured
values and their geometry sources.

`DrawingRequest` also accepts `notes`, `revisions`, and `bom`. Notes are strings;
each revision is a mapping with `revision`, `date`, `description`, and `by`
fields. `bom=True` or `bom="auto"` requests a bill of materials. BOM rows are
read from `children`, `components`, or `parts` exposed by the context, its
assembly, or its shape. Quantities count actual leaf child instances, grouped
by an explicit part number or component id, then by shared component object
identity. A combined shape without an assembly tree is not enough to invent a
BOM or a quantity and raises a ValueError.

## First-sheet assumptions

- The first release lays out front, top, right, and isometric views. Orthographic
  view curves come from OCCT hidden-line removal. Isometric curves are projected
  from the model edges, with visible/hidden classification left to OCCT.
- Automatic hole discovery targets Z-axis cylindrical through-holes. A kernel
  clearance check excludes blind holes and counterbores even when raised
  geometry changes the overall Z bounds. Other cylindrical features remain
  projected geometry but are not guessed to be holes.
- Automatic dimensions cover the overall x/y/z size, hole diameter and hole
  datum coordinates. Dimensions are measured from the supplied shape. No
  supplied text or dimension value is treated as a measurement.
- The common scale is selected by packing annotation-aware view cells into the
  available sheet area. First-angle (`iso`) and third-angle (`asme`) orthographic
  views keep their conventional alignment. The title block, notes, revision
  history, and BOM are laid out in reserved sheet regions. Text uses measured
  font advances and wraps instead of being truncated. Layout checks report
  view, text, margin, title, and schedule collisions or overflow.
- Sections and details are supported through
  `DrawingView(kind="section", plane="XY", offset=0)` and
  `DrawingView(kind="detail", parent="top", center=(x, y), radius=r, scale=2)`.
  Sections use OCCT plane intersections with hatching clipped to material;
  circular detail crops preserve circles and arcs. Sections currently support
  XY, XZ, and YZ planes; non-circular freeform cut curves are sampled polylines.
- Hole diameter callouts use arrowed leaders. Turned parts currently receive
  only honest measured bounding dimensions; general outside-diameter feature
  dimensions are not inferred yet. Tolerance classes and arbitrary
  curved-surface dimensioning remain future work.
- Units are represented in mm or in. Sheet sizes are physical ISO/ANSI sizes.
- Named views retain their conventional axes; custom direction vectors require
  a custom view name. Dimensions that cannot be displayed in the selected
  views fail the dimension-coverage check. Invalid explicit hole selections
  raise ValueError instead of silently choosing a different hole.
- Drawing text stays at least 2.5 mm high. If requested annotations cannot fit
  at that size, the sheet reports overflow rather than shrinking or dropping
  text.

## Layout samples

`examples/layout_samples.py` generates a dense mixed-hole plate, a stepped
turned shaft, and a two-part assembly with a child-derived BOM under
`samples/layout/`. The turned-part example intentionally reports only the
overall x/y/z bounds until a general diameter-feature dimension is available.
Dimension conventions use general drafting practice; the package does not
copy paid standards text or tables.

## License-conscious export stack

SVG is written directly. DXF uses ezdxf 1.4.5. PDF is drawn as vector paths and
text with ReportLab. The PNG is rendered from the same sheet primitives using
Pillow, so no SVG rasterizer or GPL dependency is required. CairoSVG and
PyMuPDF are not used.
