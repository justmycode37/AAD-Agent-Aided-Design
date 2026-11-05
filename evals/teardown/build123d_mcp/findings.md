# build123d-mcp teardown

Pinned `v0.3.91`, commit `48a7dba3a607ca4f1cab6a8ea798160fdb2c8162`. The upstream lock resolves build123d 0.11.0 and OCCT 7.9.3.1; these are isolated competitor dependencies, not changes to AAD's pins. The parent reran `reproduce.py --repetitions 3` and independently audited exported STEP with `../audit.py`.

## Reproduce

From this directory:

```sh
git clone https://github.com/pzfreo/build123d-mcp.git upstream
git -C upstream checkout 48a7dba3a607ca4f1cab6a8ea798160fdb2c8162
UV_CACHE_DIR="$PWD/.cache" uv sync --project upstream --python 3.12 --locked --no-dev
upstream/.venv/bin/python reproduce.py --repetitions 3
upstream/.venv/bin/python ../audit.py outputs/step --out audit.json
```

The runner uses real MCP stdio. Raw replies and exports are regenerated under ignored `outputs/`; compact retained results are in `results.json`. Inventory probes use default mode and experimental mode; the task calls use experimental mode because `verify_spec` requires it. A new MCP process is started for each trial. Task timings include startup, multiple diagnostic calls and export/reimport, and differ by task call sequence; they are not geometry build time or agent latency. Compute means and sample standard deviations with `../summarize.py`.

## What to adopt

- Atomic promotion of a *written and reimported* STEP, preserving an existing destination when the gate fails. PLAN's revision preservation covers source/head safety but should also explicitly cover delivered files. Source: [bank_candidate](https://github.com/pzfreo/build123d-mcp/blob/48a7dba3a607ca4f1cab6a8ea798160fdb2c8162/src/build123d_mcp/tools/export.py).
- Evidence tiers distinguish measured dimensions, structural validity and recognized features. A caller-authored `material_at_point` check correctly catches the blocked approach. This is useful bounded evidence; it does not prove general screw access. Source: [verify_spec](https://github.com/pzfreo/build123d-mcp/blob/48a7dba3a607ca4f1cab6a8ea798160fdb2c8162/src/build123d_mcp/tools/verify_spec.py).
- Refuse unsupported bounded edits and expire stale recognition handles. The source restricts automated resizing to plain through holes, checks protected holes and compares predicted/measured material change. This behavior was source-reviewed, **not exercised by the shared full-script edit task**. Source: [edit_feature](https://github.com/pzfreo/build123d-mcp/blob/48a7dba3a607ca4f1cab6a8ea798160fdb2c8162/src/build123d_mcp/tools/edit_features.py).
- A spec inferred from the current output is explicitly described as a starter, not ground truth. AAD should preserve request-derived requirements across edits and keep inference out of its acceptance oracle.

## Limits demonstrated

The plate and full-script edit exports passed the independent task probes. Both the negative-control solid and its STEP round-trip pass structural validity. `verify_spec` passes a partial size/hole specification, then fails the explicit obstruction-point specification; export and `bank_candidate` still succeed. Banking here certifies the written STEP's structural gate, not the separate intent checks. AAD must connect its persisted requirements to promotion instead of treating these independent tool results as equivalent.

The width edit uses `execute_file` replacement plus named snapshots and comparison. It does not measure semantic selector retention or safe feature editing. No agent was sampled, so false-verified rate, agent tokens, wrong-tool rate and model quality are **not estimated** from these deterministic exercises.
