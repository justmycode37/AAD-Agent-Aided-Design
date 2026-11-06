# Real-object realism review harness

This directory adds a frozen, source-backed human review instrument for mechanical detail and assembly interfaces. It does not run agents and does not decide that a model is realistic. The report contains bounded reviewer checklist scores, inter-rater agreement, denominators and uncertainty only.

No paid provider call or real-agent run was made. The tests use synthetic image bytes and scripted rating cards only; they validate harness mechanics, not model quality.

## Corpus and split

The corpus contains three real-object tasks: the Audi 0B5/DL501 dual-clutch transmission, the Bitcraze Crazyflie 2.1 nano quadcopter, and a serviceable Raspberry Pi 4 Model B enclosure. Each has a source-cited subsystem and interface checklist in [`corpus/tasks.json`](corpus/tasks.json), with fact summaries and reference/license audit notes in [`corpus/COVERAGE.md`](corpus/COVERAGE.md).

The split was fixed before any score exists. Tasks stay grouped by independent object cluster. The SHA-256 rule and ranking are frozen in [`corpus/split.json`](corpus/split.json): the first two ranked clusters (`enclosure_rpi4_serviceable`, `gearbox_0b5_dl501`) are tuning; the remaining cluster (`drone_crazyflie_21`) is held out. This is a tiny corpus: the held-out split has one object, so it cannot support a task-population confidence interval or broad generalization claim.

Check the corpus, file license notice, reference digest and split with:

```sh
uv run --offline --project packages/platform python -m evals.realism freeze-check
```

Only the low-resolution Crazyflie 2.1 simulation mesh is stored. The upstream repository is MIT licensed and its notice is retained. The mesh is limited to visible layout/orientation checks; it does not establish product CAD accuracy. The gearbox and enclosure reference-file slots are empty. No uncertain or non-commercial case file is stored. Outline/silhouette overlap is never presented as 3D accuracy.

## Prepare blinded packets

The packet contract is deliberately simple: each packet directory contains only `request.txt` and generic-named PNG images. Supply clean `aad_render`-style section packets as directories grouped by task ID:

```text
before/
  gearbox_0b5_dl501/
    exterior-iso.png
    section-xz.png
    section-yz.png
  enclosure_rpi4_serviceable/
    ...
after/
  gearbox_0b5_dl501/
    ...
```

The selected split must have at least two `section`, `cutaway` or `interior` views and one exterior view per task. PNG metadata is removed without resampling the image pixels, filenames and order are replaced, and each reviewer gets an independent random packet order and opaque IDs. Do not put an arm, author, prompt trace, or reasoning in the images. A custodian must inspect the rendered pixels for watermarks or labels that reveal the author/arm; the tool cannot detect visible branding.

For independent ratings, use at least two reviewer IDs. The assignment key belongs in `restricted/`; do not share that directory with reviewers. Share each reviewer only their packet subdirectory, matching form from `review_forms/`, and the rubric/checklist materials. Reviewers do not receive the hidden assignment map or any model reasoning.

```sh
uv run --offline --project packages/platform python -m evals.realism prepare \
  --before /path/to/before \
  --after /path/to/after \
  --output-dir /path/to/new-study \
  --reviewers rater_1 rater_2 rater_3 \
  --split tuning
```

Use `--split held_out` only after the candidate and rubric are frozen. The current held-out split has one object, so task-cluster uncertainty is reported as not estimable. Each reviewer fills their JSON form with an integer `rating` from 0 to 4, `confidence` from 1 to 4, one or more generic image filenames in `evidence`, and a short rationale. An unassessable item must have `rating: null` and a reason; omitted/unknown items do not become zero or pass. The rubric is [`RUBRIC.md`](RUBRIC.md).

Score completed forms into a new report path:

```sh
uv run --offline --project packages/platform python -m evals.realism score \
  --prepared-dir /path/to/new-study \
  --ratings-dir /path/to/completed-forms \
  --output /path/to/new-report.json
```

## Scoring and report

The per-task human checklist score is the weighted 0-4 rating mapped to 0-100. Weights are preregistered per item. A task score is `null` until every checklist item is assessed. Subsystem and interface subscores are kept separate so an exterior shell cannot compensate for missing mechanisms or disconnected interfaces.

The report includes:

- Per-task and per-arm score, item coverage, and 95% reviewer-bootstrap confidence intervals.
- Equal-task arm summaries and a paired after-minus-before 95% task-cluster percentile bootstrap. This reuses `evals/benchmark/statistics.py`; reviewers and checklist items are not treated as independent tasks.
- Pairwise exact agreement, linear- and quadratic-weighted Cohen kappa, and paired-item counts.
- Missing form/packet/rating denominators, unassessable items, and the frozen corpus hash.

Reviewer-bootstrap intervals describe uncertainty in human ratings of the exact artifact. Task-cluster intervals describe only the frozen task sample under exchangeable-cluster assumptions. For a one-object held-out split they are not estimable. Neither kind of interval is engineering validation, and no score supports a statement that models are realistic.

Tests include synthetic rating cards for an exterior-only shell and labeled floating blocks. They only test the rubric arithmetic, because their ratings are written by hand. **Nothing yet shows that reviewers rate a rendered shell or block model poorly.** A study should add rendered controls of both kinds before any result is reported.

## Run the offline tests

```sh
uv run --offline --project packages/platform --with pytest pytest -q evals/realism/tests
uv run --offline --project packages/platform --with pytest pytest -q evals/benchmark/tests/test_statistics.py
uv run --offline --project packages/platform --with pytest pytest -q tests/e2e/test_guide_coverage.py
```

No AAD capability changed, so `skills/aad/SKILL.md` is not modified.
