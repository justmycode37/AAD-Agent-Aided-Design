# Human visual realism review protocol

Version: `aad-realism-objects-v1`. This is a pre-registered measurement protocol and a review instrument, not evidence that AAD or any other model is realistic.

## Sampling unit and tuning rule

The sampling unit is one independent real-object cluster, not a checklist item, image, reviewer or revision. The corpus has one requested assembly for each cluster. Revisions of one object must remain in the same split. `corpus/split.json` fixes its ranking from SHA-256 of `aad-realism-split-v1 + NUL + cluster_id` before scoring. The first two ranked objects are tuning; the last object is held out. Do not change the seed, task content, weights or split after seeing ratings. Do not use held-out ratings to tune a model, rubric or prompt. The single held-out object makes task-population intervals unavailable for that split.

## Packet content and blinding

For one exact task/arm, a packet is a plain directory with one `request.txt` and generic `view-NN.png` images. It contains no sidecar, CAD source, file path, author, arm, provider/backend, build trace or reasoning. Review forms and cited task checklists are outside the packet. The preparation tool uses only public task request text, copies image pixels without resampling, removes PNG ancillary metadata, strips original filenames and randomizes order independently for each reviewer. A custodian holds `restricted/assignment-map.json` and does not expose it to reviewers.

Before distribution, the custodian checks image pixels for titles, watermarks, view labels or other visible text that identifies the model author or arm. Fix the source render and prepare a new study if a cue reveals provenance; do not paint over or crop geometry from the packet. Reviewers must not receive model reasoning or intermediate iterations. Each reviewer sees before/after packets under unrelated opaque IDs and no indication which images are paired. Reviewers may infer a relationship from the geometry, but are never told the arm assignment.

The packet renderer must supply at least two section/interior/cutaway images and one exterior image per task. Image citations in score forms use the sanitized names `view-NN.png`. A rating of 3 or 4 must cite visible image evidence; a rating of 0 must also identify the view showing the omission or contradiction. No outline IoU, silhouette overlap, or independently fitted camera score is collected.

## Rubric and score

Each cited checklist item uses the 0-4 ordinal rubric in `RUBRIC.md`, with a separate 1-4 confidence rating. Zero means absent, contradictory, disconnected or label-only geometry. A missing item rating is unknown and prevents a complete task score. `rating: null` is reserved for a packet defect and requires a reason. It does not count as a pass.

Item weights are fixed in `corpus/tasks.json`. The score is:

`100 * sum(item_weight * item_rating) / (4 * sum(item_weight))`

Report subsystem and interface scores separately. Average reviewer scores within each exact task/arm first, then weight each object cluster equally. Never multiply the apparent sample size by sections, checklist rows, image count or raters.

## Reliability and uncertainty

Report per-item paired counts, exact agreement, and mean pairwise linear- and quadratic-weighted Cohen kappa across reviewers. Kappa is `null` when its expected-agreement denominator is zero. Do not hide undefined kappas or empty denominators.

For an exact task/arm, 95% percentile bootstrap intervals resample reviewer scores and describe reviewer-judgment uncertainty only. For each before/after arm summary and paired delta, use the paired task-cluster percentile bootstrap in `evals/benchmark/statistics.py`, seeded `20261106`, with 10,000 resamples. Cluster on the frozen object/task IDs, not checklist rows or reviewers. The procedure must report incomplete matrices as incomplete and fewer than two independent clusters as not estimable. State the exchangeability assumption and the small fixed corpus limitation.

## Report format

Each report is JSON with this structure (values shown as field names, not example scores):

```json
{
  "corpus_id": "aad-realism-objects-v1",
  "corpus_sha256": "<frozen task file hash>",
  "split": "tuning or held_out",
  "measurement": "human weighted checklist score, 0-100",
  "arms": {
    "before": {"n_tasks_expected": 0, "n_tasks_complete": 0,
      "task_balanced_mean_0_100": null, "task_cluster_ci_95": {}},
    "after": {"n_tasks_expected": 0, "n_tasks_complete": 0,
      "task_balanced_mean_0_100": null, "task_cluster_ci_95": {}}
  },
  "paired_after_minus_before": {"estimate": null, "interval": null},
  "per_task": {},
  "inter_rater": {},
  "denominators": {},
  "confidence_interval_scope": {},
  "claim_limit": "..."
}
```

The report's interpretation is limited to human judgments against the frozen checklist and images. It does not certify geometry, operational behavior, strength, fit, flightworthiness, safety, manufacturability, or realism as an absolute property.
