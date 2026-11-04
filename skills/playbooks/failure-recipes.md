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
| Empty/ambiguous semantic query | The planned AIAD selector backend is absent here. Do not substitute a raw face index and call it stable. |

A valid B-rep or passing subset does not establish all requested behavior. Current hard-gate incompleteness keeps verification withheld.
