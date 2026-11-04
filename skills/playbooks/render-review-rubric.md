---
id: review.render.v1
title: Render review rubric
tags: [review, render, sections, checklist, ablation]
candidate_status: unmeasured
---

# Render review rubric

Unmeasured candidate rubric. It has not been evaluated in a real-agent harness. Use it standalone: mark each item PASS, NEEDS EVIDENCE, or NOT APPLICABLE and record the request phrase, view/section inspected, and next change.

1. **Intent:** Does the rendered shape match the original request, including orientation and use? Record unstated assumptions or omitted clauses.
2. **Envelope:** Do orthographic views agree with requested extents and axis directions? A bbox pass alone is not interface proof.
3. **Interfaces:** Are hole count, spacing, mating faces, connectors, and fastener access visibly consistent? Inspect a section for blocked or blind features; a hole count does not prove access.
4. **Fit and motion:** If parts move or mate, is the relevant clearance or swept path checked? A static render or a single distance is not motion proof.
5. **Walls and cavities:** Do sections show the intended openings and material continuity? A sampled thin wall can disprove min_wall; passing samples cannot prove its global minimum. Process rays are heuristics.
6. **Process:** Does the orientation and geometry fit the named process assumptions? Record process-screen skips and consult its playbook; no finite screen is manufacturing proof.
7. **Checks and status:** Are every error, warning, and skip understood and reported? Does the handoff state draft or verified accurately, without treating that status as proof of full user intent?

Ablation note: use the checklist as written in one eval arm and omit this playbook in another. Compare task outcomes with the same requests and budget. If a real-agent harness finds no benefit, remove this standalone rubric.
