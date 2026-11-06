---
id: review.render.v1
title: Independent render review brief
tags: [review, render, sections, checklist]
candidate_status: unmeasured
---

# Independent render review brief

The building agent creates `aad_render(review_packet=true)` after it has finished its own geometry checks. It hands the resulting directory to a separate reviewer when its host has a separate-reviewer or subagent capability. The reviewer sees only the packet directory: `request.txt`, `manifest.json`, and the listed images. Do not provide the conversation, model source, building agent's reasoning, or other files.

Review each item below against the original request in `request.txt`. Use only evidence visible in packet files. A section or isolated view can reveal geometry; it does not prove function. A measured bbox is an envelope, not proof of fit. A rendered color is not an analysis result. Check every saved result, including every `skip`, in `manifest.json`.

1. **Intent:** Does the geometry in the images appear to match each relevant clause of the original request, including orientation and use? Identify omitted clauses or evidence gaps.
2. **Envelope:** Do front, back, left, right, top and bottom views agree with the requested extents and orientation? A bbox alone does not prove interface fit.
3. **Interfaces:** Are hole count, spacing, mating faces, connectors and fastener access visibly consistent? Use sections to look for blocked or blind features; a hole count does not prove access.
4. **Fit and motion:** Are the relevant clearances or swept paths actually represented in packet evidence? A static render or a single distance does not prove motion.
5. **Walls and cavities:** Do sections show intended openings and material continuity? A sampled thin wall can show a violation; passing samples cannot prove a global minimum.
6. **Process:** Does the geometry appear consistent with the named process? Record process-screen skips. No finite screen proves manufacturability.
7. **Checks and status:** Are all errors, warnings and skips accounted for? Do not infer that a check passed when it is absent or skipped. Revision status in the manifest remains the build's status and is not proof of the user's intent.

Return one finding for each item using this shape. Choose `PASS`, `FINDING`, `NEEDS EVIDENCE`, or `NOT APPLICABLE` for each result.

```json
{
  "review_kind": "independent",
  "findings": [
    {
      "item": "Intent",
      "result": "NEEDS EVIDENCE",
      "request_excerpt": "short exact excerpt from request.txt, or null",
      "evidence": [
        {"file": "review-overview-front.png", "observation": "What the image does or does not show."}
      ],
      "next_change": "Concrete change, or null"
    }
  ]
}
```

Every result needs evidence filenames and an observation. Use `NEEDS EVIDENCE` when the packet cannot support a conclusion; do not fill the gap with guesses or building-agent assumptions. `FINDING` names a visible mismatch and a concrete next change. If the host has no separate reviewer, the building agent may perform the same checklist but must set `review_kind` to `self-review` and say that the critique was self-review.

The reviewer returns findings only. Review results do not alter a revision's status, count as verification, or establish engineering performance. `candidate_status` remains `unmeasured` until a real-agent harness evaluates this brief.
