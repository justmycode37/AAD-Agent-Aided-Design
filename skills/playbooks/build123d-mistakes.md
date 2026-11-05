---
id: build123d.mistakes.v1
title: Common build123d mistakes
tags: [build123d, builder-mode, algebra-mode, locations, topology]
candidate_status: unmeasured
---

# Common build123d mistakes

Unmeasured candidate guidance for the pinned build123d 0.13.0 runtime.

- In Builder mode, new objects publish into the active builder when created. Do not expect Cylinder(...).moved(...) afterward to move the geometry already added; place it inside Locations before creation.
- BuildPart is the construction context. Read or assign its result through builder.part after the context, or assign the finished Shape to result.
- Builder coordinates are plain numeric values. AAD units metadata does not convert coordinates.
- Keep algebra operations outside an active Builder context; build123d documents Builder and Algebra as separate construction modes.
- Boolean cutters must intersect the target where intended. A through-cut cylinder may extend beyond both faces, but only the intersecting material is removed; measure the result instead of computing removed volume from cutter length.
- A geometry filter or face/edge index is not a persistent AAD feature reference. Use feature() blocks and semantic face(feature=...) selectors for references that must survive edits.

Sources: [BuildPart 0.13.0 documentation](https://build123d.readthedocs.io/en/stable/build_part.html), [Builder-mode key concepts](https://build123d.readthedocs.io/en/latest/key_concepts_builder.html), and [build123d 0.13.0 release](https://github.com/gumyr/build123d/releases/tag/v0.13.0).
