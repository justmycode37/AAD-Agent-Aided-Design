# Guidance library implementation notes

Status: unmeasured candidate content. No real-agent harness has tested wrong-tool rate, task tokens, turns, requirement pass rate, or whether the rubric changes outcomes. Do not claim tuning or quality improvement.

## Contents

Seventeen short playbooks are proposed under skills/playbooks. Each has stable ID, title, searchable tags, and candidate status in frontmatter. The render-review rubric is isolated in render-review-rubric.md so it can be evaluated on its own and removed if an ablation shows no benefit. Server instructions are a separate approximately 300-token candidate. Tool descriptions are separate JSON data; all 13 model-visible and 4 app-only tools are represented.

## Current-tree facts checked before writing

- packages/engine/src/aiad/__init__.py lazily re-exports build123d names and exposes params() as its only AIAD modeling helper. The worker extracts a result Shape or BuildPart output and records one implicit main feature. Planned AIAD part(), feature(), named semantic selector APIs, Param ranges, and assembly helpers are not present.
- packages/engine/README.md states kind=2d is not implemented. Current hard checks cover B-rep validity and at least one solid, then emit an error-level hard_gate_incomplete skip; the worker does not trust a model-process verified claim.
- The contract-shaped query and measurement tools are registered, but the engine tree lacks aiad.engine.select and aiad.engine.measure providers. The parent integrated aiad_quality.library with bounded search/exact-ID retrieval, sourced materials, standard-part records, and packaged guidance. Standard-part construction is separate and requires the pinned optional parts backend.
- packages/quality/SPEC.md lists current evidence and caveats. bbox and solids use native B-rep; holes are conservative and do not prove access; clearance uses a project-contained STEP; material_at_point proves one point. min_wall and symmetry can produce failure witnesses but withhold unsupported passes, mass_g estimates from exact sourced catalog IDs under a fully dense B-rep assumption, and custom measure needs the missing selector/measure provider.
- packages/quality/PROCESS.md and process_profiles.py define six process profiles. Every result is heuristic with assumptions. The default vectors, angles, finite sampling caps, and probe dimensions are evaluator settings, not universal manufacturing rules.
- monid discover ran before web retrieval and returned unrelated endpoints. Playbooks cite the official build123d project docs and primary manufacturer/provider design guides; numeric supplier rules are not promoted as universal limits.

## Validation performed

- JSON parsing passed. The candidate description names match packages/platform/src/aiad_platform/tools.py: 13 model-visible and 4 app-only; every description is marked unmeasured, and all app-only descriptions begin with the contract-required prefix.
- PyYAML parsed all 17 playbook frontmatter blocks. Each has a non-empty ID, title, tags, and candidate status; all 17 IDs are unique.
- All 6 process guides explicitly identify a heuristic screen and say what it does not prove.
- The exact indented example in core-workflow.md ran under Python 3.12.10 with build123d 0.13.0 and cadquery-ocp-novtk 8.0.1.1.0. Result: valid=True, 1 solid, 10 faces, volume=9356.715065 model-units³.
- Server instructions contain 227 whitespace-separated words, roughly the requested 300-token size; tokenizer count was not measured.

No package test suite, real-agent harness, wrong-tool-rate evaluation, quality comparison, or performance benchmark was run. The example validates only that the current pinned geometry environment can execute this model; it does not test MCP orchestration or manufacturing suitability. At final validation, concurrent quality material and standard-part modules appeared in other workers' paths; they were read-only inputs and were not edited here.

Parent integration corrected availability wording after the catalog loader was implemented, and incorporated the newer evaluator semantics from PR #34. Reproduce current guidance metadata and executable-snippet checks through packages/quality/tests/test_library.py. The server-instruction count above describes the delegate draft; the edited candidate must be token-counted by the eventual matched-model harness. No outcome improvement has been measured.
