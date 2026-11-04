# aiad-quality

Request checks and explicitly assumed process screens for AIAD. Public functions are `spec_checks(ctx)`, `process_checks(ctx)` and, when the catalog package is supplied, `library(action, **kwargs)`. Importing the package does not import a geometry kernel.

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

S1's contractual `Check` is a required dependency. There is no parallel quality-check dataclass. The worker owns sandboxing, hard-gate completion, revision identity and persistence. The quality package must not promote a revision by itself.
