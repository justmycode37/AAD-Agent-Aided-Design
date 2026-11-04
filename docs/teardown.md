# S5 competitor teardown

This teardown exercises installed servers through real MCP stdio using fixed source, then audits fresh STEP exports outside each server. It is an integration experiment, not an agent benchmark. It does not estimate requirement pass rate, edit retention, tokens, wrong-tool rate or false-verified rate for agents. Those require independently checked agent runs with matched models and budgets.

## Shared tasks and oracle

The exact inputs and task statements are in [`evals/teardown/tasks`](../evals/teardown/tasks/README.md): a mounting plate, a width-and-spacing edit preserving its other dimensions, and a negative control whose counterbore approach is blocked by a bridge. All adapters receive the same authored source bytes. The edit is full-script replacement, so it measures neither semantic-selector retention nor imported-feature editing.

[`audit.py`](../evals/teardown/audit.py) freshly imports exported STEP, runs OCCT validity, counts solids, checks the envelope, samples the requested hole axes and diameter boundaries, and compares analytic plate volume. For the negative control it intersects a coaxial approach probe with the solid. The known obstruction disproves access; absence of intersection with a sampled probe would not certify all possible tool approaches. No competitor recognition result is used as this oracle.

The audit also samples the negative control's through bore, counterbore diameter and counterbore depth, and checks its analytic volume. The oracle was challenged with valid solids having incorrect width, spacing and diameter, plus a blocked part whose counterbore was missing. Reproduce from the repository root:

```sh
uv venv evals/teardown/.venv --python 3.12
uv pip install --python evals/teardown/.venv/bin/python build123d==0.13.0 cadquery-ocp-novtk==8.0.1.1.0
evals/teardown/.venv/bin/python evals/teardown/test_audit.py
```

Competitors keep their upstream kernel dependencies in isolated environments. The independent oracle uses AIAD's pinned kernel versions. Kernel versions differ between competitors, so these are native-stack observations, not controlled kernel performance comparisons. A matched agent experiment must record and disclose that variable.

## Installed versions and evidence

- build123d-mcp: `v0.3.91`, commit `48a7dba3a607ca4f1cab6a8ea798160fdb2c8162`. Its upstream lock resolves build123d `0.11.0`, OCCT `7.9.3.1`, MCP `2.0.0`. [Runner, commands and findings](../evals/teardown/build123d_mcp/findings.md), [retained tool results](../evals/teardown/build123d_mcp/results.json), [independent audit](../evals/teardown/build123d_mcp/audit.json).
- agentcad: `0.7.1`, commit `428abffa763c12882697de6449c59ea15c18c540`, build123d `0.10.0`, OCCT `7.8.1.1.post1`, MCP `1.30.0`. [Runner, pinned environment and findings](../evals/teardown/agentcad/findings.md), [retained tool results](../evals/teardown/agentcad/results.json), [independent audit](../evals/teardown/agentcad/audit.json).

The parent ran every retained MCP sequence and the independent STEP audit. Each task has three repeats per adapter; `summarize.py` gives the observed variance. Both adapters passed the plate and edit probes. All counterbore artifacts were structurally valid but failed the independent access probe. These deterministic observations are not estimates of agent performance.

Runners remove stale deliverables before each trial, invalidate old results/audits at run start, check the actual upstream Git commit and clean tracked source, and preserve raw replies under ignored `outputs/`. A complete manifest carries a run ID plus source and artifact hashes. The audit rejects mismatches; the summarizer requires a matching completed run and audit. A tool's transport success, a valid B-rep and fulfilled design requirements are distinct facts.

## What is better than our plan

**Written-file promotion.** build123d-mcp's `bank_candidate` writes a private STEP, reimports and gates that artifact, then atomically replaces the delivery path. A failure preserves the prior file. PLAN protects immutable revisions and the head; it should apply the same protection to delivered files. This behavior was source-reviewed; the shared tasks exercise successful promotion, not a failing replacement. [Pinned source](https://github.com/pzfreo/build123d-mcp/blob/48a7dba3a607ca4f1cab6a8ea798160fdb2c8162/src/build123d_mcp/tools/export.py).

**Evidence that tells the caller what was established.** Its experimental spec checker labels measured, structural, recognized and unverified evidence. An explicit point-occupancy requirement catches our obstruction. AIAD should support bounded point and local-thickness probes while stating their coordinate frame and coverage. [Pinned source](https://github.com/pzfreo/build123d-mcp/blob/48a7dba3a607ca4f1cab6a8ea798160fdb2c8162/src/build123d_mcp/tools/verify_spec.py).

**Bounded edit refusals and preservation checks.** Its plain-through-hole editor expires stale handles, refuses dependent counterbores/countersinks and checks protected geometry and expected volume change. This is source-reviewed behavior, not a result from the full-script edit exercise. AIAD's selector contract should be paired with explicit invariants and change-volume evidence for imported-part edits. [Pinned source](https://github.com/pzfreo/build123d-mcp/blob/48a7dba3a607ca4f1cab6a8ea798160fdb2c8162/src/build123d_mcp/tools/edit_features.py).

**Pinned check receipts and deferred acceptance.** AgentCAD binds candidate bytes, selected policy and check evidence to immutable receipts. Completing deferred checks does not itself advance the current model, and promotion accepts the saved revision with an idempotent operation. AIAD should bind every verification decision to revision/spec/kernel hashes and keep pending evidence out of accepted status. This lifecycle was source-reviewed, not exercised by the default run tasks. [Pinned change store](https://github.com/jdilla1277/agentcad/blob/428abffa763c12882697de6449c59ea15c18c540/src/agentcad/change_store.py).

**MCP output isolation.** AgentCAD duplicates the protocol file descriptor before native CAD or scripts can write to stdout. This is a concrete defense for AIAD's thin adapter/worker split; it was source-reviewed, not stress-tested here. [Pinned MCP server](https://github.com/jdilla1277/agentcad/blob/428abffa763c12882697de6449c59ea15c18c540/src/agentcad/mcp/server.py).

## What AIAD must avoid

build123d-mcp's structural gate passes the obstructed part. The size/hole spec passes, the separate obstruction-point spec fails, and the artifact can still be exported and banked. The bank is a structural safe floor, not proof that the separate intent spec passed. AgentCAD also passes its explicitly partial cylindrical-feature spec on the obstruction fixture; that checker did not assert access. Our negative control establishes this separation; it does not establish a competitor false-verified rate.

AIAD must persist request-derived requirements and bind check evidence to the exact revision/spec/kernel. Requirements inferred from an output must never become the independent acceptance oracle. Missing, unsupported, skipped or malformed error requirements must prevent promotion. A checker crash must not become an optional warning that permits verification.

The original wording allowed unknown requirements to `skip` while defining verification in terms of failing checks. S5 reported the coverage gap in [issue #5](https://github.com/justmycode37/AIAD-AI-Aided-Design-/issues/5); S2 closed it with explicit error-level coverage and draft rules in [PR #16](https://github.com/justmycode37/AIAD-AI-Aided-Design-/pull/16). Atomic STEP promotion and bounded edit evidence were sent to S1 in [issue #6](https://github.com/justmycode37/AIAD-AI-Aided-Design-/issues/6); check receipts and protocol stdout isolation went to S1/S2 in [issue #11](https://github.com/justmycode37/AIAD-AI-Aided-Design-/issues/11). S5 has made no contract changes.

## Reproduce retained observations

Follow each adapter's pinned install commands, rerun its MCP runner, then run the independent oracle:

```sh
evals/teardown/.venv/bin/python evals/teardown/audit.py evals/teardown/build123d_mcp/outputs/step --out evals/teardown/build123d_mcp/audit.json
evals/teardown/.venv/bin/python evals/teardown/audit.py evals/teardown/agentcad/outputs/step --out evals/teardown/agentcad/audit.json
python3 evals/teardown/summarize.py
```

`summarize.py` reports sample count, mean, sample standard deviation and range of the entire per-task MCP sequence. Sequences include startup, diagnostics and export/reimport, and contain different tool calls across tasks and adapters; they are not speed rankings. Repeated deterministic success is a reproducibility observation, not a confidence interval for agent quality. The required independent sampled false-verified audit has not been performed. No leaderboard submission has been made.
