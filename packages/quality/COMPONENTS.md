# Component catalog

`library(kind="component")` lists real purchasable units so agents do not invent parts. Lookups import no geometry kernel. `aad_quality.components.make_envelope` builds simplified fit solids inside a model worker.

```python
library("get", kind="component", id="component.index")     # categories, families, evidence counts
library("search", kind="component", query="M3 socket head")
library("check", items=[{"id": "fastener.iso4762.M3-0.5", "length_mm": 16}])
```

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
