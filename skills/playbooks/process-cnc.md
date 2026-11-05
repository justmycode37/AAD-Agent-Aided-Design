---
id: process.cnc_3axis.v1
title: Three-axis CNC
tags: [process, cnc, machining, tool-access, fixture]
candidate_status: unmeasured
---

# Three-axis CNC

Unmeasured candidate guidance. Ask for stock, cutter, axis direction, setup, and fixture assumptions before making access claims.

Favor features reachable from declared tool directions. Leave internal radii compatible with the intended cutter, avoid deep narrow pockets unless the tool and setup are specified, and inspect holes and pocket bottoms for access. A geometry model alone does not describe a holder, vise, clamp, or complete machining sequence.

AAD heuristic screen: cnc_3axis recognizes some coaxial solid-probe approaches at cylindrical openings, a configured tool-axis alignment, and some partial inward-cylinder corner radii. Probe and approach settings are assumptions. The default approach length is finite and does not model a holder or fixture. The evaluator does not prove arbitrary-pocket reachability or collision-free machining.

A pass is not a CAM simulation, setup plan, or machining proof. Confirm critical access with the actual shop or CAM process.

Source: [Protolabs, CNC machining complex features](https://www.protolabs.com/resources/design-tips/mastering-complex-features-on-machined-parts/).
