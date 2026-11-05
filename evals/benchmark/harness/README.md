# S5 MCP agent-loop harness

This folder implements one backend-agnostic trial. It consumes a protocol public stage projection, a connected MCP `ClientSession` or bridge, a matching external isolation-preflight report, and one explicit provider/model configuration. It does not load task oracles, aggregate benchmark results, decide release readiness, or validate manufacturing quality.

## Setup

The harness has its own exact provider and MCP pins in `pyproject.toml` and `uv.lock`; it does not change the repository root lock or configuration.

```sh
uv sync --project evals/benchmark/harness
uv run --project evals/benchmark/harness python -m evals.benchmark.harness \
  --config evals/benchmark/harness/run-config.json \
  --public-projection evals/benchmark/corpus/public-task.json \
  --stage stage-1 \
  --launcher my_backend_launcher:launch
```

Copy `run-config.example.json` to a host-owned config and replace every `REPLACE_...` value. The model is mandatory and is not defaulted or checked against an assumed availability list. Credentials come from the provider SDK's normal environment configuration and are never copied into the run config, trace, or CLI output.

The parent-owned launcher should return `TrialLaunch(backend, isolation_preflight, close=...)`. The optional sync or async `close` callback is invoked by the CLI after the trial, including an ineligible preflight result. The preflight must describe the same backend revision/environment in the run config, attest successful private-read-sentinel, network-denial, outside-write, and solver-credential-absence probes, and carry a SHA-256 evidence digest. The loop performs no provider token-count or generation request until these checks pass. The launcher owns backend startup, teardown, staging only declared public assets, and production of this preflight report.

`provider_supports_image_input` is an explicit model capability declaration. If tool images are enabled and this is false, the loop stops before any provider request. Set both image fields to true only after verifying image input for the explicitly selected model; the example keeps them disabled.

`PublicStageProjection.from_task_projection` rejects task, stage, or asset fields for oracle data, expected outcomes, hidden checks, host paths, and credentials. Pass the projection described in `PROTOCOL.md`, not a complete task record.

## Loop and trace behavior

- Tool schemas are taken from the backend's native MCP catalog. The four AAD app-only tools are filtered using names and MCP app metadata; tools that look like host filesystem, shell, browser, corpus, oracle, or credential functions are excluded. Tool descriptions and schemas are sanitized before they reach the provider. For non-AAD catalogs, provide an explicit `native_tool_allowlist` from that backend's pinned catalog.
- The harness adds `benchmark_finish` with the protocol receipt schema. It stores the receipt as a model claim. Server `status: verified` claims are stored separately and neither kind is treated as independent evidence.
- OpenAI uses Responses function calls and `responses.input_tokens.count`; Anthropic uses Messages tool blocks and `messages.count_tokens`. The prompt count includes the native tool catalog and images. The same reservation rule applies to both: count input, add the configured percentage and fixed safety margin, then reserve the full per-call output limit before generation. Anthropic's counter is documented as an estimate, so keep a positive margin. Actual cache, image, and reasoning usage detail is retained; cache and reasoning subcounts are not added twice.
- Missing or malformed provider usage stops the loop and makes the trial ineligible. The result reports known input/output sums and uses `null` for a total if any attempted generation has unknown usage. A provider call timeout also leaves usage unknown.
- Requested MCP image blocks are sent as image input to both providers. App-only scene reads are never exposed; binary geometry payloads in structured results or text are replaced by size and digest metadata.
- The JSONL trace is exclusive-created with mode `0600`, append-only, flushed per event, and resolved outside every configured solver write root. It stores sanitized raw provider/tool observations, hashes for the public projection, instructions and actual provider tool catalog, plus separate claim events. Auth-like values are redacted. An in-memory sink is marked `mock` and cannot yield a sample-eligible result.
- Origin labels are provisional diagnostics. The loop always returns `sample_eligible=false` pending a separate host manifest validator. `provisional_eligible` means only that the loop found no local eligibility blocker. Custom provider clients default to custom origin. Forged live labels cannot authenticate a sample.
- Artifact receipt paths remain model claims. `stage_claimed_artifacts()` is a host-side helper that accepts only existing relative files under the solver output root, rejects absolute/traversing/external/escaping-symlink claims, and copies accepted files to a destination outside all solver write roots. Copying is not geometry validation.
- Name and argument failures are recorded only as `*_error_proxy` counters. No semantic wrong-tool label is inferred.

Each invocation returns one `TrialResult`. Budget stops and incomplete model turns retain their actual observed usage and remain visible as incomplete. No retained result is resumed or reused by this package.

## Programmatic composition

```python
from evals.benchmark.harness.aad_benchmark_harness import (
    IsolationPreflight,
    PublicStageProjection,
    RunConfig,
    run_agent_loop,
)

stage = PublicStageProjection.from_task_projection(public_projection, "stage-1")
config = RunConfig.from_mapping(run_config_json)
result = await run_agent_loop(
    config,
    stage,
    backend=connected_client_session_or_bridge,
    isolation_preflight=preflight_for_this_backend_revision,
)
```

The official SDK usage follows the [OpenAI function-calling guide](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI token-counting guide](https://developers.openai.com/api/docs/guides/token-counting), [Anthropic tool-use guide](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview), [Anthropic token-counting guide](https://platform.claude.com/docs/en/build-with-claude/token-counting), and [Anthropic tool-result image format](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls).

## Offline contract tests

```sh
PYTHONDONTWRITEBYTECODE=1 uv run --project evals/benchmark/harness python -m pytest -p no:cacheprovider evals/benchmark/tests/test_agent_loop.py evals/benchmark/tests/test_provider_sdk.py -q
```

Those tests use fake provider/MCP implementations and the pinned official provider SDKs over in-memory HTTP transports. They must be reported as mock-origin contract tests, not real-agent outcomes or evidence about CAD quality, verified status, or manufacturability. No provider network or paid model call is made. Parent validation and integration limitations are recorded in `VALIDATION.md`.

Async timeouts are cooperative. A response arriving after the deadline cannot finish the trial, but blocking code can exceed wall time before returning control. The pending host coordinator must enforce the hard deadline in a killable process and retain unknown usage after termination.
