# S5 benchmark protocol, draft v1

This is an evaluation implementation protocol, not a change to CONTRACTS.md. No real-agent benchmark outcome has been measured yet. Unit tests, scripted tool controls and reference builders must never be labelled real-agent outcomes.

## Pilot implementation status (not release evidence)

The repository now contains a frozen, outcome-blind 20-task selection in `pilot-selection.json`: five parts, four edit chains, three assemblies, three 2D profiles, two drawings, two clarification tasks and one infeasible request. Within each category, task IDs are ranked by SHA-256 of the fixed seed, category and task ID; the fixed quota is taken before any result is observed. The manifest records all rankings, the 100-task corpus hash and run order.

The current runner exercises only AAD, one provider/model and one repetition. Its offline control uses a scripted model and real local MCP stdio/AAD daemon and is not an agent outcome. No paid or real-agent run is claimed here. This AAD-only pilot does not satisfy the matched-backend, multiple-model-family or repeated-trial release matrix described below, and a 20-task pilot is not the release gate. Every live provider adapter is created through a shared factory that requires an explicit `SpendConsent` object derived from `--confirm-spend`; the single-stage harness CLI and pilot CLI cannot create one without the flag. See [README.md](README.md) for the exact selection, budgets, command, oracle coverage and report fields.

For original-request auditing, a task is a confirmed pass only with complete coverage and all checks passing. Any unsupported check remains unsupported and in the raw denominator; it never counts as a pass. Bounded positive evidence is indeterminate. The runner does not authenticate its own run manifest, so false-verified inference is blocked until a host-side manifest validation is performed outside the CLI.

For edit chains, compare each native verified claim with the original-request audit for the same stage. A later stage failure does not retroactively make an earlier, verified stage false. Report the false-verified outcome at the task sampling unit by aggregating only the claimed stages.

## Task records

`corpus/tasks.json` is `{ "schema_version": 1, "tasks": [...] }`. Each task has `id`, `title`, `category`, `difficulty` (`easy`, `medium`, `hard`), `kind` (`part`, `assembly`, `2d`, `drawing`, `clarification`), `tags`, and a nonempty `stages` list. Each stage has:

- `id`: unique within the task.
- `request`: the exact public user message, with explicit dimensions/tolerances where relevant.
- `expected_outcome`: `artifact`, `clarification`, or `infeasible`.
- `input_assets`: a list of public input asset descriptors, empty when not needed. Only these assets may be staged for the solver. Relative paths stay in the corpus asset directory.
- `oracle`: host-only `{coverage: "complete"|"bounded", checks: [...], preserve_check_ids: [...]}`. Each check has a unique `id`, `type`, and its typed expected values. Preservation IDs name current-stage checks for unchanged requirements after an edit.

Tasks may have a host-only `reference` describing a factory and parameters in `corpus/references.py`. Reference builders are positive controls, never model output or input unless explicitly declared as public assets. Expected values come from the request and independent arithmetic, not measurements copied from the reference builder's output. Hidden checks must not import aad_quality evaluators or execute generated model source. Unknown checks stay unassessed.

Initial check types: `bbox_mm` (`ranges`: three inclusive [min,max] pairs), `volume_mm3` (`range`), `solids` (`equals`), `point_material` (`point_mm`, optional `additional_points_mm`, `equals`: solid/void), `cylindrical_bores` (explicit axes, centers, request-sourced center tolerance, diameter/depth bounds and through/blind intent), `clearance_mm` (named public reference asset and `range`), `dxf_profile` (cut layers, contour counts, request-tolerance circles, optional circle patterns, regular polygons, extents and/or dimensioned segments), `drawing_dimensions` (expected measured dimensions and required views), and `clarification` (the missing/conflicting requirements that must be surfaced). Pilot clarification responses remain unsupported until meaning and polarity can be independently audited. Other types require explicit implementation and validation before they can pass. Finite witnesses do not become a global manufacturing certificate. A bounded oracle cannot establish complete original-request correctness.

The public projection contains task ID/title/kind plus each stage's request and declared public assets. It excludes expected outcomes, oracle data, preservation IDs and reference factories. Keep public projection and hidden definitions separate in the runtime filesystem.

## Real agents and fairness

Compare AAD, build123d-mcp, agentcad and a plain-build123d MCP wrapper with the same model and budget within each model family. Require at least two model families and repeated trials. Backends use their pinned native tool catalog; count its prompt cost. Exclude app-only tools from the model. Only CAD MCP tools and a common `benchmark_finish` tool are exposed; no host filesystem, shell or browser tool is given to the model.

The common `benchmark_finish` receipt is `{artifacts: [...], claims_requirements_met: boolean, summary: string}`. It is a model claim, not evidence. Record native server `verified` claims separately from common model success claims; do not pretend a baseline has an equivalent verification contract when it does not.

Run configuration explicitly names provider/model, backend revision/environment, token budget, call limit, wall deadline, repetition and task-order seed. No unverified model-name defaults. The pilot reserves up to 12,000 estimated input/output tokens per task before a generation, with 32 generation calls, 96 model-issued tool calls and 300 seconds. The token reservation is not a hard billable-token ceiling: a provider can report more input usage than the pre-call estimate, and that overage can be billed before later calls stop. OpenAI `max_output_tokens` and Anthropic `max_tokens` are set to the per-call output cap, but the harness has no provider-side input-token cap and does not establish a finite worst-case total-token or cost bound. Use actual provider usage receipts; missing usage is unknown/ineligible, not zero. Preserve reasoning/cached/image usage details without double-counting provider totals. Handle tool errors consistently and stop after reported overages or deadline exhaustion. Do not log credentials or auth headers.

Description/rubric ablations must be declared before examining outcomes. Keep their arm and catalog/instruction hashes in the result. They remain unmeasured candidates until that comparison runs. Do not tune on a held-out task set and then call the same results independent validation.

## Execution and hidden-check isolation

Provision backend runtimes without hidden checks. A solver's staged job contains only public assets and its own outputs. Model-generated CAD Python must not read the corpus, oracle sources, host result store or provider credentials, and must not reach the network. Sanitize server environments. Test the actual backend policy using private read sentinels outside the job and network/write probes. If isolation or required backend capability is unavailable, mark the trial ineligible and make no paid model call; never silently fall back to an unsandboxed run.

S1's current standalone sandbox policy denies subprocess creation on macOS, while MCP servers/daemons may need children. An eval-only outer boundary may permit descendants while retaining read/write/network restrictions, but requires actual escape probes. That is not evidence that the production engine boundary is integrated or secure. Report limitations and coordinate production changes through sector-labelled GitHub issues.

Capture immutable raw model/tool traces outside solver-writable paths, with source/environment/task/catalog hashes and run-scoped artifacts. Reject external, traversing or escaping-symlink artifact paths. Independently import geometry in a clean, killable auditing process; do not execute model source or trust its metrics/spec/receipt. Reference builders and synthetic negative controls are separate from sampled agent trials. A failed or incomplete run cannot reuse retained results.

## Reporting and release gates

Report first-pass validity, request requirement pass rate, edit retention, false-verified rate, provider tokens, time, and uncertainty with reproducible commands and raw denominators. Unknown/missing evidence does not pass. Clarification/infeasible tasks have a separate disposition metric; do not inflate geometric success with refusals. Unsupported backend tasks remain visible.

The native false-verified audit is against the complete original request, not the agent's spec. An output with any proven requested failure is bad; only full coverage with all passes is good; unresolved coverage is indeterminate. Report bad/good/indeterminate and empty denominators explicitly. An empty verified denominator or indeterminate audit cannot pass a release gate. Do not inflate independent sample size with repeated copies of one task. Report per-task repeated variance and paired/clustered uncertainty for backend comparisons. Any statistical bound used for release must state its method and sampling assumptions.

The release target remains PLAN section 7: AAD beats the best baseline on request pass rate and edit retention at no more than 1.25 times the tokens, and false-verified is below 5% in an independent sampled audit. Missing real-agent evidence blocks that claim. CADGenBench may use local public scoring/data only; no public leaderboard or Space submission without the user's approval.
