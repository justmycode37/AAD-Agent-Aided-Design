# Independent quality controls

`probe_worker_evidence.py` runs authored positive and negative controls through S1's actual worker, then freshly imports each BREP in the clean parent process. Model source never runs in the auditing process. It checks that reported metrics agree with the artifact, and that a wrong-size artifact cannot be labelled verified by mutating checker state.

```sh
packages/quality/.venv/bin/python evals/quality/probe_worker_evidence.py --repetitions 3
```

The command intentionally exits nonzero while a trust failure remains. A nonzero exit is a blocker, not an expected-pass test or permission to waive the error. Raw jobs are under ignored `.runs/<run_id>/`; retained observations are in `worker-evidence.json`. Runtime-source, model-source and BREP hashes are recorded. Runs refuse to write complete evidence if engine/quality source changes during execution, and invalidate the prior result at startup.

The retained run includes S1's containment from [PR #32](https://github.com/justmycode37/AIAD-AI-Aided-Design-/pull/32): checker mutation now stays draft with `requirements_proven: false`. The separate wrapper-metric control still reports dimensions that disagree with the BREP, so the probe remains nonzero. This is containment of a known false-verification path, not independent validation of worker evidence. Repetitions and sample standard deviation of the independently measured extent are recorded per control. These are constructed deterministic controls, not sampled agent outcomes, and they do not estimate the headline false-verified rate. See [issue #31](https://github.com/justmycode37/AIAD-AI-Aided-Design-/issues/31).

The false-verified audit for release must sample real agent outputs and test the complete original request using separate hidden checks. It must not reuse the output-derived spec or the same evaluator as its oracle. Empty verified denominators and missing audit evidence cannot count as a passing rate.
