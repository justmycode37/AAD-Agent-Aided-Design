---
id: viewer.presentation.v1
title: CAD viewing, user views, and analysis presentation
tags: [viewer, CAD, views, interior, section, stress, analysis, display, inspection]
candidate_status: unmeasured
---

# CAD viewing and user-facing views

Unmeasured candidate agent guidance. The implementation is tested; this wording
has not been evaluated for agent tool-choice efficiency.

## Two interfaces, one original scene

Chat exposes orbit/pan/zoom, Pull apart/Put together, XYZ Grid, Reset, a wider
Views menu, and fullscreen. Small white-outlined icons sit beside the title.
Advanced CAD panels and action shortcuts are hidden. Local windows retain the
native CAD engine's full toolset; standalone exports can opt into CAD controls.

| Capability | How agents prepare chat views / local CAD behavior |
| --- | --- |
| Components | `presentation.isolate` or `hide`; exact names and leaf/group paths from `scene.parts`. Native tree also controls faces/edges and selection. |
| Sections | `section.planes`: 1–3 XY/XZ/YZ or custom-normal planes, signed offsets from the model center in model units, optional flipped normals and intersection mode. Native Clip UI also exposes caps and plane controls. |
| Display | `style`: shaded-edges, shaded, wireframe, transparent. Native materials and edges remain available locally. |
| Camera | `view`: iso/front/rear/left/right/top/bottom; `projection`: orthographic/perspective. Native orientation marker, orbit, pan, and zoom. |
| Explode/grid | Optional `explode` and `grid` booleans in each recipe. User toggles remain simple. Explode fits the pieces; collapse restores assembled framing. |
| Measurement | Native mesh distance, properties, and picking in the local CAD window. Mesh measurements are not exact B-rep metrology. `aad_measure` may be unavailable; inspect its result. |
| Surface inspection | Native zebra, material, studio lighting, and Z scale in CAD mode. Remote environment-map fetches are excluded by the offline viewer contract. |
| Analysis | Named `analysis` views color whole components using supplied scalar summaries. A legend shows quantity, units, range, and source. No solver is provided by this viewer. |

Do not promise feature parity with all CAD software. Geometry editing and exact
CAD analysis remain separate from this display viewer.

## Provide a menu of views

Pass `views` to `aad_show`, alongside the reviewed revision and optional base
`presentation`, `view`, and `title`:

```json
{
  "project":"gearbox",
  "title":"Gearbox",
  "views":[
    {"id":"interior","label":"Interior","description":"Housing hidden","presentation":{"hide":["housing_upper","housing_lower"]}},
    {"id":"gears","label":"Gear train","presentation":{"isolate":["/Group/gears"]}},
    {"id":"section","label":"Longitudinal section","view":"front","presentation":{"section":{"planes":[{"plane":"YZ","offset":0}]},"projection":"orthographic"}}
  ]
}
```

Use actual part names from `scene.parts`, not the example names. Each recipe is
complete and independent: absent visibility/style/section fields use the normal
whole-model display. Recipes do not inherit base filters. Original restores the
base presentation; Reset restores the currently selected recipe. Repeating an
identical result preserves the selection; replacing recipes preserves its ID
when present and otherwise returns to Original. `views: []` clears the menu.
The same scene hash is reused without fetching or retessellating its geometry.

At most 24 recipes and 64 KiB of normalized recipe metadata are accepted. IDs
are unique `[A-Za-z0-9_-]` strings up to 64 characters; `original` is reserved.
Labels are unique, non-empty, at most 48 characters. Optional descriptions are
at most 240 characters. `presentation.inside` is a legacy alias for one Interior
recipe; new integrations use `views`.

## Analysis views: evidence before color

Add `analysis` only when actual result data exists:

- `quantity`: what the numeric values mean, e.g. Maximum von Mises stress.
- `units`: the units of those values, e.g. MPa.
- `source`: the actual result artifact or solver run, not a made-up citation.
- `values`: 1–2048 `{part, value}` records, resolved to unique scene part paths.
  Values must be finite; no part may be assigned twice.
- Optional `range: [minimum, maximum]` must contain every value. When omitted,
  bounds come from the supplied values.

Each component is uniformly colored by its supplied summary value. This is not
a pointwise stress field, displacement solution, or an FEA solver. Do not use
invented values to make a requested Stress menu entry appear. When results are
missing, say what is unavailable and offer supported geometric views. Keep
solver assumptions, load cases, boundary conditions, material evidence, and
revision association attached to the analysis source.

## Exports

MCP Apps use the original scene chunks. For HTML-only chat, export with
`python -m aad_platform.viewer.bundle SCENE_DIR OUTPUT_DIR --views 'JSON'`.
Pass `viewer.html` to the host's HTML preview then display tools; supported
hosts inline its local SVG metadata assets losslessly. `standalone.html` works
offline; `--controls cad` enables native CAD controls only in that standalone
file. The chat artifact always stays simple. No preview server is needed.
Preserve original scene bytes, hash and triangle count. Never use mesh
simplification or coordinate rounding to get under a host's HTML size limit.
