# CAD inspection and curated chat views

The user sees small icon buttons beside the model title. Hover hints and
accessible labels name each action:

- **Pull apart / Put together** separates an assembly and zooms to fit the pieces, then brings it back together at the previous assembled zoom and framing.
- **Show grid / Hide grid** toggles the XYZ grids and axes.
- **Views** opens the agent's named recipes: interiors, sections, components, cameras, or sourced analysis. Original restores the prepared starting view.
- **Reset** returns to the selected recipe's initial settings.

The corner button makes the view bigger. Drag to turn, scroll or pinch to zoom,
and Shift-drag or right-drag to pan. The CAD toolbars, part tree, menus,
measurement panels, and their keyboard shortcuts are hidden in chat.
A model with one visible piece disables Pull apart.

The local CAD window retains the native engine's tree, face/edge visibility,
clipping and caps, measurement/properties, selection, cameras, exploded animation,
zebra, materials, studio lighting, and Z scale. Standalone exports can enable
this interface with `--controls cad`. Chat exports always stay simple.

Agents supply independent recipes in `aad_show.views`:

```json
{"project":"gearbox","views":[
  {"id":"interior","label":"Interior","presentation":{"hide":["housing_upper","housing_lower"]}},
  {"id":"section","label":"Longitudinal section","view":"front","presentation":{"section":{"planes":[{"plane":"YZ","offset":0}]}}}
]}
```

Use actual `scene.parts` names. At most 24 recipes and 64 KiB of normalized
metadata are accepted. A recipe may include description, camera, presentation,
and analysis. The Views button is disabled when no additional recipes exist.
The former `presentation.inside` field remains a compatibility alias for one
Interior recipe; the dedicated Inside button is removed.

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
planes, and initial explode/grid states. A section uses either XY/XZ/YZ or a
custom normal. Offsets are signed distances from the model center in model
units. Normals are normalized, offsets must be finite, and flipped reverses a
normal. The optional intersection flag selects the clipping engine's
intersection mode. Use `title` for a short, plain-language caption.

Each recipe starts from its own visibility/style/section settings, with grid and
explode off unless explicitly enabled. Original restores the base presentation.
Reset restores the selected recipe. Identical results are deduplicated; updated
recipes for the same scene do not transfer the mesh again. A replaced recipe
list keeps the selected ID when present, otherwise selects Original. Local
windows receive the same results through WebSocket updates.

The same CAD engine runs in MCP Apps, local windows, and HTML/offline exports.
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
