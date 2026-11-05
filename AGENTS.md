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
