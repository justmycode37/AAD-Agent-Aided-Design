# Benchmark foundations

This change provides an offline-tested single-stage MCP agent loop, two pinned provider adapters, task-clustered statistical helpers, and 100 task definitions with a public projection. It does not provide completed head-to-head benchmark results or an independent false-verified audit.

The harness preserves native server claims separately from model success claims, records provider usage, reserves input/output budgets before generation, refuses late finishes, protects trace and artifact paths, and requires authenticated host validation before a result can become sample-eligible. See [harness/VALIDATION.md](harness/VALIDATION.md) for its limits.

The corpus has 120 stages. Its 105 supported reference controls have native bounds, solid counts, material-point witnesses or DXF extent checks. Five drawing controls explicitly report unsupported: labeled rectangles and typed dimensions cannot establish actual projections or measured dimensions. All artifact coverage remains bounded. These authored controls are not agent samples.

Reproduce the final local checks from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 uv run --project evals/benchmark/harness python -m pytest -p no:cacheprovider evals/benchmark/tests/test_agent_loop.py evals/benchmark/tests/test_provider_sdk.py -q
PYTHONDONTWRITEBYTECODE=1 UV_PROJECT_ENVIRONMENT=packages/quality/.workspace-venv uv run --locked --all-packages --all-extras python -m pytest -p no:cacheprovider evals/benchmark/tests/test_statistics.py evals/benchmark/tests/test_corpus.py -q
```

The parent ran these commands: 30 harness controls and 45 statistics/corpus controls passed. The standalone harness environment reports one warning because the root pytest timeout option has no plugin installed there. Its SDK tests use in-memory HTTP transports; they make no network or paid model call. Test counts have no agent-quality uncertainty estimate or false-verified denominator.

Work required before real comparison results can count:

1. Implement and independently validate original-request artifact oracles, including negative mutants, proper projected drawing controls and full coverage accounting.
2. Bind actual backend isolation probes to each job, runtime and policy; enforce hard wall limits and descendant cleanup outside the cooperative agent loop.
3. Compose whole edit chains with a shared budget and conversation; bind all receipts and immutable artifacts in a host-validated run manifest.
4. Run four backends with matched budgets across two model families, retain repeated trials and an independent audit sample, then coordinate CI/release gates with S2.

[PROTOCOL.md](PROTOCOL.md), [STATISTICS.md](STATISTICS.md) and [corpus/COVERAGE.md](corpus/COVERAGE.md) define the reporting and coverage limits. No shared contract changed. No public leaderboard submission was made.
