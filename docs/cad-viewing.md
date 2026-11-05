# CAD inspection and curated chat views

The user sees small icon buttons beside the model title. Hover hints and
accessible labels name each action:

- **Pull apart / Put together** separates an assembly and zooms to fit the pieces, then brings it back together at the previous assembled zoom and framing.
- **Show grid / Hide grid** toggles the XYZ grids and axes. This choice stays in place across Views and Reset.
- **Views** opens the agent's named recipes: interiors, sections, components, cameras, or sourced analysis. Original restores the prepared starting view.
- **Reset** returns to the selected recipe's initial settings, apart from the grid.

The corner button makes the view bigger. The CAD toolbars, part tree, menus,
measurement panels, and their keyboard shortcuts are hidden in chat.
A model with one visible piece disables Pull apart.

Hover highlights the whole part in soft blue. Selection uses a stronger blue
tint while keeping its color, shading and edges visible. Click selects it; clicking it again clears that selection.
Shift-click toggles additional parts, and blank click or Escape clears selection.
Dragging still rotates.

On a host supporting native prompt insertion, double-click adds an unsent
`@part` reference to the real chat prompt. Current T3 HTML chat lacks that host
API, so this action stays unavailable there. No copy-and-paste workflow or separate
editor is shown, and nothing automatically sends. Users can describe edits in
normal chat.

Native references carry the exact project, revision, scene and part path. The
agent resolves that identity before editing. References retain their original
revision after the viewer changes; clearing selection does not remove a reference
already inserted into the user's prompt.

Opening a model, choosing a view, Reset, and resizing fit the visible component
bounds to the current canvas, with room around the edges and title controls.

| Input | Mac | Other desktops |
| --- | --- | --- |
| Rotate | Click-drag | Drag |
| Pan | Two-finger scroll, both directions | Shift-drag or right-drag |
| Zoom | Pinch; Option-scroll as a fallback | Scroll or pinch |

Pull apart spaces coaxial components along the assembly axis and separates
parallel stacks. Original geometry and part orientation do not change.
Auto-spacing is a view layout inferred from bounding boxes, not a physical
disassembly sequence or collision proof. Parts can still overlap in a projection.

The local CAD window retains the native engine's tree, face/edge visibility,
clipping and caps, measurement/properties, selection, cameras, exploded animation,
zebra, materials, studio lighting, and Z scale. Standalone exports can enable
this interface with `--controls cad`. Chat exports always stay simple.

Agents supply independent recipes in `aad_show.views`:

```json
{"project":"gearbox","views":[
  {"id":"interior","label":"Interior","presentation":{"hide":["housing_upper","housing_lower"]}},
  {"id":"see-through","label":"See-through","presentation":{"style":"transparent"}},
  {"id":"section","label":"Longitudinal section","view":"front","presentation":{"section":{"planes":[{"plane":"YZ","offset":0}]}}}
]}
```

See-through uses translucent surfaces with edge lines hidden, so overlapping
components stay readable. It keeps all original parts present.

Use actual `scene.parts` names. At most 24 recipes and 64 KiB of normalized
metadata are accepted. A recipe may include description, camera, presentation,
and analysis. The Views button is disabled when no additional recipes exist.
The former `presentation.inside` field remains a compatibility alias for one
Interior recipe; the dedicated Inside button is removed.

`presentation.materials` supports up to 256 per-part display overrides with `parts`, `color` (`#RRGGBB`), `metalness`, `roughness`, and `opacity` (numeric values 0–1). `presentation.environment: "studio"` enables an offline reflection environment. Display settings preserve original scene bytes.

Analysis colors whole components from sourced scalar values, with quantity,
units, range, and source in the legend. It does not run a solver or display a
pointwise finite-element field. Supply real results before offering Stress.

Users ask the agent for detailed views in ordinary language:

| Request | Agent command |
| --- | --- |
| “Show me the gears inside.” | `aad_show(project="gearbox", presentation={"hide":["housing_upper","housing_lower"]})` |
| “Only show this gear.” | `aad_show(project="gearbox", presentation={"isolate":["/Group/gear_1"]})` |
| “Cut through the middle.” | `aad_show(project="gearbox", presentation={"section":{"planes":[{"plane":"YZ","offset":0}]}})` |
| “Show it as wireframe from the front.” | `aad_show(project="gearbox", view="front", presentation={"style":"wireframe"})` |
| “Show the whole thing again.” | `aad_show(project="gearbox", presentation={})` |

Part selectors are exact names or full/group paths from `scene.parts`. Ambiguous
names and unknown paths return BAD_INPUT. The local CAD interface offers mesh
measurements; exact B-rep measurements through `aad_measure` depend on the
installed provider.

`aad_show.presentation` supports isolate/hide lists, shaded/edge/wireframe/
transparent styles, orthographic/perspective projection, one to three section
planes, an explode state, and a new scene's initial grid state. A section uses
either XY/XZ/YZ or a custom normal. Offsets are signed distances from the model
center in model units. Normals are normalized, offsets must be finite, and flipped reverses a
normal. The optional intersection flag selects the clipping engine's
intersection mode. Use `title` for a short, plain-language caption.

Each recipe starts from its own visibility/style/section settings, with explode
off unless explicitly enabled. Original restores the base presentation. Reset
restores the selected recipe.

A new scene takes its grid from its initial recipe, off when absent. After that,
Views, Original, Reset, and same-scene updates keep the user's exact XY/XZ/YZ
planes and axes, and ignore `grid` in those recipes.

Identical results are deduplicated; updated
recipes for the same scene do not transfer the mesh again. A replaced recipe
list keeps the selected ID when present, otherwise selects Original. Local
windows receive the same results through WebSocket updates.

The same CAD engine runs in MCP Apps, local windows, and HTML/offline exports.
HTML exports use lossless PNG scene chunks and verify chunk/total byte counts
and the scene SHA-256 before rendering; missing chunks show an error.
The original scene bytes are hash-verified and never simplified, rounded, or
retessellated. Hiding, clipping, camera choices, and exploded poses only affect
presentation.

Agent guidance lives in [the AAD viewing skill](../skills/aad-viewing/SKILL.md)
and [the complete capability/schema playbook](../skills/playbooks/cad-viewing.md).
MCP initialization instructions point all connected agents to that playbook,
which is also searchable through `aad_library`.

Run `npm ci`, `npm run build`, `npm test`, and `npm run test:e2e` in `viewer/`.
The build packages the single-file runtime in Python, so users need no Node
installation. Browser checks cover the simple controls and agent-driven views
in an opaque-origin sandbox, including strict CSP, mobile bounds, unchanged
scene transfers, large scenes, and retries.
