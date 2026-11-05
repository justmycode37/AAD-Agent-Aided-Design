# S5 benchmark pilot

This change prepares a frozen 20-task AAD pilot. It adds independent original-request checks for the selected tasks, a complete edit-chain loop, a local isolation preflight, a real MCP-stdio/AAD-daemon runner, bounded paid-run controls, and a report format aligned with [PROTOCOL.md](PROTOCOL.md).

No paid provider call or real-agent run was made for this change. The offline smoke uses a scripted model only; it verifies the harness and local CAD path and is not an agent-quality result. **A 20-task pilot is not the release gate.** The multi-backend, multi-model, repeated-trial comparison and independent audit required by the protocol remain outstanding.

## Frozen task selection

The corpus has 100 tasks and 120 stages. The pilot fixes 20 tasks and 24 stages across every requested category. The selection rule and full ranked populations are recorded in [pilot-selection.json](pilot-selection.json).

| Category | Corpus tasks | Selected |
| --- | ---: | ---: |
| Part | 40 | 5 |
| Edit chain | 20 | 4 |
| Assembly | 10 | 3 |
| 2D profile | 15 | 3 |
| Drawing | 5 | 2 |
| Clarification | 5 | 2 |
| Infeasible request | 5 | 1 |
| **Total** | **100** | **20** |

The selection hashes every task ID within its predeclared category using SHA-256 of `seed + NUL + category + NUL + task_id`, sorts ascending, and takes the fixed quota. A second hash determines run order. Seed: `aad-benchmark-pilot-v1`. Neither difficulty, reference availability, oracle support, nor outcome is an input to selection. The checked-in manifest also stores the corpus hash, every category ranking, selected IDs, and run order. Quotas are fixed to ensure category coverage and are not proportional; estimates weight each category by its corpus population. Validate the manifest with:

```sh
uv run --offline --project packages/platform python -m evals.benchmark.selection
```

The selected IDs are:

- Parts: `part_plate_001`, `part_step_bushing_002`, `part_lid_002`, `part_lobe_wheel_001`, `part_spoked_wheel_001`.
- Edit chains: `edit_plate_widen_001`, `edit_enclosure_add_mounts_011`, `edit_clamp_add_bolt_015`, `edit_heat_sink_add_fins_017`.
- Assemblies: `assembly_fan_support_007`, `assembly_corner_stop_008`, `assembly_spacer_stack_010`.
- 2D profiles: `profile_keyhole_006`, `profile_hex_flange_009`, `profile_gasket_012`.
- Drawings: `drawing_clevis_002`, `drawing_flange_004`.
- Clarification: `clarify_electronics_box_002`, `clarify_pipe_adapter_004`.
- Infeasible: `infeasible_envelope_005`.

The current selection has no input assets. The runner stops before any model call if a later selection change adds assets that have not been staged.

## Independent oracles and coverage

The selected tasks declare 94 original-request check records: 91 bounded artifact checks and three clarification/infeasible checks that remain unsupported pending a semantic audit. Implemented artifact check types are `bbox_mm`, `solids`, `point_material`, `cylindrical_bores`, `component_layout`, `linear_pattern`, `rotational_pattern`, `dxf_profile`, and `drawing_dimensions`. The 2D profile checks read saved native Sheet2D JSON; drawing checks compare the BRep-derived requested dimensions and projected views with the saved SVG.

The host audit reads saved BRep, Sheet2D JSON, SVG, and response text. It does not execute model source, import `aad_quality`, trust AAD-returned measurements, or treat `benchmark_finish` as evidence. It runs in a fresh `python -I -B` process with a 90-second timeout and bounded request, output, and artifact sizes. Missing expected artifacts are failures. A failure witness makes a task bad; a task is good only when every requested check passes under complete coverage. Bounded positive evidence and unsupported checks stay indeterminate. Unsupported checks remain in the raw denominator and never count as passes.

The negative controls apply wrong BReps, an empty 2D profile, a wrong SVG, capped or undersized bores, and a pinhole in place of an enclosure cavity. Rotated spoke and lobe wheel references verify that the pattern oracle does not assume an unrequested angular phase. Clarification wording is deliberately unsupported: negated topic lists and responses that deny an infeasible conflict remain indeterminate and cannot enter the request-pass numerator. A positive `part_plate_001` reference control passes its bounded witnesses and still remains indeterminate. These are oracle controls, not agent samples.

## Offline end-to-end smoke

Run the scripted control with a new output directory:

```sh
uv run --offline --project packages/platform python -m evals.benchmark.pilot \
  --offline-smoke \
  --output-dir /tmp/aad-pilot-smoke
```

This launches the packaged `aad_platform.cli mcp` server over stdio, starts a fresh real AAD daemon for each selected task, runs the AAD isolation probes, executes the scripted fake model, saves native artifacts, audits them in the isolated worker process, and confirms daemon teardown. It sends zero provider API requests. Its token receipts and CAD responses are scripted controls; no quality conclusion may be drawn from them.

The output directory must not already exist. It contains `report.json`, `manifest.json`, `preflight.json`, per-task JSON records, per-stage traces, and saved artifacts. The report records task selection, corpus/catalog/backend hashes, raw denominators, classifications, usage and timing, and uncertainty fields. The model claim, native verification claims, and independent audit remain separate.

## Paid pilot command and spend estimate

This is the single cost-accounted pilot command. Set the four `PILOT_*` shell values to the chosen model, caller-supplied input/output prices, and a new output directory before running it. The runner does not discover or verify prices. It prints an estimate at the configured token budget before isolation preflight or provider creation. Without `--confirm-spend`, it prints that estimate and exits without creating the output directory or sending a provider request. With the flag, it creates an explicit consent object; the shared provider factory independently rejects adapter creation without that object. Injected providers also need consent unless explicitly marked with a fake, mock, or scripted origin. This consent is an accidental-spend guard, not authentication or a security boundary; a Python caller can deliberately invoke the private factory. The lower-level single-stage harness CLI also requires `--confirm-spend`, but it does not produce this cost estimate or a pilot report.

```sh
uv run --project packages/platform python -m evals.benchmark.pilot \
  --provider openai \
  --model "$PILOT_MODEL" \
  --input-usd-per-million "$PILOT_INPUT_RATE" \
  --output-usd-per-million "$PILOT_OUTPUT_RATE" \
  --output-dir "$PILOT_OUTPUT_DIR" \
  --confirm-spend
```

Use `--provider anthropic` instead when that is the selected provider. The provider SDK uses the caller's configured credentials; the runner does not inspect or pass credentials into MCP or the sandboxed CAD worker. SDK retries are disabled.

The pilot is capped at:

| Budget | Per task | Pilot maximum |
| --- | ---: | ---: |
| Configured input plus output token reservation | 12,000 | 240,000 |
| Generation calls | 32 | 640 |
| Provider token-count calls | 32 | 640 |
| Provider API requests | 64 | 1,280 |
| Model-issued AAD MCP tool calls | 96 | 1,920 |
| Wall time | 300 s | 6,000 s of task deadlines, plus preflight and audit time |
| Audit timeout | 90 s | 1,800 s across 20 audit processes |
| Provider-side requested output cap per generation | 2,048 | 2,048 |
| `aad_build` timeout | 120 s | 120 s |
| Repetitions | 1 | 1 per task |

Generation, token-count, and model-issued tool-call limits are shared across every stage of an edit chain. The host separately performs one `aad_new` and up to four `aad_build` isolation probes before the model loop; these local calls are not provider calls or model-issued tool calls. The printed estimate prices the configured 240,000-token reservation at the higher caller-supplied input/output rate. It is not a spend ceiling: a provider can report more input usage than its pre-call count, and the overage can be billed before the loop stops later calls. The runner sends OpenAI `max_output_tokens` or Anthropic `max_tokens` of 2,048 per generation; if honored, output generation is at most 1,310,720 tokens across 640 calls. These adapters cannot enforce a provider-side input-token cap, so this harness does not establish a finite worst-case total-token or spend bound. Request fees, taxes, discounts, and cached-token rates are also outside the estimate.

## Reported metrics and uncertainty

`report.json` reports the following with raw counts and denominators:

- **Request pass rate:** complete, all-pass original-request task audits divided by all 20 selected tasks. Bad and indeterminate tasks remain in the denominator. With bounded artifact checks this is a confirmed-pass lower bound; true request correctness is marked not estimable. The raw 20-task rate is separate from the category-population-weighted bootstrap estimate.
- **Check witnesses:** passed, failed, unsupported, and total declared checks. Unsupported and missing evidence are nonpasses.
- **First-build validity:** valid first `aad_build` artifacts divided by all artifact-expected stages; unattempted stages remain nonpasses. It includes a task-cluster bootstrap over each artifact task's valid-stage fraction.
- **Edit retention:** preserved checks that pass after an edit divided by preservation checks that passed on the preceding stage. The report includes the source-pass denominator, unknown/failed counts, and per-task values.
- **Clarification/infeasible disposition:** unsupported semantic checks remain indeterminate and do not count as good; counts are reported separately from artifact tasks, with its own task-cluster bootstrap.
- **False-verified audit:** native-verified trials classified against the original request. It reports `n_verified`, bad/good/indeterminate counts, unknown claims, missing records, blockers, and the unresolved range. An empty native-verified denominator is unknown, not zero false-verifications. The CLI does not authenticate its host manifest, so it sets `host_manifest_validated` to false and blocks inferential bounds; only raw counts are reported.
- **Usage and time:** token receipts, generation/token-count operations, provider API request count, model-issued MCP calls, task/tool wall time, and total pilot time. `usage_origin` distinguishes provider-reported usage from synthetic scripted receipts; offline smoke reports zero provider API requests. Token and time totals have no repeated-run interval because the pilot uses one repetition per task.

For offline smoke, the top-level report and manifest set `provider` to `scripted`. The harness's internal provider-family compatibility value does not mean an OpenAI adapter or API request was used.

The pilot reports a 95% stratified task-cluster percentile bootstrap with 10,000 resamples and fixed seed `20261105`. Each category is resampled at its selected task count and weighted by that category's corpus population. These intervals are descriptive and assume the hash-ranked sample is exchangeable within each category; the singleton infeasible stratum has a degenerate interval. The one-sided Clopper–Pearson false-verified bound is available only after an external host-manifest validation; this CLI does not perform that validation. Details and limits are in [STATISTICS.md](STATISTICS.md).

## Comparison scope

This runner includes one AAD backend arm per run. A matched plain-build123d arm is not included: this repository has no plain-build123d MCP runner with the same pinned tool catalog, isolation preflight, artifact capture, and report path. Wiring and validating those pieces would be a separate benchmark backend, not a small configuration change. Existing `build123d-mcp` teardown artifacts are not a plain-build123d control and are not pooled here.

The result is a pilot, not the release gate. The release matrix still requires matched backend comparisons, at least two model families, repeated trials, and an independent native-verification audit under the protocol. No agent result is claimed by the offline smoke.
