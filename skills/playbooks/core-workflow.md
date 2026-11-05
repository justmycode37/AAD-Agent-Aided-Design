---
id: workflow.core.v1
title: Core modeling workflow
tags: [workflow, spec, model.py, revisions, verification]
candidate_status: unmeasured
---

# Core modeling workflow

Unmeasured candidate guidance. Use aiad_library search for short summaries and get for one exact reference ID.

## Capture the request

Translate explicit user requirements into unique, measurable spec entries. Keep assumptions visible and separate; do not silently fill missing fits, tolerances, materials, loads, or process settings. Compare the model with the original request because a complete spec can still omit user intent.

## Model with the API that exists

The current AIAD model namespace adds params() and re-exports build123d. It does not implement the planned AIAD part(), feature(), face(feature=...), edge(feature=...), or assembly() helpers. Ordinary scripts currently produce one implicit main feature. Use build123d and params(); do not copy the planned helper example as executable code.

    from aiad import *

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

Create a project with a spec, build, inspect the returned checks and failure location, render views or sections, and compare them with the original prompt. Make a targeted edit and rebuild. Use aiad_show for a reviewed revision, then export only with its actual status and checks stated.

The current M0 hard gate includes an error-level hard_gate_incomplete result, and the worker does not trust a model-process verified claim. Expect draft output until a trusted complete gate exists. Valid B-rep, matching spec values, and a complete-looking model do not establish that the spec captured the user's full intent.

Sources: [build123d 0.13.0 release](https://github.com/gumyr/build123d/releases/tag/v0.13.0), [BuildPart 0.13.0 documentation](https://build123d.readthedocs.io/en/stable/build_part.html).
