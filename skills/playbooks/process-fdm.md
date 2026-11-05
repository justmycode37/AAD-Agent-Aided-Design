---
id: process.fdm_pla.v1
title: FDM and FFF
tags: [process, fdm, fff, additive, orientation, supports]
candidate_status: unmeasured
---

# FDM and FFF

Unmeasured candidate guidance. Ask for the printer/material context when fit, strength, surface, or duty life matters.

Model around the intended build direction and layer orientation. Check whether loads pull across layers, whether supports touch functional faces, and whether bridges or steep surfaces are acceptable to the chosen printer and slicer. Choose wall and hole dimensions from the actual nozzle, material, settings, and supplier guidance; do not use a universal minimum.

AAD heuristic screen: fdm_pla samples downward-facing surface normals and inward-normal wall rays. Its default build direction is +Z and its default downward-slope threshold is 45 degrees. A wall screen needs min_wall_mm or nozzle_diameter_mm with wall_line_count. These are configured screening assumptions, not printer limits. The screen does not inspect support spans or run a slicer.

A passing screen is not proof of printability, strength, dimensional fit, or service life. Review the chosen orientation and slicer preview.

Source: [Prusa, Modeling with 3D printing in mind](https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135?product=core-one-l).
