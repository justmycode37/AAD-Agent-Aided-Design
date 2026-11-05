# Simple viewing, agent-controlled inspection

The user sees small icon buttons beside the model title. Hover hints and
accessible labels name each action:

- **Pull apart / Put together** separates an assembly and zooms to fit the pieces, then brings it back together at the previous assembled zoom and framing.
- **Show grid / Hide grid** toggles the XYZ grids and axes.
- **Show inside / Show outside** reveals an existing interior and restores the cover or outside section. This button appears only when an interior view is defined for the current selection.
- **Reset** returns to the view prepared by the agent.

The corner button makes the view bigger. Drag to turn, scroll or pinch to zoom,
and Shift-drag or right-drag to pan. The CAD toolbars, part tree, menus,
measurement panels, and their keyboard shortcuts are not exposed to the user.
A model with one visible piece disables Pull apart.

For an assembly with covers, the agent enables the optional inside button with
`presentation={"inside":{"hide":["housing_upper","housing_lower"]}}`.
The model initially stays complete. For a hollow part, use
`presentation={"inside":{"section":{"planes":[{"plane":"YZ"}]}}}`
to reveal its existing cavity. Omit `inside` for models without an interior;
the viewer does not guess that every solid is hollow. An inside toggle keeps
the agent's part selection, preserves the grid, and restores the outside camera
when toggled back. Reset returns to the prepared outside view.

Users ask the agent for detailed views in ordinary language:

| Request | Agent command |
| --- | --- |
| “Show me the gears inside.” | `aad_show(project="gearbox", presentation={"hide":["housing_upper","housing_lower"]})` |
| “Only show this gear.” | `aad_show(project="gearbox", presentation={"isolate":["/Group/gear_1"]})` |
| “Cut through the middle.” | `aad_show(project="gearbox", presentation={"section":{"planes":[{"plane":"YZ","offset":0}]}})` |
| “Show it as wireframe from the front.” | `aad_show(project="gearbox", view="front", presentation={"style":"wireframe"})` |
| “Show the whole thing again.” | `aad_show(project="gearbox", presentation={})` |

Part selectors are exact names or full/group paths from `scene.parts`. Ambiguous
names and unknown paths return BAD_INPUT. Measurements remain an agent task
through `aad_measure`; the user does not need to operate a measurement toolbar.

`aad_show.presentation` supports isolate/hide lists, shaded/edge/wireframe/
transparent styles, orthographic/perspective projection, one to three section
planes, and initial explode/grid states. A section uses either XY/XZ/YZ or a
custom normal. Offsets are signed distances from the model center in model
units. Normals are normalized, offsets must be finite, and flipped reverses a
normal. The optional intersection flag selects the clipping engine's
intersection mode. Use `title` for a short, plain-language caption.

Presentation settings replace the previous agent-controlled settings. Manual
explode/grid choices persist across agent updates unless explicitly set. Reset
keeps the requested part filter and section, so it never exposes parts the
agent was asked to hide. Identical results are deduplicated; a new presentation
of the same scene updates immediately without another mesh transfer. Open
local windows receive these changes and keep the requested view when reloaded.

The same UI runs in MCP Apps, the opt-in local window, and HTML/offline exports.
The original scene bytes are hash-verified and never simplified, rounded, or
retessellated. Hiding, clipping, camera choices, and exploded poses only affect
presentation.

Run `npm ci`, `npm run build`, `npm test`, and `npm run test:e2e` in `viewer/`.
The build packages the single-file runtime in Python, so users need no Node
installation. Browser checks cover the simple controls and agent-driven views
in an opaque-origin sandbox, including strict CSP, mobile bounds, unchanged
scene transfers, large scenes, and retries.
