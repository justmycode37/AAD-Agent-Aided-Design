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
tools. The host must inline local images by absolute path (T3's `html_preview`
and `html_render` support this). The scene is stored in lossless RGB PNG chunks;
the CAD viewer runtime is carried in a separate SVG asset. Nothing is drawn
from those assets: AAD restores the original scene bytes and renders the scene
in WebGL. No mesh simplification, rounding, or retessellation occurs. The
runtime uses inline scripts, without `eval` or external dependencies.

The exported `viewer.html` stays under T3's 512,000-character
`html_preview`/`html_render` argument cap. It references each PNG chunk once by
absolute local path; those image tags are inlined by the host. Do not put chunk
data URIs in `viewer.html`. Exports refuse oversized manifests or HTML arguments
and point to the generated `standalone.html`.

The viewer checks the original scene ID using
SHA-256 when Web Crypto is available, the byte count, and the triangle count.
It reports a missing asset instead of substituting a different mesh.

Scene payloads are split into lossless RGB PNG chunks. This keeps the bytes
close to 4/3 of the scene size when an HTML host inlines each image, instead of
nesting base64 text inside SVG and then encoding that SVG again. The viewer
requires every chunk, verifies each chunk's byte count and the complete scene's
byte count and SHA-256 against `scene.json`, then reassembles the original bytes.
A missing or altered chunk produces a visible load error; the viewer never
substitutes geometry. This avoids relying on one oversized SVG data URL.

Inline artifact limits are host-specific. The current T3 `html_preview` ceiling
is 25 MiB after local images are inlined; export estimates the inlined size and
refuses to write `viewer.html` when the estimate exceeds that ceiling. It still
writes `standalone.html` and reports its path so the user can open it locally
or use a host with a larger limit. This 25 MiB figure is not a general
HTML-host limit.

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

For a requested automatic browser fallback, add `--open-on-limit`. When either
chat limit is exceeded, export opens the complete standalone page in Safari on
macOS (the default browser elsewhere, or when Safari cannot open it) and
returns that file path. Safari is launched with `open -a Safari`, so no macOS
Automation permission is needed. It opens no
browser for a scene that fits. Without this opt-in, export continues to report
the limit as an error. A stale `viewer.html` is removed on oversized export.

Standalone browser pages automatically size the canvas to the available browser
window height and refit on resize, using the same viewer runtime and controls.
Embedded chat keeps its compact height. This uses the browser viewport without
entering operating-system fullscreen; the fullscreen button still requests or
exits actual browser fullscreen and reports that state.
