# AgentCAD teardown

Pinned AgentCAD 0.7.1 at `428abffa763c12882697de6449c59ea15c18c540`. The competitor's dependency caps intentionally keep build123d 0.10.x and OCCT 7.8 together; AIAD pins are unchanged.

From this directory:

```sh
git clone https://github.com/jdilla1277/agentcad.git upstream
git -C upstream checkout 428abffa763c12882697de6449c59ea15c18c540
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt './upstream[mcp]'
.venv/bin/python reproduce.py --repetitions 3
../.venv/bin/python ../audit.py outputs/step --out audit.json
```

Resolved dependency versions and the exact commit are recorded in `results.json`. The runner checks the commit; `requirements.txt` pins the observed transitive dependency versions for this Mac. The pinned source is installed separately. Other operating systems may need different native wheels; no cross-platform install result is claimed.

The parent ran the MCP tool sequences. CLI `init --no-agent-setup` is setup only; builds, measurement, inspection, spec checking and edit comparison run through real MCP stdio. Preview, viewer and automatic diff are disabled for the core path; the edit performs an explicit diff. Each trial uses a new project directory. The common sources include the output hook that AgentCAD requires; no independent Python reference export is counted as an AgentCAD build.

## What to adopt

- Immutable check receipts bind candidate geometry, selected policy and check evidence by hashes. Staged candidates can complete deferred checks without becoming current; promotion accepts saved bytes and records an idempotent operation. This is a stronger, explicit acceptance lifecycle than the current PLAN's build/status description. **Source-reviewed, not exercised by our default run tasks.** [Pinned change store](https://github.com/jdilla1277/agentcad/blob/428abffa763c12882697de6449c59ea15c18c540/src/agentcad/change_store.py), [MCP promotion tool](https://github.com/jdilla1277/agentcad/blob/428abffa763c12882697de6449c59ea15c18c540/src/agentcad/mcp/server.py).
- Command success is separate from spec success: `check_spec` returns a `passed` field. AIAD should preserve the same distinction between successful execution and successful design checks. [Pinned checker](https://github.com/jdilla1277/agentcad/blob/428abffa763c12882697de6449c59ea15c18c540/src/agentcad/commands/check_spec.py).
- Source-frame shared/reference-only/candidate-only volumes and an explicit unavailable result help review edits without inferring success from two similar renders. The shared edit task calls `diff`; PLAN already requires shared/unique volume, so this reinforces the implementation priority rather than adding a novel requirement. [Pinned diff](https://github.com/jdilla1277/agentcad/blob/428abffa763c12882697de6449c59ea15c18c540/src/agentcad/commands/diff.py).
- Direct file-descriptor separation protects MCP stdout from native CAD code and generated-script output. **Source-reviewed, not stress-tested here.** [Pinned MCP entry point](https://github.com/jdilla1277/agentcad/blob/428abffa763c12882697de6449c59ea15c18c540/src/agentcad/mcp/server.py).

## What the task checks establish

The independent STEP audit evaluates the common task-specific geometric probes; the server's cylindrical-feature spec is intentionally partial. In particular, detecting the requested counterbore cylinders does not check the screw approach. A successful partial `check_spec` report on the obstruction fixture is not a claim of general manufacturability, and cannot be converted into a false-verified-rate estimate.

The edit is complete source replacement with version comparison; there is no evidence here for semantic selector retention. We did not run the browser viewer, render quality, staged promotion, cloud mode or a real model-driven agent loop. The evidence is local to the installed native stack on this Mac.
