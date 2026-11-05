---
name: aad-viewing
description: Prepare interactive AAD CAD views for users, choose view presets and inspection tools, and present sourced analysis results through the simple chat viewer or full local CAD viewer.
---

# AAD viewing

Use `aad_show` when the user asks to see a model. Preserve the original saved
scene; hiding, clipping, camera changes, colors, and exploded poses are display
settings, not modeling changes. Never simplify or round the display mesh to fit
a chat artifact.

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
    {"id":"section","label":"Longitudinal section","view":"front","presentation":{"section":{"planes":[{"plane":"YZ","offset":0}]}}}
  ]
}
```

Resolve part names/paths from the returned `scene.parts`; examples are not
universal names. Provide useful views for the actual model: interior only when
there is an interior, isolated subassemblies, selected sections, transparent or
wireframe inspection, and camera directions. The menu adds **Original**. Each
recipe is independent of the base presentation and other recipes; include all
settings it needs, except the grid. Updating recipes for the same scene does not
reload its mesh.

The XYZ grid and axes are a user preference. A new scene takes them from its
initial recipe. After that, Views, Reset, and same-scene `aad_show` updates keep
the user's exact grid planes and axes, even when a recipe sets `grid`. Do not tell
the user that a same-scene `grid` value changed the display; ask them to use Grid.

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

For the complete schema, capability table, precision limits, and HTML export
workflow, fetch `aad_library(action="get", kind="playbook",
id="viewer.presentation.v1")`. In this repository the same reference is
`skills/playbooks/cad-viewing.md`. Query available measurement/analysis providers
before claiming exact B-rep measurements or solver support.
