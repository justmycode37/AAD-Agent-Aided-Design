# Independent quality controls

`probe_worker_evidence.py` runs authored positive and negative controls through S1's actual worker, then freshly imports each BREP in the clean parent process. Model source never runs in the auditing process. It checks that reported metrics agree with the artifact, and that a wrong-size artifact cannot be labelled verified by mutating checker state.

```sh
packages/quality/.venv/bin/python evals/quality/probe_worker_evidence.py --repetitions 3
```

The command intentionally exits nonzero while a trust failure remains. A nonzero exit is a blocker, not an expected-pass test or permission to waive the error. Raw jobs are under ignored `.runs/<run_id>/`; retained observations are in `worker-evidence.json`. Runtime-source, model-source and BREP hashes are recorded. Runs refuse to write complete evidence if engine/quality source changes during execution, and invalidate the prior result at startup.

The retained run includes S1's containment from [PR #32](https://github.com/justmycode37/AIAD-AI-Aided-Design-/pull/32): checker mutation now stays draft with `requirements_proven: false`. The separate wrapper-metric control still reports dimensions that disagree with the BREP, so the probe remains nonzero. This is containment of a known false-verification path, not independent validation of worker evidence. Repetitions and sample standard deviation of the independently measured extent are recorded per control. These are constructed deterministic controls, not sampled agent outcomes, and they do not estimate the headline false-verified rate. See [issue #31](https://github.com/justmycode37/AIAD-AI-Aided-Design-/issues/31).

The false-verified audit for release must sample real agent outputs and test the complete original request using separate hidden checks. It must not reuse the output-derived spec or the same evaluator as its oracle. Empty verified denominators and missing audit evidence cannot count as a passing rate.


The separate `worker-evidence-gate46.json` repeats the controls on PR #46 head
`c7301d165615c7b9ab8fcba0b3b79802ed1bdd13` merged with quality commit
`0b78f55`. It retains the distinct engine and quality source fingerprints.
It is compatibility evidence, not a dependency of #34 and not permission to
promote model-worker evidence. Reproduce in a separate worktree from
`origin/main`: merge that #46 commit, merge the quality commit, sync the
locked workspace, and run the same probe with
`--repetitions 3 --out /tmp/worker-evidence-gate46.json`.

Both authored-control runs report no false verified or false requirement-proof
claims; none of their results is verified, so they estimate no false-verified
rate. Each still reports three wrapper-metric/artifact disagreements and
therefore exits 1. Independent X sample SD is 0 mm in each control. #31 remains
open until the trusted pipeline recomputes metrics and binds the receipt.
