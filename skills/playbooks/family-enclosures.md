---
id: family.enclosure.v1
title: Enclosures
tags: [part-family, enclosure, housing, clearance, interfaces]
candidate_status: unmeasured
---

# Enclosures

Unmeasured candidate guidance. Ask for the component envelope, connector and cable access, mounting points, lid or service method, sealing needs, thermal limits, material, and process.

Separate the external envelope, cavity, mounting features, interface openings, and closure. Check assembly order and whether fasteners, cables, and tools can reach their targets. Use supplied component STEP files for explicit clearance checks when available; that measurement does not prove sealing, thermal performance, or assembly.

For cast gearboxes, bell housings and organic covers, choose curved cross sections and lofts rather than defaulting to rectangular extrusions. Search `aad_library` for `family.cast_housing.v1` for an executable sequence using the loft, shell, bearing-seat, flange, rib, boss and blend helpers. Playbook `modeling.curved-freeform.v1` covers spline and sweep construction. Match the requested silhouette and inspect sections through the cavity.

Keep local wall transitions, bosses and ribs within supplier guidance. A sampled thin wall can fail a min_wall requirement, but passing samples leave the global minimum unproven, so inspect sections and do not call a wall-thickness screen a global proof. Draft and process checks are geometric heuristics; they do not establish castability or strength.
