---
name: aad-viewing
description: Prepare interactive AAD CAD views for users, choose view presets and inspection tools, and present sourced analysis results through the simple chat viewer or full local CAD viewer.
---

# AAD viewing

Use `aad_show` when the user asks to see a model. Preserve the original saved
scene; hiding, clipping, camera changes, colors, and exploded poses are display
settings, not modeling changes. Never simplify or round the display mesh to fit
a chat artifact.

## Use the canonical viewer

Use AAD's packaged viewer for every CAD model. For HTML-only chat, export the
saved AAD scene with `python -m aad_platform.viewer.bundle SCENE_DIR OUTPUT_DIR`
and pass `viewer.html` to the host's HTML preview and display tools. Use its
`--title`, `--presentation` and `--views` options for inspection recipes. This
also works for an explicitly unverified inspection scene; it does not require
replacing the renderer when a build check fails.

Do not write a separate Three.js/WebGL viewer or copy an old stub to work around
missing MCP tools, a failed build, or HTML size limits. Unless the user explicitly
requests a different viewer, preserve AAD's controls, navigation and original
scene geometry. If the runtime is missing, repair the installation or report
the actual blocker.

When a viewer looks outdated, inspect the active Python environment and the
resolved asset before editing UI: `python -c "import sys; from aad_platform.viewer import viewer_path; print(sys.executable); print(viewer_path())"` (one line).
An old editable checkout is a separate runtime from another checkout containing
new commits. Merging a PR does not update that environment or saved HTML files.
Regenerate the viewer bundle with the current runtime after updating it.

Know what the user sees:

- **Chat:** a model title, small white-outlined Pull apart, Grid, and Reset icons,
  a wider **Views** button, fullscreen, and orbit/pan/zoom. CAD panels and action
  shortcuts stay hidden. Pull apart fits the separated pieces; Put together
  restores assembled framing. Reset restores the selected view recipe, except
  the grid. Opening a scene, choosing a view, Reset, and resizing fit the model
  to the current canvas with space around its edges and title controls.
- **Navigation:** on Mac, click-drag rotates, two-finger scroll pans in both
  directions, pinch zooms, and Option-scroll is the zoom fallback. Other desktops
  scroll or pinch to zoom and Shift-drag or right-drag to pan.
A slim loading bar appears during startup and model loading. It tracks received
bytes, stays active during geometry preparation, and disappears when ready.
There is no visible loading text or percentage; reduced motion is respected.

Grid planes, origin and extent stay anchored in world coordinates while the
camera moves. Only tick density changes with zoom, with hysteresis to prevent
flicker near thresholds. Labels retain their native axis orientation; overlapping
or offscreen labels are hidden, reserving space for the title and XYZ marker.

Hovering a part shows a muted `Part name · #number` line beneath the title.
The one-based number follows the original scene manifest, stays fixed across
views, and is revision-local. The label clears when hovering ends.

- **Parts:** hover highlights the whole part with a soft blue tint; selection
  uses a stronger blue tint while retaining material and detail. Click selects; click the selected part again to deselect.
  Shift-click toggles additional parts; blank click or Escape clears selection.
  Dragging rotates without selecting. Hosts advertising native unsent composer
  insertion also support double-clicking a part to add an `@part` reference to
  the user's real chat prompt. Current T3 HTML chat lacks that host API, so this
  action stays unavailable there. There is no clipboard workflow, separate request
  editor, or automatic sending. The user can still describe edits in normal chat.
- **Local CAD window / CAD standalone export:** native part tree, face/edge
  visibility, sections and caps, projections and standard cameras, selection,
  mesh measurements/properties, exploded animation, zebra inspection, material,
  studio lighting, and Z scale. These capabilities remain available even though
  chat does not expose their panels. This is the pinned CAD engine's capability
  set, not a claim of parity with every commercial CAD application.

Give chat users choices with `aad_show.views`, not a dedicated Inside button:

```json
{
  "project": "gearbox",
  "title": "Gearbox",
  "views": [
    {"id":"interior","label":"Interior","presentation":{"hide":["housing_upper","housing_lower"]}},
    {"id":"see-through","label":"See-through","presentation":{"style":"transparent"}},
    {"id":"section","label":"Longitudinal section","view":"front","presentation":{"section":{"planes":[{"plane":"YZ","offset":0}]}}}
  ]
}
```

Resolve part names/paths from the returned `scene.parts`; examples are not
universal names. Provide useful views for the actual model: interior only when
there is an interior, isolated subassemblies, selected sections, See-through
(`style: "transparent"`, translucent surfaces without edge lines), or
wireframe inspection, and camera directions. The menu adds **Original**. Each
recipe is independent of the base presentation and other recipes; include all
settings it needs, except the grid. Updating recipes for the same scene does not
reload its mesh.

The XYZ grid and axes are a user preference. A new scene takes them from its
initial recipe. After that, Views, Reset, and same-scene `aad_show` updates keep
the user's exact grid planes and axes, even when a recipe sets `grid`. Do not tell
the user that a same-scene `grid` value changed the display; ask them to use Grid.

Native prompt references and negotiated selection context identify parts using
`aad://part/<project>/<rev>/<scene_id>?path=<encoded-path>`. Resolve this against
that revision's `scene.parts` and scene hash before editing. The `@name` is a
display label, not a file or a globally unique selector. Never pass the identity
URI as a modeling query; resolve the actual part and its modeling selectors first.
A newer revision needs reconciliation, even if a part has the same name. MCP
hosts may stage selection context with `selectedCount`/`omittedCount`; obtain
omitted references before acting on them. A reference already inserted into a
prompt retains its original revision. Clearing selection does not remove it from
the user's prompt.

Exploded layout spaces coaxial components along the assembly axis and separates
parallel stacks without changing original geometry or part orientation. Its
auto-spacing is a view layout inferred from bounding boxes, not a physical
disassembly sequence or collision proof. Parts can still overlap in a projection.

An analysis view requires real supplied or computed results. `analysis` colors
whole components from numeric part values and displays quantity, units, range,
and source. It is not a solver or a spatial finite-element field. Do not invent
stress values, label arbitrary colors as stress, or imply that an unavailable
analysis ran. State missing loads, constraints, material, or results when they
prevent a requested analysis view; provide the supported geometric views.

`presentation.materials` accepts up to 256 `{parts, color, metalness, roughness, opacity}` overrides. Color is `#RRGGBB`; scalar values are from 0 to 1. `presentation.environment: "studio"` adds an offline generated reflection environment. Both affect display only and preserve scene bytes.
HTML-only exports use lossless PNG scene chunks. The viewer verifies chunk and total byte counts and the scene SHA-256, and reports missing chunks as errors. The 25 MiB T3 `html_preview` limit is host-specific; oversized exports leave `standalone.html` and report its path.

For the complete schema, capability table, precision limits, and HTML export
workflow, fetch `aad_library(action="get", kind="playbook",
id="viewer.presentation.v1")`. In this repository the same reference is
`skills/playbooks/cad-viewing.md`. Query available measurement/analysis providers
before claiming exact B-rep measurements or solver support.
