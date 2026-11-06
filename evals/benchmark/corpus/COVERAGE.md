# Requirement-to-check coverage

This map lets the independent artifact-oracle implementation audit every intent in the corpus. Expected numbers are declared from each public request or direct arithmetic from its dimensions. The builder output is never used to create expected values.

## Request intent map

| Request intent | Check data | Where it appears | Coverage limits |
|---|---|---|---|
| Overall 3D size and connected body count | `bbox_mm.ranges` and `solids.equals` | Part and edit stages; assembly stages | These checks do not prove all local geometry. Every artifact stage also has a feature check. |
| Through/blind bores, bolt patterns, counterbores, pin bores | `cylindrical_bores.bores[]` with unit axis, center, diameter range, depth range, and `through` intent | Mounting plates, brackets, sleeves, flanges, clevises, clamps, enclosures, adapters, pulley | Repeated bores are listed individually. Coaxial counterbore seats are represented as separate shallow blind cylinders at the same axis. `xy_translation_invariant` shifts expected XY centers to the BRep bounding-box center when the request leaves XY placement open. |
| Material and void witnesses | `point_material.point_mm` plus optional `additional_points_mm`, with `solid` or `void` | Channels, brackets, enclosures, clamps, saddles, wheels, heat sinks, and edit invariants | Multiple samples can distinguish an enclosure cavity from a pinhole. Finite witnesses still prove only the listed points; they are not a global manufacturing certificate. `xy_translation_invariant` shifts XY samples to the BRep bounding-box center when the request leaves XY placement open. |
| Clearance from the declared fan envelope | `clearance_mm.asset_id` and inclusive `range` | `part_fan_carrier_001`, using public asset `fan_envelope` | Measures this one named asset pair only. It does not prove other assembly fit or process access. |
| Importing an existing solid without changing it | Extension `asset_match` compares the output to a declared public asset by symmetric-difference volume and bidirectional surface distance | First stage of `edit_import_mesh_019`, using `fan_envelope.stl` | Bounded until the independent oracle implements clean asset import and comparison. The next edit stage checks the requested envelope, core point, solid count, and added holes. |
| Slot count, centers, length, width, and through direction | Extension `slot_pattern.slots[]` | Guide rails and two slot edit chains | Bounded until the independent oracle implements and validates slot extraction. |
| Linear or radial repeated features | Extensions `linear_pattern` and `rotational_pattern` | Heat-sink fins, cover ribs, wheel lobes, wheel spokes | Selected heat-sink checks accept either in-plane array axis and search several scan lines; rotational checks measure count and equal angular spacing without fixing phase. `xy_translation_invariant` shifts the pattern center and feature witnesses to the BRep bounding-box center when the request leaves XY placement open. An optional tip-radius witness checks lobes at measured feature angles. These do not prove the full surface shape of every feature; therefore bounded. |
| Fastener access beside a wall | Extension `fastener_access` | `part_blocked_counterbore_001` | Checks the specified screw approach axis and finite shank/head envelope against surrounding geometry, excluding the intended seated counterbore. It is a bounded access witness, not a general fastener or process certificate. |
| Assembly component count and placement | Extension `component_layout.parts[]` plus `bbox_mm`, `solids`, and component-center witnesses | All 10 assembly tasks | Compares the unordered set of component bounding boxes. It does not establish mating constraints, non-interference, motion, or a bill of materials; therefore bounded. |
| 2D profile layers, closed contours, dimensions, holes, and extents | `dxf_profile.cut_layers`, `contour_count`, `closed_contours`, `circles`, `circle_patterns`, `regular_polygon`, `extents_mm`, and dimensioned `segments` | All 15 profile tasks | Segment endpoints and circle centers are checked against request tolerances. Selected regular-hexagon and bolt-circle checks ignore angular phase. The pilot reads native Sheet2D JSON; it does not certify exported DXF compatibility. |
| Drawing dimensions and required views | `drawing_dimensions.dimensions[]` and `required_views` | All 5 drawing tasks | The selected pilot mappings compare BRep-derived dimensions and projected views with SVG geometry and labels. This does not certify drafting-standard compliance or collision-free sheet layout. |
| Missing information in vague requests | `clarification.missing[]` | All 5 vague tasks | Natural-language meaning and negation are not independently audited. These checks remain unsupported until a reviewed semantic oracle exists. |
| Explicit contradictions or infeasibility | `clarification.conflicts[]` | All 5 infeasible tasks | Public dimensions show arithmetic conflicts, but response polarity is not independently audited. These checks remain unsupported until a reviewed semantic oracle exists. |

## Check-type inventory

Counts are check records across all 120 stages; identical IDs intentionally recur between stages only when they represent a retained edit invariant.

| Type | Records | Semantics |
|---|---:|---|
| `bbox_mm` | 90 | Three inclusive ranges for X, Y, and Z overall sizes in millimetres. |
| `solids` | 90 | Exact expected solid count. |
| `point_material` | 188 | One or more finite material/void samples per check. |
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

## Pilot oracle implementation

The frozen 20-task pilot has 94 checks across 24 stages. Its selected artifact checks have implementation and negative-mutant controls for these types:

The spoked-wheel request leaves XY placement open. Its point, bore and spoke checks set `xy_translation_invariant: true`; the oracle shifts those XY witnesses to the measured BRep bounding-box center and keeps the requested Z plane fixed. The other 19 selected tasks state their coordinate frame or placement where their checks use absolute coordinates.

| Type | Selected checks | Pilot implementation |
|---|---:|---|
| `bbox_mm` | 16 | BRep bounds in millimetres |
| `solids` | 16 | BRep solid count |
| `point_material` | 36 | Solid/void samples from the saved BRep, including five-point enclosure cavity witnesses |
| `cylindrical_bores` | 11 | Explicit axis, position, diameter/depth and through/blind witnesses |
| `linear_pattern` | 2 | Equally spaced fin count/width witnesses independent of in-plane array axis |
| `rotational_pattern` | 2 | Phase-free count/equal-spacing checks; lobe tips are sampled at measured feature angles |
| `component_layout` | 3 | Unordered component bounding-box and center witnesses |
| `dxf_profile` | 3 | Native Sheet2D JSON with request-tolerance circle centers and phase-free regular-hex/bolt-circle checks |
| `drawing_dimensions` | 2 | BRep-derived requested dimensions and SVG projected-view checks |
| `clarification` | 3 | Unsupported pending audited response-meaning and conflict-polarity checks |

Of these 94 checks, 91 artifact checks remain bounded and three clarification/infeasible checks are unsupported. Negated topic lists and responses that assert an infeasible fit remain indeterminate; keyword presence never establishes a complete pass. Wrong BRep, Sheet2D JSON, and SVG mutants reject supported artifact checks. A bounded positive `part_plate_001` reference control passes its witnesses but remains indeterminate. Rotated reference controls cover the two selected wheel patterns, the alternate heat-sink fin axis, and the phase-free hex flange.

The implementation is scoped to this selection. `clearance_mm`, `slot_pattern`, `fastener_access`, `asset_match`, and unreviewed task-specific mappings remain unsupported; any future use stays unsupported until independently implemented and tested. The report never counts an unsupported check as a pass. The drawing oracle is implemented for the two selected requests, but the corpus still has no complete positive drawing-reference control, and the other three drawing mappings remain out of pilot scope.

## Coverage status

All artifact stages are labeled `bounded`. Finite material points, bounds, listed bores and named views do not prove every requested surface, thickness, absence of extra features or geometry-to-dimension association. Bore and exact-circle checks require request-sourced center tolerances; missing tolerances are unsupported. The selected enclosure cavity uses multiple points and rejects a center pinhole. A well-formed check definition does not establish that the independent oracle implements it. Unsupported checks remain unassessed. The pilot's two selected drawing oracles inspect actual projections and geometry-derived dimensions; they do not replace the missing positive drawing-reference controls for the full corpus.

All 10 clarification/infeasible stages have a disposition check and no reference factory. No expected geometry is fabricated for them. Reference controls remain separate from all public task inputs except the explicitly declared `fan_envelope.stl` asset.
