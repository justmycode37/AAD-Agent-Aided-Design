# HTML-only chat viewers

AAD's MCP App loads the original scene through `aad_scene_read` in 128 KiB
chunks. Keep this path when the host has the AAD MCP server connected.

For a chat host that only accepts HTML artifacts, export a bundle:

```sh
uv run python -m aad_platform.viewer.bundle \
  ~/AAD/mechanik-getriebe/.aad/revs/000003/scene \
  ~/AAD/mechanik-getriebe/exports/interactive
```

Pass the resulting `viewer.html` text to the host's HTML preview and display
tools. The host must inline local SVG assets (T3's `html_preview` and
`html_render` support this). Separate SVG metadata containers hold the complete
original `scene.bin.gz` bytes and the CAD viewer runtime. Nothing is drawn from
those SVGs: AAD unpacks the metadata and renders the original 3D scene in WebGL.
No mesh simplification, rounding, or retessellation occurs. The runtime uses
ordinary inline module scripts, without `eval` or external dependencies.

The exported HTML argument stays small while the host bundles the separate
asset into the final artifact. The viewer checks the original scene ID using
SHA-256 when Web Crypto is available, the byte count, and the triangle count.
It reports a missing asset instead of substituting a different mesh.

The same export also writes `standalone.html`, which opens directly from disk
with the viewer and original scene embedded. No daemon or internet connection
is needed. Chat exports use the simple MCP App interface, including a wider
Views menu of agent-provided recipes. `--controls cad` enables the full native
inspection interface in `standalone.html`; `viewer.html` remains simple for chat.

Offer an Interior recipe without editing the model:

```sh
uv run python -m aad_platform.viewer.bundle \
  ~/AAD/mechanik-getriebe/.aad/revs/000003/scene \
  ~/AAD/mechanik-getriebe/exports/inside \
  --views '[{"id":"interior","label":"Interior","presentation":{"hide":["housing_upper","housing_lower"]}}]' \
  --title 'Gearbox'
```

`--presentation` and `--views` accept the same settings as `aad_show`, including per-part `materials` and the offline `environment: "studio"`; see
[CAD viewing](cad-viewing.md). `--view` selects a standard camera direction.
Reset restores the selected recipe; Original restores the base presentation.

Allow content to determine the frame height. Do not set `height: 100%` on the
document roots: a host may add padding around the viewer. Pass the measured
height from the HTML preview to the display tool so that padding and the
bottom corners are included.
