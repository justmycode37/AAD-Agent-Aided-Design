# Requirement-to-check coverage

This map lets the independent artifact-oracle implementation audit every intent in the corpus. Expected numbers are declared from each public request or direct arithmetic from its dimensions. The builder output is never used to create expected values.

## Request intent map

| Request intent | Check data | Where it appears | Coverage limits |
|---|---|---|---|
| Overall 3D size and connected body count | `bbox_mm.ranges` and `solids.equals` | Part and edit stages; assembly stages | These checks do not prove all local geometry. Every artifact stage also has a feature check. |
| Through/blind bores, bolt patterns, counterbores, pin bores | `cylindrical_bores.bores[]` with unit axis, center, diameter range, depth range, and `through` intent | Mounting plates, brackets, sleeves, flanges, clevises, clamps, enclosures, adapters, pulley | Repeated bores are listed individually. Coaxial counterbore seats are represented as separate shallow blind cylinders at the same axis. |
| Material and void witnesses | `point_material.point_mm` with `solid` or `void` | Channels, brackets, enclosures, clamps, saddles, wheels, heat sinks, and edit invariants | Finite witnesses prove only the listed points. They are not a global manufacturing certificate. |
| Clearance from the declared fan envelope | `clearance_mm.asset_id` and inclusive `range` | `part_fan_carrier_001`, using public asset `fan_envelope` | Measures this one named asset pair only. It does not prove other assembly fit or process access. |
| Importing an existing solid without changing it | Extension `asset_match` compares the output to a declared public asset by symmetric-difference volume and bidirectional surface distance | First stage of `edit_import_mesh_019`, using `fan_envelope.stl` | Bounded until the independent oracle implements clean asset import and comparison. The next edit stage checks the requested envelope, core point, solid count, and added holes. |
| Slot count, centers, length, width, and through direction | Extension `slot_pattern.slots[]` | Guide rails and two slot edit chains | Bounded until the independent oracle implements and validates slot extraction. |
| Linear or radial repeated features | Extensions `linear_pattern` and `rotational_pattern` | Heat-sink fins, cover ribs, wheel lobes, wheel spokes | Checks centers/count, axis, and width or pitch radius. Does not prove the full surface shape of every fin, rib, lobe, or spoke; therefore bounded. |
| Fastener access beside a wall | Extension `fastener_access` | `part_blocked_counterbore_001` | Checks the specified screw approach axis and finite shank/head envelope against surrounding geometry, excluding the intended seated counterbore. It is a bounded access witness, not a general fastener or process certificate. |
| Assembly component count and placement | Extension `component_layout.parts[]` plus `bbox_mm`, `solids`, and component-center witnesses | All 10 assembly tasks | Compares the unordered set of component bounding boxes. It does not establish mating constraints, non-interference, motion, or a bill of materials; therefore bounded. |
| 2D profile layers, closed contours, dimensions, holes, and extents | `dxf_profile.cut_layers`, `contour_count`, `closed_contours`, `circles`, `extents_mm`, and dimensioned `segments` | All 15 profile tasks | Segment endpoints and circles are checked against the stated tolerances. DXF/DXF-like geometry is the intended artifact evidence. |
| Drawing dimensions and required views | `drawing_dimensions.dimensions[]` and `required_views` | All 5 drawing tasks | Checks the named measured values and view set. It does not certify drafting-standard compliance or collision-free sheet layout. |
| Missing information in vague requests | `clarification.missing[]` | All 5 vague tasks | No reference geometry or positive artifact expectation is present. |
| Explicit contradictions or infeasibility | `clarification.conflicts[]` | All 5 infeasible tasks | Conflicts are arithmetic from public dimensions/tolerances. No positive geometry or reference builder is present. |

## Check-type inventory

Counts are check records across all 120 stages; identical IDs intentionally recur between stages only when they represent a retained edit invariant.

| Type | Records | Semantics |
|---|---:|---|
| `bbox_mm` | 90 | Three inclusive ranges for X, Y, and Z overall sizes in millimetres. |
| `solids` | 90 | Exact expected solid count. |
| `point_material` | 192 | One finite material/void sample per check. |
| `cylindrical_bores` | 66 | Explicit bore witnesses; each carries an axis, center, diameter/depth bounds, and through/blind intent. |
| `clearance_mm` | 1 | Minimum gap from the declared fan-envelope asset to the carrier rails, in millimetres. |
| `slot_pattern` | 6 | Extension described above. |
| `linear_pattern` | 8 | Extension described above. |
| `rotational_pattern` | 4 | Extension described above. |
| `fastener_access` | 1 | Extension described above. |
| `asset_match` | 1 | Bounded import comparison to a named public asset. |
| `component_layout` | 10 | Extension described above. |
| `dxf_profile` | 15 | 2D profile evidence. |
| `drawing_dimensions` | 5 | Drawing evidence. |
| `clarification` | 10 | Missing/conflicting request requirements. |

No other check type is accepted by the loader. Unknown evidence is rejected as unsupported rather than silently passing. The strict validator also rejects non-finite values, duplicate IDs/requests, malformed check payloads, unsafe or escaping asset paths, duplicate JSON keys, and preservation IDs that do not identify unchanged checks in the immediately preceding stage.

## Coverage status

All artifact stages are labeled `bounded`. Finite material points, bounds, listed bores and named views do not prove every requested surface, thickness, absence of extra features or geometry-to-dimension association. A well-formed check definition does not establish that the independent oracle implements it. Unsupported checks remain unassessed. The five drawing reference builders explicitly remain unsupported until actual projections and geometry-derived dimensions replace their former placeholders.

All 10 clarification/infeasible stages have a disposition check and no reference factory. No expected geometry is fabricated for them. Reference controls remain separate from all public task inputs except the explicitly declared `fan_envelope.stl` asset.
