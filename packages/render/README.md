# aad-render

Owned by S3. Two jobs:

- `make_scene()`: tessellates a built model into the `tcv-5` scene the viewer loads (`CONTRACTS.md` section 7).
- `render()`: images for the agent itself (`CONTRACTS.md` section 9).

`make_scene(ctx, out)` writes `out/scene.bin.gz` and `out/scene.json` and returns the manifest. The payload is the
instance form three-cad-viewer 5.1.3 decodes, `{instances, shapes}`, with base64 typed buffers and `shapes.feature_map`
mapping each mesh face and edge to its feature. Mesh deviation scales with the part's size. The same shape gives the same
bytes.

Measured 2026-11-04 on this Mac, single process, geometry nothing has meshed yet, 15 runs, with other agents loading the
machine (load average about 4.5):

| Part | Faces | Triangles | Compressed | Median | p95 | SD |
|---|--:|--:|--:|--:|--:|--:|
| Bracket | 23 | 2,436 | 29 KB | 22–25 ms | 24–29 ms | 1–4 ms |
| Plate, 60 counterbores | 190 | 30,748 | 388 KB | 329–350 ms | 365–488 ms | 29–70 ms |
| Plate, 96 counterbores | 298 | 49,036 | 612 KB | 617–625 ms | 678–733 ms | 29–57 ms |

Ranges are two runs of the benchmark. The budget is 300 ms for at most 200 faces (`PLAN.md` section 9). A typical part is far
inside it. A hole-heavy part near the 200-face limit misses it by about 10–15% at the median under this load; an earlier run
of the same part measured 281 ms. Reproduce with
`uv run --locked --package aad-render --all-extras python packages/render/tests/perf/bench_scene.py --repeat 20`. Each run
uses geometry nothing has seen, because repeating a part measures OCCT's and ocp-tessellate's mesh caches. `make_scene`
clears ocp-tessellate's cache itself, so a reloaded revision gets the same bytes and feature map as the first build.

Both run inside kernel workers. `import aad_render` stays cheap; kernel and renderer imports happen on first use.

`render(ctx, req, out)` writes PNG images and `inspection.json` under an output directory and
returns all their paths. With labels it also returns `labels.json`. Callers must distinguish
PNG and JSON files. The `aad_render` MCP tool returns the report in `inspection`, the label
legend in `labels`, and paths in `artifacts`; only PNG files become image content blocks.
Install the optional `aad-render[vtk]` extra for the production backend. The context needs a
build123d shape, units, and optional feature metadata; both dictionaries and objects are accepted.
Requests accept the fields below and size (1–2048 pixels for Python, 64–2048 for MCP, default
1024). A sheet is one square image containing front, right, top,
and isometric panes. Below 64 pixels it uses a single isometric view because annotations and a
four-pane layout do not fit.

Representative output from `examples/agent_renders.py`: [dimensioned housing sheet](examples/images/housing-sheet.png),
[capped section](examples/images/housing-section.png), and [sampled wall thickness](examples/images/housing-thickness.png).

Images use fresh, pixel-scaled tessellation, 3x supersampling (2x at 2048), smooth surface
lighting, depth shading, curved silhouettes and separate crease/tangent/seam lines. Line style
includes lighter hidden edges. Orthographic sheet panes share one scale and show a world grid,
rulers and automatic overall/diameter callouts. Isometric views have a scale bar and axis triad.

```python
from pathlib import Path
from aad_render import render

paths = render(ctx, {
    "views": ["sheet", "iso"],
    "section": {"plane": "YZ", "offset": 0},
    "labels": "faces",
    "highlight": ["face:0"],
}, Path("job/renders"))
```

Labels refer to the source revision and are ephemeral. `labels.json` maps each displayed number
to its source face, edge, or feature selector. Section cuts preserve trimmed entities through
OCCT's boolean history. New cap faces and section edges have no source selector and are omitted
from the legend. A cut that removes everything produces an annotated empty image; an empty input
shape raises a clear `ValueError`.

For closer inspection, use these optional controls:

| Field | Meaning |
| --- | --- |
| `focus` | A selector, list of selectors, or `{selector, margin?, clip?}` / `{min: [x,y,z], max: [x,y,z], margin?, clip?}`. Fits the views and refines nearby faces. Default margin 0.25 plus 2% padding. `clip` hides geometry outside the padded box without adding cap faces. |
| `camera` | `{direction: [x,y,z], up?, target?, span?}`. Direction points toward the viewer; span is the vertical extent in model units. Produces `custom.png` when no views are specified; explicitly include `custom` to mix it with named views. |
| `dimensions` | `auto` (default: overall sizes and round features), `overall`, or `none`. Grid, rulers and scale indicators remain available. |
| `probes` | List of `{image: "top", x, y}` or `{point: [x,y,z], direction: [x,y,z]}`. Image names omit `.png`; pixel coordinates start at the whole image's top-left. Sheet probes find their pane automatically. |
| `measure` | List of `{a: selector, b: selector}`. Reports minimum distance, closest points, delta, and oriented angle when both entities have a fixed direction. Also accepts `point:(x,y,z)`. |
| `heatmap` | `thickness`, `overhang`, `draft`, or `curvature`. |
| `heatmap_range` | Fixed `[low, high]` color range: model units for wall thickness/bend radius, degrees for draft/overhang. |
| `min_wall` | Positive thickness threshold in model units. Samples below it, after a mesh-error allowance, are magenta. |
| `direction` | Build direction for overhang or pull direction for draft. Default `[0,0,1]`. |

`inspection.json` version 1 contains exact B-rep mass properties, bounding box, face surface
types and defining dimensions, edge curve types and adjacency, and exact section loops. Cylinder
facts distinguish inward-facing holes from outward-facing bosses. Numbers are rounded to nine
decimal places for repeatability; their accuracy remains subject to the model/kernel tolerance.
Each image lists pane rectangles, visible face selectors and pixel bounds, scale, labels actually
drawn, and a 2x4 `world_to_pixel` affine matrix: multiply `[x,y,z,1]` to obtain whole-image pixel
coordinates. This visibility describes the geometry under annotations, including the frontmost
surface for xray views.

Probes intersect the B-rep rather than the displayed mesh, returning a point, surface normal,
selector, local bend radius and normal-direction wall thickness when available. Section-cap
probes have no source selector or wall reading. Pixel probes honor focus clipping. Measurements
and probe answers are included in the report and marked in the image.

```python
paths = render(ctx, {
    "views": ["top"], "focus": "face:6", "size": 2048,
    "probes": [{"image": "top", "x": 1024, "y": 1024}],
    "measure": [{"a": "face:0", "b": "face:1"}],
}, Path("job/detail"))
```

Thickness uses deterministic inward mesh rays spread across the surface, with extra subdivision
on large flat faces. The smallest candidates are remeasured on the B-rep, with closest points
reported where available. Reports include sample spacing, per-face readings and the selected
minimum's method and location. This is a sampled search, not a certified global minimum; small
features below the sampling spacing can be missed. Grey means no reading. Section thickness
uses the uncut solid; artificial caps stay grey.

Overhang uses triangle angles from the build direction (0° upward, 90° vertical, 180° downward).
Draft uses the signed angle from a plane perpendicular to the pull direction (0° undrafted).
Curvature shows the smallest local bend radius from surface derivatives, on a logarithmic scale;
flat faces are grey. These visualizations do not constitute process checks or prove manufacturability.

PNG output contains no timestamps or variable metadata. Tests compare bytes across separate
processes for sheets, labels, sections, thickness and overhang on one machine. Byte equality
across operating systems or GPUs is not promised. Orthographic scale bars use model units.

On Linux without a display, install OSMesa (`libosmesa6` on Debian/Ubuntu). The renderer selects
`vtkOSOpenGLRenderWindow`, or raises an actionable error when OSMesa is absent. Explicit
`VTK_DEFAULT_OPENGL_WINDOW` overrides are respected. Linux image tests skip when no usable GL
context is available; a green run with skips does not prove headless rendering.

The 500 ms sheet target is not a guaranteed limit for this inspection pipeline. Two geometry
passes, fresh meshing, reports, labels and dense wall sampling add work as part complexity grows;
thickness maps can take seconds. Kernel/VTK import time is additional on a cold worker.

Reproduce samples and tests from the repository root:

```sh
uv run --locked --package aad-render --all-extras python packages/render/examples/agent_renders.py /tmp/aad-renders
uv run --locked --package aad-render --all-extras --with pytest --with pytest-timeout python -m pytest packages/render/tests
```

The backend interface and lazy registration live in `aad_render.backends`. Importing the
package never opens a human interface. `make_scene` remains a separate viewer integration;
it is not implemented by the agent rendering module.
