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
`html_render` support this). The HTML references a small SVG container whose
metadata holds the complete original `scene.bin.gz` bytes. Nothing is drawn
from that SVG: AAD decompresses its metadata and renders the original 3D scene
in WebGL. No mesh simplification, rounding, or retessellation occurs.

The exported HTML argument stays small while the host bundles the separate
asset into the final artifact. The viewer checks the original scene ID using
SHA-256 when Web Crypto is available, the byte count, and the triangle count.
It reports a missing asset instead of substituting a different mesh.

Allow content to determine the frame height. Do not set `height: 100%` on the
document roots: a host may add padding around the viewer. Pass the measured
height from the HTML preview to the display tool so that padding and the
bottom corners are included.
