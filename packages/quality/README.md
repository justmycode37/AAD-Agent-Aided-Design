# aiad-quality

Request checks and explicitly assumed process screens for AIAD. Public functions are `spec_checks(ctx)`, `process_checks(ctx)` and `library(action, **kwargs)`. Importing the package or querying the library does not import a geometry kernel.

Each requirement keeps its own ID and severity. Unsupported geometry or requirements remain unproven. The readiness helper in `aiad_quality.spec` enforces complete requirement coverage alongside the hard gate; a valid solid alone is insufficient. [Process evidence and limitations](PROCESS.md) describe finite manufacturing screens.

Call readiness with `verification_ready(ctx.spec, checks, hard_checks, process=ctx.process)` so a process set only on the project/context still requires evidence. This helper cannot attest checks from an interpreter that also executes model code.

Development uses Python 3.12 and the contractual geometry pins:

```sh
uv venv packages/quality/.venv --python 3.12
uv pip install --python packages/quality/.venv/bin/python -e packages/engine -e 'packages/quality[geometry]' pytest pytest-timeout
packages/quality/.venv/bin/python -m pytest packages/quality/tests -q
```

The CI package command uses the workspace lock:

```sh
uv run --locked --package aiad-quality --all-extras --with pytest --with pytest-timeout python -m pytest packages/quality/tests -q
```

Library search returns at most five short summaries; `get` retrieves one exact ID. IDs are never file paths or Python expressions:

```python
from aiad_quality import library

library("search", kind="playbook", query="cnc access")
library("get", kind="material", id="pla")
```

Kinds are `part`, `material`, `playbook`, and `api`. An unknown ID returns `found: false`. Material definitions name their exact source and limitations; `mass_g` compares a fully dense nominal estimate, not a measured manufactured mass. Source-backed density values do not establish density uncertainty or manufacturing accuracy.

The optional `parts` extra pins `bd_warehouse==0.4.0` with the contractual geometry stack. `aiad_quality.standard_parts.make_part(id, **params)` constructs selected socket-head screws, deep-groove bearings, or external thread helices inside a model worker. Construct outside an active Builder context, then add the prebuilt geometry explicitly. The screw uses full thread geometry united into one solid; a simplified minor-core screw would understate clearance. Catalog records list exact size choices, examples, fixed construction modes and limitations. Dimensions and outputs are in **mm**; convert geometry explicitly before using an inch-coordinate model. The adapter refuses a missing or mismatched backend version. A bearing and a thread form may contain multiple solids; a thread form contains no shaft. Treat them as nominal modeling references, not certified purchased hardware.

Playbooks, render-review rubric, server instructions, and tool descriptions are **unmeasured candidates**. The library does not install the descriptions as server defaults. The agent harness must test guidance/description ablations before any improvement claim or recommendation to keep the rubric. S2 can retrieve candidates through `aiad_quality.catalog.tool_description_candidates()` and `server_instruction_candidate()`.

Guidance lives under `skills/` in the checkout. The build hook includes it in the wheel and in a self-contained source distribution. Reproduce packaging checks with `uv build packages/quality --wheel --sdist --out-dir packages/quality/dist`; an installed wheel must support lookups outside this repository.

S1's contractual `Check` is a required dependency. There is no parallel quality-check dataclass. The worker owns sandboxing, hard-gate completion, revision identity and persistence. The quality package must not promote a revision by itself.
