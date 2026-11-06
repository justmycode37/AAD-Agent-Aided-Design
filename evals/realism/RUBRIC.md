# Blind review rubric

Review each packet from its request, task checklist, and rendered images only. Do not receive the model author's name, backend/arm label, build prompt, agent trace, or model reasoning. Reviewers may know which object is requested and may consult the cited public facts supplied with each checklist item.

For every checklist item, assign one ordinal rating and a confidence level. Point to one or more packet image files as evidence. Judge visible construction and interfaces against the cited fact and the request; a part name, color, label, or exterior resemblance is not geometry evidence.

## Rating

| Score | Meaning |
|---:|---|
| 0 | Required subsystem/interface is absent, contradicted, disconnected, or only represented by a label with no corresponding geometry. |
| 1 | A token shape is present, but its structure, placement, support, attachment, or relationship to adjacent parts is not credible. |
| 2 | Partially plausible geometry is visible, but a key relationship, feature, or section detail is missing or unclear. |
| 3 | Convincing geometry and relationships are visible for this checklist item; remaining uncertainty is limited and stated. |
| 4 | Detailed and coherent geometry is visible from useful views, with the relevant interfaces and assembly/service relationships clearly supported. |

A high score requires section evidence where internal construction is part of the item. Do not infer unseen parts from the outline. Do not reward decorative detail that obscures or conflicts with the requested construction.

## Confidence

| Score | Meaning |
|---:|---|
| 1 | Low: view coverage or cited evidence leaves substantial uncertainty. |
| 2 | Limited: some evidence is visible, but important geometry is ambiguous. |
| 3 | Good: the relevant shape and relationship are mostly clear. |
| 4 | High: the evidence directly shows the requested geometry and its relationship. |

Use an unassessable (`rating: null`) response only for a packet defect, such as a missing/corrupt image or a section that cannot be interpreted. Give a reason. Missing or unlabeled geometry is a score of 0, not unassessable. A score of 3 or 4 must cite at least one image. The same evidence rule applies to a 0 score so omissions can be reviewed.

The weighted task score maps the 0-4 ordinal checklist total to 0-100, weighting each preregistered checklist item as shown. It is complete only if every checklist item is rated. Report subsystem and interface subscores separately. Inter-rater agreement and confidence intervals describe these human judgments; they do not establish engineering correctness or that a model is realistic.
