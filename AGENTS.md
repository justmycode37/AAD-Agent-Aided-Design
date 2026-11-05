# Agent guidance

Read `ARCHITECTURE.md` for how the repository fits together and `CONTRACTS.md` for repository interfaces and workflow.

To use AAD's tools, read `skills/aad/SKILL.md`. It must tell an agent everything AAD can do.

**Always keep that guide complete.** When you add, change or remove anything an agent or user can do with AAD, update `skills/aad/SKILL.md` in the same change. That includes tools and their arguments, modeling helpers, spec requirements, process settings, render and viewer options, drawing and export options, library entries, CLI commands and flags, and limits. Move an item out of "What does not work yet" when it starts working. `tests/e2e/test_guide_coverage.py` fails when a name is missing, but it cannot judge meaning, so check the wording too.

For AAD viewer or presentation work, read `skills/aad-viewing/SKILL.md` and the
capability/schema reference in `skills/playbooks/cad-viewing.md`.

Keep the full native CAD capabilities available in the local CAD viewer.
Chat users see the simple controls and agent-curated `aad_show.views`; do not
expose the CAD panels in chat. Preserve original geometry. Analysis views need
sourced values and a legend; display colors do not establish a stress result.

## CAD modeling standard

The user's default is realistic, complete assemblies. Unless the user explicitly
requests an exterior study or simplified concept, model the interior and the
interfaces that make the object function: cavities, component envelopes,
attachments, bearings and shafts, moving clearances, power and control paths,
and assembly/service access where applicable. Use continuous curved geometry
when the real part calls for it.

Inspect the assembly and check relevant interference and motion envelopes
before presenting it. Provide interior, section or isolated subsystem views so
the user can inspect those details. Adding visual detail does not prove that
a mechanism works: distinguish checked geometry, representative components,
and engineering that still needs evidence. Never call a model operational,
flightworthy or manufacture-ready from appearance or CAD validity alone.

Carry the requested model through build, review and presentation. Do not stop
at an exterior mockup when a realistic object was requested.

Use the canonical packaged AAD viewer. HTML-only chat uses `python -m aad_platform.viewer.bundle` on the saved AAD scene. Do not build a custom Three.js/WebGL viewer or use the legacy stub as a fallback; repair a missing/outdated viewer runtime. A failed geometry check may still be inspected as an unverified scene with the canonical viewer.
