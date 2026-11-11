---
id: family.thread.v1
title: Threads
tags: [part-family, threads, fasteners, inserts, fit]
candidate_status: unmeasured
---

# Threads

Unmeasured candidate guidance. Clarify whether the request is for clearance, a tapped hole, a cosmetic thread representation, a fully modeled helical thread, or an insert. Record the thread system, nominal size, pitch, hand, engagement, blind/through condition, material, and the mating fastener.

Use the fastener or insert's exact supplier geometry for fit-critical designs. A smooth tapped-hole blank, a cosmetic thread, and a fully modeled thread are different deliverables. Do not silently substitute one for another. Do not choose pitch, clearance, engagement, or print compensation from memory.

The current quality source provides `thread.iso_metric_external` in M3-0.5, M4-0.7, M5-0.8, M6-1, M8-1.25, or M10-1.5, with positive length in mm. It returns a smooth major-diameter envelope and records the thread as cosmetic in metadata. AAD drawings automatically include the designation and say that the thread form is omitted from the B-rep. Metric socket screws made with `make_detailed` use the same cosmetic representation. The pinned bd_warehouse helical sweep produces 10 edges below the hard gate's 0.01 mm minimum and 107 sliver faces for an M3×0.5×8 test screw; `thread='helical'` is therefore rejected. `edge_quality` and `face_quality` remain unchanged. There is no built-in internal thread, tapped-hole, fit, strength, or life proof. See the [pinned bd_warehouse source](https://github.com/gumyr/bd_warehouse/tree/v0.4.0) and check the actual mating fastener.
