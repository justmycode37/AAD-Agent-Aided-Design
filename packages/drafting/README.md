# aiad-drafting

The first S4 package creates a single, dimensioned drawing sheet from a
build123d shape. It writes SVG, R2013 DXF, vector PDF, and a PNG preview. The
public entry point is:

    from aiad_drafting import DrawingRequest, make_drawing

    result = make_drawing(ctx, DrawingRequest(), output_dir)

ctx is read structurally and should expose a build123d shape as shape.
units may be mm or in; no import from aiad.engine.types is needed.
Requests may also be dictionaries. DrawingResult.dimensions includes measured
values and their geometry sources.

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
- The common scale is selected from the requested sheet's usable drawing area.
  The sheet has a margin and a compact title block. This first release does not
  implement sections, details, revision tables, BOMs, tolerance classes, or
  automated collision resolution for arbitrary dense drawings.
- Units are represented in mm or in. Sheet sizes are physical ISO/ANSI sizes.
- Named views retain their conventional axes; custom direction vectors require
  a custom view name. Dimensions that cannot be displayed in the selected
  views fail the dimension-coverage check. Invalid explicit hole selections
  raise ValueError instead of silently choosing a different hole.

## License-conscious export stack

SVG is written directly. DXF uses ezdxf 1.4.5. PDF is drawn as vector paths and
text with ReportLab. The PNG is rendered from the same sheet primitives using
Pillow, so no SVG rasterizer or GPL dependency is required. CairoSVG and
PyMuPDF are not used.
