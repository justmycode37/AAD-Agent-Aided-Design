# aiad-render

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
`uv run --locked --package aiad-render --all-extras python packages/render/tests/perf/bench_scene.py --repeat 20`. Each run
uses geometry nothing has seen, because repeating a part measures OCCT's and ocp-tessellate's mesh caches. `make_scene`
clears ocp-tessellate's cache itself, so a reloaded revision gets the same bytes and feature map as the first build.

Both run inside kernel workers. `import aiad_render` stays cheap; kernel and renderer imports happen on first use.

`render(ctx, req, out)` writes PNG files under an output directory and returns their paths.
Install the optional `aiad-render[vtk]` extra for the production backend. The context needs a
build123d shape, units, and optional feature metadata; both dictionaries and objects are accepted.
Requests accept the fields in `CONTRACTS.md`: views, style, section, labels, heatmap, highlight,
and size (1–2048 pixels, default 1024). A sheet is one square image containing front, right, top,
and isometric panes. Below 64 pixels it uses a single isometric view because annotations and a
four-pane layout do not fit.

```python
from pathlib import Path
from aiad_render import render

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

Thickness is a mesh ray-casting heuristic: a low percentile of up to 16 inward rays per face,
requiring an opposite exit surface. Grey means no reading. In a section, thickness is measured
on the original solid; artificial caps stay grey. Overhang colors use each triangle's angle
from +Z (0° upward, 90° vertical, 180° downward), assuming build direction +Z. They do not
constitute a process check or prove manufacturability.

PNG output contains no timestamps or variable metadata. Tests compare bytes across separate
processes for sheets, labels, sections, thickness and overhang on one machine. Byte equality
across operating systems or GPUs is not promised. Orthographic scale bars use model units.

On Linux without a display, install OSMesa (`libosmesa6` on Debian/Ubuntu). The renderer selects
`vtkOSOpenGLRenderWindow`, or raises an actionable error when OSMesa is absent. Explicit
`VTK_DEFAULT_OPENGL_WINDOW` overrides are respected. Linux image tests skip when no usable GL
context is available; a green run with skips does not prove headless rendering.

Reproduce samples and tests from the repository root:

```sh
uv run --locked --package aiad-render --all-extras python packages/render/examples/agent_renders.py /tmp/aiad-renders
uv run --locked --package aiad-render --all-extras --with pytest --with pytest-timeout python -m pytest packages/render/tests
```

The backend interface and lazy registration live in `aiad_render.backends`. Importing the
package never opens a human interface. `make_scene` remains a separate viewer integration;
it is not implemented by the agent rendering module.
