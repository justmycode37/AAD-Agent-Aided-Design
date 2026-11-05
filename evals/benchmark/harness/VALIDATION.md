# Pilot validation

The pilot's local controls are deterministic tests, not CAD-quality samples. The offline end-to-end command uses a scripted provider and real local AAD MCP stdio and daemon. It makes zero provider API requests. No real-agent result or release conclusion is reported.

## End-to-end offline control

From the repository root, use a new output directory:

```sh
uv run --offline --project packages/platform python -m evals.benchmark.pilot \
  --offline-smoke \
  --output-dir /tmp/aad-pilot-smoke
```

The runner validates the frozen selection and public projection, performs host isolation probes, verifies the MCP tool catalog, and then runs all selected tasks. Each task has a fresh host home and daemon. Agent-issued CAD calls pass through the real MCP stdio session; tool metadata, traces, and native artifacts are saved outside the solver-writable tree. The solver worker receives the engine's fixed environment allowlist and filesystem/network sandbox. The host kills timed-out audit workers and confirms daemon teardown before starting the next task.

Each saved artifact is checked in a fresh `python -I -B` process with explicit host-owned paths, a 90-second timeout, a 256 MiB per-file limit, and bounded request/response size. The audit process imports geometry but never executes model source or trusts AAD-returned measurements. `aad_import` is excluded from the model tool list. Unsupported, missing, malformed, and bounded evidence cannot become passes.

The smoke's scripted token receipts are synthetic. Its responses and geometry deliberately do not stand in for a real agent. Do not use its metrics as a model or backend-quality result.

The offline control run for this change completed 20/20 scripted task loops and 40 local MCP tool calls, stopped all 20 task daemons, and had zero audit-process errors and zero provider API requests. These are transport and accounting checks only.

## Focused offline tests

The pilot harness, original-request mutants, and independent audit controls are covered by:

```sh
uv run --offline --project packages/platform --with pytest==9.2.0 python -m pytest \
  -p no:cacheprovider \
  evals/benchmark/tests/test_agent_loop.py \
  evals/benchmark/tests/test_task_loop.py \
  evals/benchmark/tests/test_pilot.py -q
```

The controls cover shared generation/tool budgets over edit stages, factory/adapter/single-stage/task/pilot consent gates, provider output caps and post-response token overages, frozen task selection, wrong-geometry mutants, negated/denying clarification responses, multi-point enclosure cavities, request-sourced bore tolerances, phase-rotated wheel and hex-profile references, alternate-axis heat-sink arrays, stage-matched false-verified accounting, bounded positive references, and unsupported evidence counted as a nonpass. They do not call a provider. The local pytest environment may warn that the root `timeout` option is unknown when `pytest-timeout` is not installed.

Statistics and corpus validation are separate:

```sh
uv run --offline --project packages/platform --with pytest==9.2.0 python -m pytest \
  -p no:cacheprovider \
  evals/benchmark/tests/test_statistics.py \
  evals/benchmark/tests/test_corpus.py -q
```

`test_provider_sdk.py` exercises pinned SDK request/response translation through in-memory HTTP transports; it makes no network or paid call. The offline cache in the implementation environment did not contain the SDK's transitive `jiter>=0.16` dependency, so that SDK-specific test could not be resolved there. Do not enable network access merely to reproduce it in a no-spend validation.

## Run boundaries

The paid pilot command requires explicit provider, model, caller-supplied input/output token rates, and `--confirm-spend`. It prints an estimate at the configured reservation budget before preflight or provider construction. Without the flag it exits before creating run output or making a provider request. Provider adapters receive a 2,048 output-token cap per generation, but input counts can understate billed usage; the 12,000-token per-task budget is not a hard billable-token ceiling. Generation-call and model-issued AAD-tool-call limits remain hard counts, and SDK retries are disabled. The lower-level single-stage CLI also requires `--confirm-spend` and the shared factory rejects a missing `SpendConsent`. Full limits and metric denominators are documented in [../README.md](../README.md).

The runner covers only one AAD arm, one model, and one repetition per selected task. No matched plain-build123d runner is wired. Even an eligible future pilot report is not the release gate, which still requires matched baselines, multiple model families, repeated trials, and an independent native-verification sample.
