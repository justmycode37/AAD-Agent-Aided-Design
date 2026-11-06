# Component catalog

`library(kind="component")` lists real purchasable units so agents do not invent parts. Lookups import no geometry kernel. `aad_quality.components.make_envelope` builds simplified fit solids; the additive `make_detailed` tier builds representative internal/functional geometry for supported families inside a model worker. Neither helper changes the component record's evidence status.

```python
library("get", kind="component", id="component.index")     # categories, families, evidence counts
library("search", kind="component", query="M3 socket head")
library("check", items=[{"id": "fastener.iso4762.M3-0.5", "length_mm": 16}])
```

## Detailed geometry

```python
from aad_quality.components import make_detailed

bearing = make_detailed("bearing.radial.608", variant="2RS", lod="full")
connector = make_detailed("connector.JST-XH.4pin", lod="fit")
motor = make_detailed("motor.stepper.17HS19-2004S1", lod="full")
screw = make_detailed("fastener.iso4762.M3-0.5", length_mm=16)
```

`make_detailed(id, *, units="mm", lod="full", vendor_step=None,
origin_mm=(0, 0, 0), vendor_evidence=None, contact_count=None,
thread="cosmetic", **selection)` is available for radial deep-groove bearings,
connectors with a usable fit envelope, motors with catalog output-shaft
dimensions, and socket screws with catalog head/drive dimensions. `lod` is
`fit` or `full`; on bearings, `fit` builds eight representative balls and
omits the cage and closures, while `full` uses a dimension-derived ball count
and includes them. On connectors, `fit` uses external pin stubs and a plain
housing; `full` cuts contact openings and carries pins through the housing. On
motors, `fit` omits representative mounting holes, while `full` adds them when
they fit the sourced body dimensions. LOD changes do not change a part's fit
dimensions. Where the unchanged `make_envelope` is a plain motor box, a sourced
gear-motor shaft in `make_detailed` extends beyond that body envelope.
Screws keep their catalog head and drive details at either level. Inch fasteners
use the record's `length_in` selection; metric and cut-to-length entries use
their listed selection name. Unknown or missing fit dimensions raise an error.
If a connector record has no contact count, a caller may pass `contact_count`
for a representative layout; missing contact dimensions otherwise require a
vendor STEP. Output-shaft dimensions missing from a motor record also require
vendor STEP. Non-radial bearing internals are not substituted with ball-bearing
geometry.

The fit envelope dimensions come from the catalog. Any other feature, such as
bearing raceways, ball size/count, connector contact cross section, or mounting
hole diameter/depth, is marked representative in model metadata. Metadata
records a copy of the catalog `evidence` and leaves `evidence.status` unchanged.
`lod` reduces representative-detail cost for bearings, connectors, and motors;
it does not claim manufacturing accuracy or performance.

Import a supplier STEP with `aad_import`, then reference the staged filename:

```python
part = make_detailed(
    "motor.servo.SG90", vendor_step="servo.step", origin_mm=(0, 0, 0),
    vendor_evidence={"source": "supplier drawing", "document": "drawing number/revision"},
)
```

`origin_mm` is the source STEP datum translated to model origin. `vendor_evidence`
must name a `source`, `url`, or `document` and is recorded separately from the
catalog status. Never substitute it for the catalog record's `evidence.status`.
The original `make_envelope` behavior is unchanged.

The full helical screw form fails the fixed hard gate: an M3×0.5×8 sweep has
10 sub-threshold edges and 107 sliver faces. `make_detailed` uses a cosmetic
thread. Metadata records its designation, and AAD drawings add a note that the
thread form is omitted from the B-rep. `thread="helical"` is rejected; no hard
gate check is weakened or exempted.

Cost measurement: `evals/library/benchmark_detailed_fasteners.py` builds 100
M3×0.5×8 detailed screws at full LOD through `run_build`. One run took 75.6 s,
produced 100 solids / 1,200 faces / 2,400 edges, and passed every error-level
hard check. The model phase took 0.40 s and mesh validation 3.32 s; 70.8 s was
reported in the worker's unassigned `other` phase. This is a single, machine-
specific full-worker result, not a per-part geometry estimate. Run the script
for local timings.

## Sources and what they establish

| Status | Source | Establishes |
|---|---|---|
| `web_checked` | `evals/library/web_evidence.json`, a page read for that part | The part was listed on the recorded date. Notes record conflicts. |
| `symbol_library` | KiCad symbols 9.0.0 | The part number exists. Package outlines are typical, not from its datasheet. |
| `footprint_library` | KiCad footprints | The part number or series exists. |
| `source_table` | bd_warehouse 0.4.0 tables | Dimensions as tabulated upstream, not checked against paid standards. |
| `standard_series` | Hand-entered | A commonly stocked series. Sizes need supplier confirmation. |
| `author_recall` | Hand-entered | Nothing independent. |

Spot checks found wrong hand-entered values, such as a switch's current rating and a motor's height. Those were corrected, but the same error rate should be assumed for unchecked `author_recall` specifications. Inch screw lengths are a hand-entered series because the upstream ranges start implausibly long. The parallel key envelope length of five times the key width is also a hand-entered placeholder, not a stocked size.

A valid catalog check compares a declared list with these records. It does not inspect the geometry, prove stock, price or lifecycle, or size a part for its load.

## Regenerating

```sh
.venv/bin/python evals/library/build_component_tables.py   # hardware tables from bd_warehouse
PYTHONPATH=packages/quality/src .venv/bin/python evals/library/check_components.py kicad-symbols-9.0.0 footprints.json
```

The second script's docstring gives the download commands. It rewrites `component_evidence.json`, lists part numbers no library matched, and compares the hand-entered radial bearings with the source table.

To add a part, add it to `components/mechanical.py`, `electronics.py` or `electromechanical.py`, then rerun the check. Add a web entry only after reading a page for that exact part number.
