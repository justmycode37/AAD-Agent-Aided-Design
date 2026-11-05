---
id: process.sheet_metal.v1
title: Sheet metal
tags: [process, sheet-metal, bends, relief, flat-pattern]
candidate_status: unmeasured
---

# Sheet metal

Unmeasured candidate guidance. Get the material, stock thickness, bend method, inside radius, and supplier rules before assigning dimensions.

Start from a uniform sheet thickness. Plan bend lines, flange lengths, corner relief, hole and slot locations near bends, and the required flat pattern. Material, thickness, tooling, and bend method affect bend allowance, springback, and achievable features. Do not copy one fabricator's limits as universal rules.

AAD heuristic screen: sheet_metal recognizes a narrow case of coaxial partial-cylinder pairs. It needs stock_thickness_mm and minimum_inside_bend_radius_mm. It does not validate a flat pattern, relief, forming tools, springback, material behavior, or general sheet-metal topology.

A pass applies only to the recognized cylinder pairs and declared assumptions; it is not proof that the part can be formed or will match the flat pattern.

Source: [Protolabs, sheet-metal bend-radius guidance](https://www.protolabs.com/resources/design-tips/the-basics-of-bend-radii-in-sheet-metal/).
