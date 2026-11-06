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

`make_envelope` builds the unchanged simplified nominal solid for fit and clearance checks. `make_detailed` is an additive geometry tier beside it. Both build outside an active Builder context. Detailed bearings, connectors and motors are several solids, so use them in `kind="assembly"` projects.

```python
from aad_quality.components import make_envelope
screw = make_envelope("fastener.iso4762.M3-0.5", length_mm=16)
motor = make_envelope("motor.stepper.17HS19-2004S1")
```

```python
from aad_quality.components import make_detailed
bearing = make_detailed("bearing.radial.608", variant="2RS", lod="full")
connector = make_detailed("connector.JST-XH.4pin", lod="fit")
motor = make_detailed("motor.stepper.17HS19-2004S1", lod="full")
screw = make_detailed("fastener.iso4762.M3-0.5", length_mm=16)
```

`make_detailed(id, *, units="mm", lod="full", vendor_step=None,
origin_mm=(0, 0, 0), vendor_evidence=None, contact_count=None,
thread="cosmetic", **selection)` supports deep-groove radial bearings,
connectors with catalog fit dimensions, motors with catalog shaft dimensions,
and socket screws with catalog drive dimensions. Use the record's selection
names (`length_mm` or `length_in`, for example). `lod` is `fit` or `full`; on radial
bearings, `fit` uses eight representative balls and omits the cage/closures,
while `full` uses a dimension-derived count and adds the cage and selected
shields/seals. For connectors, `fit` uses a plain housing with external pin
stubs; `full` cuts contact openings and carries pins through the housing. For
motors, `fit` omits representative mounting holes while `full` adds them when
the pattern fits the sourced body dimensions. LOD changes preserve fit
dimensions. A sourced gearmotor shaft can extend beyond the plain-box envelope
returned by unchanged `make_envelope`. Screws retain their catalog head and
drive at both levels. Unknown or
missing fit dimensions raise a clear error. For a connector without a catalog contact count, pass
`contact_count` only when a representative layout is acceptable; its count,
pitch and pin dimensions are marked representative. A servo or other motor
without catalog output-shaft diameter and length needs a dimensioned vendor
STEP asset rather than guessed shaft dimensions.

The returned shapes are ordinary build123d objects. `make_detailed` records the
catalog evidence unchanged in `meta().aad_component_geometries`, along with
the selected LOD, representative geometry and thread representation. Catalog
envelope dimensions remain catalog sourced. Representative raceways, balls,
cages, contact sections, and mounting-hole dimensions are identified there;
they do not upgrade `evidence.status` or establish ratings, load capacity,
electrical fit or operation.

For vendor geometry, import a supplier STEP with `aad_import`, then refer to
the staged filename. `origin_mm` is the datum coordinate in the supplier file
that should land at model origin. The import evidence must identify a `source`,
`url` or `document`; it is recorded separately from the component catalog's
evidence status:

```python
motor = make_detailed(
    "motor.servo.SG90", vendor_step="sg90.step", origin_mm=(0, 0, 0),
    vendor_evidence={"source": "supplier drawing", "document": "drawing number and revision"},
)
```

Screws have the under-head plane at Z=0 with the shank below it. Motors with shaft data have the mounting face at Z=0 with the shaft above it. In `make_envelope`, gearmotors and servos are plain box outlines. `make_detailed` adds a shaft only when its diameter and length are in the component record; otherwise it raises an error or accepts a dimensioned vendor STEP. Other parts start at Z=0.

Envelopes and detailed geometry are in millimetres. In an inch project, pass `units="in"`. Cut-to-length parts such as shafts, lead screws and extrusions take any `length_mm` from 1 to 6000; check the supplier's maximum. `make_detailed` screw threads use a major-diameter B-rep and a cosmetic thread callout in model metadata and AAD drawings. A helical thread fails the unchanged hard gate's `edge_quality` and `face_quality` checks; do not use or exempt it.

The recorded worker run for 100 full-LOD M3×0.5×8 detailed screws took 75.6 s; the model phase was 0.40 s and 70.8 s was unassigned worker time. See `packages/quality/COMPONENTS.md` and `evals/library/benchmark_detailed_fasteners.py` for scope and reproduction. Treat this as one machine-specific full-worker measurement, not a per-screw geometry estimate.

## Report what the evidence supports

Each record has `evidence.status`:

- `web_checked`: a page naming the part was read on the recorded date. Read any `note`.
- `symbol_library` or `footprint_library`: the part number is in KiCad's library, so it exists. Its specs and outline are still typical.
- `source_table`: dimensions come from the bd_warehouse tables, not a licensed copy of the standard.
- `standard_series`: a commonly stocked series entered from trade knowledge.
- `author_recall`: not independently checked. Hand-entered ratings have been wrong in spot checks, so read the datasheet before relying on them.

No status proves current stock, price, lifecycle or fit for the duty. When the catalog lacks a part you need, say so, name a real candidate you can cite from a datasheet, and mark it unverified in your report. Do not present it as checked. Size motors, drivers, fuses and fasteners from loads you have calculated; a part being real does not make it adequate.
