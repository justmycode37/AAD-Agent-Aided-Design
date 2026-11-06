---
id: failure.recipes.v1
title: Failure recipes
tags: [failure, debugging, build, checks, recovery]
candidate_status: unmeasured
---

# Failure recipes

Unmeasured candidate guidance. Use the returned code, message, source line, expected/actual values, and assumptions. Do not invent a cause or claim a repair was tested when it was not.

| Symptom | Next move |
| --- | --- |
| Syntax or runtime failure | Fix the reported model.py line first. Rebuild before changing other geometry. |
| Boolean, fillet, chamfer, shell, or sweep failure | Reduce the operation to the smallest local change. Check cutter placement, overlap, edge selection, and radius against a render or section. Report only parameter values the tool actually tested. |
| Invalid shape or unexpected solids | Inspect the build steps and solid count. Separate construction from cuts and check after each local edit. |
| Requirement fail | Compare expected and measured values. Change geometry only if it misses the original request; never weaken the spec simply to make the model pass. |
| Requirement skip or unavailable suite | Treat it as unknown, find the missing geometry/provider, or tell the user what remains unchecked. A skip is not a pass. |
| Visual mismatch or blocked interface | Inspect orthographic views and a section; compare directly with the original user wording and adjust. |
| Empty/ambiguous semantic query | Read the returned candidates and refine the selector. Do not substitute a raw face index and call it stable. |

A valid B-rep or passing subset does not establish all requested behavior. The worker cannot attest its own result, so verification stays withheld.

## Measured fillet failures on a boolean-heavy housing

The existing `evals/dsg-0b5/model.py` was run read-only with the pinned
build123d/OCCT environment. It built 247 solids in 33.26 s. Its bell housing is
one valid solid with 1,031 faces and 2,400 edges; the shortest edge is
0.036873 mm and the median edge length is 17.699 mm.

- One fillet request for all 2,400 edges failed with `IsDone=False` at both
  0.25 mm (2.34 s) and 1 mm (15.18 s).
- A selected group of 12 edges at least 20 mm long succeeded at 0.25 mm and
  returned a valid shape (0.12 s). One selected edge from that group also
  succeeded at 0.25 mm (0.10 s).
- Testing the eight shortest edges individually exposed distinct failure
  modes. At 0.25 mm, two returned `IsDone=False`, one returned `IsDone=True`
  but an invalid shape, and three raised `Standard_Failure: There are no
  suitable edges for chamfer or fillet`. Other short edges succeeded.
- Results were not monotonic in radius: the 0.104014 mm edge succeeded at
  0.25 mm but returned `IsDone=False` at 0.02 mm. The 0.142076 mm edges failed
  at both tested radii. Each trial used the original, unmodified housing.

This experiment measures the immediate causes: an all-edge request is atomic
and fails when the kernel cannot complete a heterogeneous set; individual edges
may be ineligible, produce a non-completed builder, or produce an invalid result
even when the builder says it completed. Edge length alone and smaller radii do
not reliably predict success. Use explicit groups, validate the result, report
every edge, and treat fallback radii as tested candidates rather than a promise.
