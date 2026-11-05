---
id: family.thread.v1
title: Threads
tags: [part-family, threads, fasteners, inserts, fit]
candidate_status: unmeasured
---

# Threads

Unmeasured candidate guidance. Clarify whether the request is for clearance, a tapped hole, a modeled helical thread, or an insert. Record the thread system, nominal size, pitch, hand, engagement, blind/through condition, material, and the mating fastener.

Use the fastener or insert's exact supplier geometry for fit-critical designs. A smooth tapped-hole blank, a cosmetic thread, and a fully modeled thread are different deliverables. Ask before substituting one for another. Do not choose pitch, clearance, engagement, or print compensation from memory.

The current quality source has one thread record, thread.iso_metric_external: an external right-hand ISO metric helix in M3-0.5, M4-0.7, M5-0.8, M6-1, M8-1.25, or M10-1.5, with positive length in mm and square ends. It returns the helix only, with no shaft or mating hole. Construction uses optional bd_warehouse; aad_library exposes its catalog record, and model workers can call aad_quality.standard_parts.make_part with the optional parts dependency. It is not re-exported by aad. It provides no internal thread, tapped-hole, fit, strength, or life proof. See the [pinned bd_warehouse source](https://github.com/gumyr/bd_warehouse/tree/v0.4.0) and check the actual mating fastener.
