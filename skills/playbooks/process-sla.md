---
id: process.sla.v1
title: SLA resin printing
tags: [process, sla, resin, orientation, supports, drainage]
candidate_status: unmeasured
---

# SLA resin printing

Unmeasured candidate guidance. Ask which printer, resin, orientation, post-cure, and service conditions apply.

Choose orientation with support contact, unsupported minima, surface finish, enclosed cavities, resin escape, and cleaning access in view. Do not assume that an FDM orientation or overhang rule transfers to resin printing. Check the selected printer's current resin-specific design sheet.

AIAD heuristic screen: sla currently reuses bounded downward-normal and wall-ray screens. The default build direction is +Z and the default downward-slope threshold is 45 degrees; a wall threshold requires min_wall_mm. The screen does not model resin behavior, optimize orientation, check drainage, or evaluate supports.

A pass is only evidence from those finite geometric samples. It does not prove a resin part will drain, clean, cure, remain dimensionally accurate, or survive its load.

Sources: [Formlabs support behavior](https://formlabs.com/global/support/How-supports-work-in-SLA-printing/), [orientation guidance](https://formlabs.com/support/Model-Orientation/), and [hollowing guidance](https://formlabs.com/support/Hollowing-models-in-PreForm/). These are provider-specific references.
