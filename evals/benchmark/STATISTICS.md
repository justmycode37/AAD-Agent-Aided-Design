# Reporting protocol

This protocol is declared before inspecting real-agent outcomes. There are no sampled agent results in this directory yet. Statistical tests and the scripted local smoke use controls and cannot establish AAD's release criteria.

Each reported number must identify its run manifests, source/task/catalog hashes, provider/model/budget, units, numerator and denominator, uncertainty method and reproduction command. Report comparison arms and model families separately. Scripted controls, missing usage receipts, ineligible runs and unsupported tasks remain visible in their own categories. A statistical helper does not authenticate a caller's provenance labels.

## Repetitions and comparisons

`statistics.task_summary` requires an explicit expected task/repetition matrix from the predeclared host plan, then summarizes repetitions within each task. It reports sample standard deviation with denominator n−1; fewer than two measured observations gives an unknown variance. The headline mean weights tasks equally, regardless of how many repetitions they have. A missing row or value, or any unexpected row, makes the headline mean unknown. Never infer the expected matrix from observed rows in a real run. Known-only diagnostics are named explicitly.

`statistics.paired_task_bootstrap` requires both arms to match the explicit expected matrix, with no unknown observations. Every observation carries an arm ID; mixed-arm observations are rejected. The caller must additionally validate matching public task, provider/model, budget and ablation configuration in host manifests. It averages each task's repetitions, then resamples task indices jointly between arms. It reports the observed difference, or the ratio of task-balanced means, a two-sided percentile interval, bootstrap sample standard deviation, seed and resample count. A ratio requires positive baseline means for every task. Missing cells cannot silently disappear from the comparison.

These descriptive intervals assume exchangeable task clusters. They do not establish that a hand-authored corpus represents all CAD requests. Degenerate intervals are identified, not treated as proof of certainty. They do not correct for choosing the best baseline, many metrics or repeated tuning. Release conclusions must use held-out results and a prespecified comparison/multiplicity policy. The procedure follows the paired-resampling and percentile definitions in the [SciPy bootstrap documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html); our sampling unit is a task cluster rather than an individual repetition.

## Native false-verified audit

Audit records must match the explicitly requested arm ID; mixed model/backend arms are rejected. Arm labels still require authentication against host manifests.

Keep native server verification claims separate from a model's `benchmark_finish` success claim. Audit the original request using the independent artifact oracle:

- **Bad:** at least one requested requirement has a proven failure. A bounded negative witness can establish bad.
- **Good:** coverage is complete and every requested requirement passes.
- **Indeterminate:** any other case, including bounded positive witnesses or unsupported requirements.

Commit a host-side sampling plan before examining selected outcomes. Select at most one repetition per task independently of its outcome and retain the sampling assumptions, selection algorithm/seed and plan SHA-256. Selection across different models or backends is a separate arm; do not pool them into one reassuring denominator. The plan and receipts must be checked externally: `false_verified_summary` accepts their identifiers but does not prove that selection was random or independent.

Report bad/good/indeterminate among selected native-verified trials, unknown native claims, missing selected audits and the unresolved fraction range `[bad/n, (bad+indeterminate)/n]`. Empty denominators are unknown. Scripted controls, missing selected records, unknown native claims and indeterminate audits block inference. Repeated trials of one task cannot increase the independent sample size.

For edit chains, match native verified claims to the original-request audit for that same stage. Aggregate the matched stage outcomes within the selected task: a later unverified edit failure does not change the audit of an earlier verified stage. A task with multiple verified stages is bad if any claimed stage is bad, indeterminate if none are bad but one is unresolved, and good only if all claimed stages have complete good audits.

For a complete eligible sample, report the one-sided Clopper–Pearson upper confidence bound for bad/n, conditional on selected trials making a native verified claim and on the declared independent-sampling assumptions. The implementation calls [SciPy `binomtest`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html) with `alternative="less"` and `proportion_ci(method="exact")`; record the installed SciPy version. A bound below the declared threshold satisfies only this statistical criterion. It does not pass the release gate: isolation, authenticated provenance, the complete real-agent run matrix, independent audit coverage and baseline comparisons still need validation.

`false_verified_summary` now requires the caller to set `host_manifest_validated=True` before it will calculate that bound. This boolean represents an external validation step; the helper cannot authenticate the manifest itself. The pilot CLI does not implement that external validator and always reports `host_manifest_validated: false`. It can show raw bad/good/indeterminate counts and the observed bad fraction among native-verified trials, but it blocks inferential bounds and threshold claims. An empty verified denominator remains unknown. Scripted controls are separately blocked from sampled-agent inference.

## Frozen 20-task pilot metrics

The pilot manifest selects 20 of the 100 corpus tasks with fixed per-category quotas and one trial per task. Its run order and corpus hash are frozen in `pilot-selection.json`. The report keeps task pass rate separate from check-level witnesses:

- `request_pass_rate` is the raw count of complete, all-pass original-request audits over all 20 selected tasks. Bad and indeterminate audits are nonpasses. Because the selected artifact oracles are bounded, this is a confirmed-pass lower bound; the report marks true request correctness not estimable when any task lacks complete coverage. Its bootstrap estimate is population-weighted by category and may differ from the raw sample rate.
- `request_check_witnesses` counts every declared check. Unsupported checks and missing evidence stay in the denominator and do not pass. The raw witness rate is passed checks / all checks; its task-level bootstrap value is the category-weighted mean of each task's passed-check fraction. It is not the task-level request pass rate.
- `first_pass_build_validity` uses all artifact-expected stages as its denominator, including stages with no build attempt. Its raw rate is stage-weighted; its bootstrap resamples tasks and uses each task's fraction of valid first-build stages.
- `edit_retention` is retained preserve checks divided by preserve checks that passed at the preceding stage. A missing or unsupported prior/final check is excluded from the numerator and recorded as unresolved; the report keeps the raw denominators and per-task counts. The raw rate pools checks; its bootstrap uses the mean task-level retention rate.
- Clarification and infeasible requests have a separate good/bad/indeterminate disposition and category-weighted task bootstrap. The current pilot treats all natural-language disposition checks as unsupported until response meaning and conflict polarity are audited, so keyword presence cannot produce a good task.
- Token and wall-time values are observed totals for this run. The configured token budget is a pre-call reservation target, not a hard billed-token ceiling; input usage can exceed the provider pre-count before the loop stops later calls. Providers receive a 2,048 output-token cap per generation, up to 1,310,720 output tokens across the configured 640 calls if honored. Input tokens have no provider-side cap here, so the cost estimate is not a finite worst-case bound. The pilot has one repetition per task, so it does not estimate repeated-run resource uncertainty; missing usage remains explicit.

The pilot uses `task_cluster_bootstrap`: it resamples tasks with replacement within each category, then weights category means by the category population count in the 100-task corpus. It uses 10,000 resamples, seed `20261105`, and a 95% percentile interval. Repetitions are averaged within task before resampling; the pilot has one repetition. Hash ranking is deterministic and outcome-blind, but exchangeability within categories is an assumption, not proof of representativeness. A one-task stratum produces a degenerate interval. These intervals are descriptive, and the 20-task pilot is not the release gate.

## Reproduce the implementation controls

From the repository root:

```bash
uv run --offline --project packages/platform --with pytest==9.2.0 python -m pytest -p no:cacheprovider evals/benchmark/tests/test_statistics.py -q
```

The zero-failure binomial control is also checked against the independent closed form `1 - (1-confidence)**(1/n)`. The test suite includes unknown denominators, duplicate repeated tasks, scripted controls, incomplete pairing, degenerate bootstrap distributions and nominal false-verified samples. All are authored fixtures, not agent measurements. No public benchmark submission is made.
