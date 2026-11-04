# Library validation

This package supplies on-demand content and a small part adapter. It has no real-agent comparison yet. The descriptions and review rubric remain unmeasured candidates; do not infer wrong-tool, quality, token, or edit-retention gains from these checks.

From the repository root, reproduce the package tests and repeated constructor measurements:

```sh
UV_PROJECT_ENVIRONMENT=packages/quality/.ci-venv uv run --locked --package aiad-quality --all-extras --with pytest --with pytest-timeout python -m pytest packages/quality/tests -q
UV_PROJECT_ENVIRONMENT=packages/quality/.workspace-venv uv run --locked --all-packages --all-extras python evals/library/probe_parts.py --repetitions 3
UV_PROJECT_ENVIRONMENT=packages/quality/.workspace-venv uv run --locked --all-packages --all-extras python evals/library/check_mcp_library.py
UV_PROJECT_ENVIRONMENT=packages/quality/.workspace-venv uv run --locked --all-packages --all-extras python evals/library/check_evidence.py
```

`parts-evidence.json` retains source versions/hashes, fresh BREP hashes, native OCCT validity/bounds, screw thread-zone bounds, bearing bore material probes, repetition counts, means and sample standard deviations. These are deterministic construction controls, not manufacturing certificates or sampled agent outcomes. Each default part was valid in every recorded repetition; per-axis extent sample SD was zero. That does not estimate model-quality or population variation.

`mcp-evidence.json` records actual SDK stdio calls through the adapter/daemon, with runtime/guidance hashes. It covers all catalog kinds, exact retrieval, missing IDs and invalid path IDs. It contains no model-token or timing estimate. Reproduction uses an isolated temporary daemon state and stops that daemon afterward.

Runs invalidate prior output at startup. Source changes prevent a completed MCP/constructor record. Fingerprints include upstream Python and CSV data tables and the validation code. `check_evidence.py` requires the exact three-trial matrix per part, canonical UUID run paths, strict boolean flags, current versions, and a complete recorded MCP call sequence. It reimports the local BREPs, recomputes native measurements and summaries, and rejects mismatches. Missing/changed artifacts, wrong retrieval IDs, duplicate JSON keys and non-finite values fail validation. Checks use explicit errors and also run under `python -O`. Raw BREP artifacts are ignored under `.runs/`; reproduce them before checking evidence from a fresh clone.

Package resources must also work outside the checkout:

```sh
uv build packages/quality --wheel --sdist --out-dir packages/quality/dist
uv build packages/quality/dist/aiad_quality-0.1.0.tar.gz --wheel --out-dir packages/quality/dist/from-sdist
```

The parent additionally installed the rebuilt wheel into a venv containing only the quality wheel and PyYAML, then called search/get for every kind repeatedly under `python -I`. Lookup and packaged candidate retrieval passed without importing a CAD kernel. Construction requires the optional pinned `parts` backend. Material and guidance sources are documented in the adjacent notes.

The separate [worker evidence controls](../quality/README.md) still block release when reported geometry disagrees with a fresh artifact audit. Library validation does not waive that blocker or estimate the headline false-verified rate.
