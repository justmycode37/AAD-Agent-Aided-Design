---
id: workflow.core.v1
title: Core modeling workflow
tags: [workflow, spec, model.py, revisions, verification]
candidate_status: unmeasured
---

# Core modeling workflow

Unmeasured candidate guidance. Use aad_library search for short summaries and get for one exact reference ID. Get workflow.capabilities.v1 for the full map of what AAD can do. Choose purchased parts from the component catalog; see components.real-parts.v1.

## Capture the request

Default to a realistic assembly when the user asks for a real object. Include
its interior, component interfaces, fasteners, support structure and power or
control paths as applicable. An exterior silhouette is sufficient only when
the user asks for an exterior study. Model moving envelopes and service access,
inspect interior/section views and check critical interference. Identify
representative components and unresolved engineering; CAD detail and shape
validity alone do not establish operation or manufacturing readiness.

Translate explicit user requirements into unique, measurable spec entries. Keep assumptions visible and separate; do not silently fill missing fits, tolerances, materials, loads, or process settings. Compare the model with the original request because a complete spec can still omit user intent.

## Model with the API that exists

`from aad import *` re-exports build123d and adds `params()`, `meta()`, `part()`, `feature()`, the `face()`, `edge()` and `vertex()` selectors, gear helpers, assembly(), asset() and the helpers in playbook workflow.capabilities.v1. Plain build123d also works, as below, and lands in one implicit feature. Name features when you expect to edit the part. Assembly helpers are not implemented. The full guide is playbook `aad.guide.v1`.

    from aad import *

    P = params(width=60.0, depth=40.0, thickness=4.0, hole_d=4.4,
               spacing_x=46.0, spacing_y=26.0)

    with BuildPart() as bracket:
        Box(P.width, P.depth, P.thickness)
        with Locations(
            (-P.spacing_x / 2, -P.spacing_y / 2, 0),
            (P.spacing_x / 2, -P.spacing_y / 2, 0),
            (-P.spacing_x / 2, P.spacing_y / 2, 0),
            (P.spacing_x / 2, P.spacing_y / 2, 0),
        ):
            Cylinder(P.hole_d / 2, P.thickness + 2, mode=Mode.SUBTRACT)

    result = bracket.part
    assert result.is_valid
    assert len(result.solids()) == 1

This is a geometry smoke example, not a manufacturing recommendation. Coordinates are plain numbers: project units do not rescale them.

## Iterate and hand off

Create a project with a spec, build, inspect the returned checks and failure location, render views or sections, and compare them with the original prompt. Make a targeted edit and rebuild. Use aad_show for a reviewed revision, then export only with its actual status and checks stated.

The worker does not trust a model-process verified claim, and no independent validator is connected yet, so a successful build is `draft`. Valid B-rep, matching spec values, and a complete-looking model do not establish that the spec captured the user's full intent.

Sources: [build123d 0.13.0 release](https://github.com/gumyr/build123d/releases/tag/v0.13.0), [BuildPart 0.13.0 documentation](https://build123d.readthedocs.io/en/stable/build_part.html).
