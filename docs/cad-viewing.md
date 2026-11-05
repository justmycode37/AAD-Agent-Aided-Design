# CAD viewing and inspection

AAD uses the pinned `three-cad-viewer` 5.1.3 renderer for the same local
inspection UI in an MCP App, the opt-in local window, and HTML exports. It
preserves the saved tessellation and verifies its compressed bytes against the
scene ID before rendering. Viewing controls never rebuild or edit the CAD model.

The inspection workflow follows common mechanical CAD practice, with
SolidWorks as the reference for sections, component visibility, display styles,
camera views, and exploded assemblies. This does not claim feature-for-feature
parity with every SolidWorks viewing or drawing feature.

| Control | Use |
| --- | --- |
| Section | Cut through the model center immediately. The Clip panel controls three independent planes, normal directions, offsets, caps, and intersection mode. Toggle Section off to restore the whole model. |
| Parts | Choose a named component and hide it, isolate it, or show all components. The navigation tree also controls faces and edges individually. For the gearbox, hide `housing_upper` and `housing_lower` to expose the gears. |
| Display | Choose shaded, shaded with edges, wireframe, or transparent surfaces. Hidden components remain hidden when switching styles. |
| View | Isometric, front, rear, left, right, top, or bottom. The orientation marker also changes the camera. |
| Orthographic | Switch between orthographic and perspective projection. |
| Explode | Separate assembly components immediately; the native animation controls adjust the exploded pose. Toggle off to reassemble. |
| Measure | Select faces, edges, or vertices to inspect distance. Measurements use the mesh and model units; they are not exact B-rep metrology. Measuring exits exploded mode. |
| Zebra / Material / Studio | Inspect surface reflections or change the presentation using local procedural lighting. Remote environment-map presets are excluded from the build. |
| Reset view | Restore all components, the camera, surface display, clipping planes, and inspection tools. |
| Fullscreen | Use the host's fullscreen API when available, only after a user click. |

Drag to orbit, Shift-drag or right-drag to pan, and scroll to zoom. These controls
are local to the viewer; they do not call modeling tools or send selections to
the agent.

Develop the viewer with `npm ci`, `npm run build`, `npm test`, and
`npm run test:e2e` in `viewer/`. The build copies its single-file output into the
Python package, so users need no Node installation. Browser tests run in an
opaque-origin sandbox using an in-memory reference MCP host, including strict
CSP, component visibility, sections, projections, exploded views, measurement
activation, mobile sizing, large scenes, and chunk retries.
