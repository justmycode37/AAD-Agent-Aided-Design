# Reporting protocol

This protocol is declared before inspecting real-agent outcomes. There are no sampled agent results in this directory yet. Statistical tests use synthetic controls and cannot establish AIAD's release criteria.

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

For a complete eligible sample, report the one-sided Clopper–Pearson upper confidence bound for bad/n, conditional on selected trials making a native verified claim and on the declared independent-sampling assumptions. The implementation calls [SciPy `binomtest`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html) with `alternative="less"` and `proportion_ci(method="exact")`; record the installed SciPy version. A bound below the declared threshold satisfies only this statistical criterion. It does not pass the release gate: isolation, authenticated provenance, the complete real-agent run matrix, independent audit coverage and baseline comparisons still need validation.

## Reproduce the implementation controls

From the S5 worktree:

```bash
UV_PROJECT_ENVIRONMENT=packages/quality/.workspace-venv uv run --locked --all-packages --all-extras python -m pytest evals/benchmark/tests/test_statistics.py -q
```

The zero-failure binomial control is also checked against the independent closed form `1 - (1-confidence)**(1/n)`. The test suite includes unknown denominators, duplicate repeated tasks, scripted controls, incomplete pairing, degenerate bootstrap distributions and nominal false-verified samples. All are authored fixtures, not agent measurements. No public benchmark submission is made.
