# Drawings directly from Sheet2D

`make_drawing(ctx, request, out)` accepts `ctx.kind="2d"`, a live or serialized
`ctx.sheet`, and matching `ctx.units`. It draws the actual XY geometry without
inventing an extrusion or thickness. The default four 3D views become one plan
view. An explicit native 2D view request must be a single `top` view; extrude the
source sheet for 3D, section or detail projections.

Automatic dimensions measure the cut profile's X/Y extents, true circular
interior cutout diameters and their datum coordinates. A planar-feature table
defines rectangular, circular and obround contours by center, width, length
and long-axis angle. Table X/Y coordinates refer to the minimum cut bounds;
angles are degrees. Score/engrave line segments and circles also receive
measured table rows. Circle width and length are its diameter; a line has no
assigned physical width. Every numeric table cell has geometry provenance in
`DrawingResult.dimensions`. Inch automatic values normally use three decimal
places. Precision expands when needed to keep a nonzero dimension or tolerance
from printing as zero. The dimension record reports the effective precision.
Explicit dimensions retain the requested tolerance.

Reference geometry and annotation may extend beyond the cut profile. They
participate in packing, while dimensions and zero datums still refer to the
actual cut bounds. Source layer names survive in SVG/DXF. Circular arcs remain
analytic. General non-circular view curves follow the shared drawing projector's
sampled display path; raw native SVG/DXF exports preserve their exact source
curves separately.

The result includes native topology checks and a `2d.undimensioned_features`
check. Unrecognized automatic cut/score/engrave features fail dimension
coverage; they are not silently declared manufacturable. Explicitly requesting
no dimensions is supported. Optional laser limits remain unproven when absent.
`DrawingResult.ok` reports drawing/check success and never promotes a revision
to `verified`.

Text remains editable annotation at its actual scaled size. A drawing whose
source text would print below 2.5 mm raises a readable error rather than
enlarging the modeled text. Use a suitable sheet/scale or a separate detail.
Source annotation rotation is retained in SVG, DXF, PDF and the PNG preview.

Run `python packages/drafting/examples/planar_sheet.py` from an environment with
the package installed to regenerate the native laser-panel sample.
