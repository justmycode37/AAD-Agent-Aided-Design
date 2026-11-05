---
id: components.real-parts.v1
title: Use real purchasable parts, invent only the custom ones
tags: [components, parts, bom, screws, fasteners, bearings, electronics, transistor, mosfet, motor, stepper, connector, real, buyable, purchase]
candidate_status: unmeasured
---

# Real parts rule

Design any custom form you need: housings, brackets, linkages, covers. The elemental units inside it must exist. Never make up a screw length, bearing size, motor, transistor or connector.

1. `aad_library` with `action=get`, `kind=component`, `id=component.index` lists every category and family.
2. Search by function and size: `M3 socket head`, `608 bearing`, `logic mosfet`, `nema 17`, `JST XH 4 way`. Results come five at a time; use `next_offset` for more.
3. `get` the record. Use its dimensions, `holes` (clearance and tap drill) and `choices`.
4. Only use values in `choices`. `M3 x 17` is not a stock screw; pick 16 or 20. `qty` must be a positive whole number.
5. Validate the list with `action=check`, or add a `components` requirement to the spec:

```yaml
requirements:
  - id: BOM
    components:
      - {id: fastener.iso4762.M3-0.5, length_mm: 16, qty: 4}
      - {id: bearing.radial.608, variant: 2RS, qty: 2}
      - {id: motor.stepper.17HS19-2004S1, qty: 1}
      - {id: passive.resistor.chip.0603, resistance_ohm: 4700, qty: 2}
      - {custom: printed motor bracket, reason: designed in this project}
```

## Placing parts in the model

`make_envelope` builds a simplified nominal solid for fit and clearance checks. It does not model threads, teeth or pins. Build it outside an active Builder context.

```python
from aad_quality.components import make_envelope
screw = make_envelope("fastener.iso4762.M3-0.5", length_mm=16)
motor = make_envelope("motor.stepper.17HS19-2004S1")
```

Screws have the under-head plane at Z=0 with the shank below it. Motors with shaft data have the mounting face at Z=0 with the shaft above it. Gearmotors and servos are plain box outlines with no shaft, so take the shaft position from their record. Other parts start at Z=0.

Envelopes are in millimetres. In an inch project, pass `units="in"`. Cut-to-length parts such as shafts, lead screws and extrusions take any `length_mm` from 1 to 6000; check the supplier's maximum.

## Report what the evidence supports

Each record has `evidence.status`:

- `web_checked`: a page naming the part was read on the recorded date. Read any `note`.
- `symbol_library` or `footprint_library`: the part number is in KiCad's library, so it exists. Its specs and outline are still typical.
- `source_table`: dimensions come from the bd_warehouse tables, not a licensed copy of the standard.
- `standard_series`: a commonly stocked series entered from trade knowledge.
- `author_recall`: not independently checked. Hand-entered ratings have been wrong in spot checks, so read the datasheet before relying on them.

No status proves current stock, price, lifecycle or fit for the duty. When the catalog lacks a part you need, say so, name a real candidate you can cite from a datasheet, and mark it unverified in your report. Do not present it as checked. Size motors, drivers, fuses and fasteners from loads you have calculated; a part being real does not make it adequate.
