# Shared teardown tasks

These deterministic tool exercises are not agent benchmark results. All adapters receive the exact same source bytes. Use each adapter's actual MCP build/execute and validation/export tools; record unsupported operations and installation failures. Repeat successful task operations three times, preserving raw tool responses and elapsed wall time. Do not treat kernel validity as requirement satisfaction.

1. `plate.py`: 60 × 40 × 6 mm plate, four 4.4 mm through holes on a 46 × 26 mm grid. Export STEP and inspect validity and dimensions.
2. `edit_plate.py`: edit the first design to 80 mm width and 66 mm X spacing. Preserve depth, thickness, hole diameter and count. Record whether the adapter has real editing/history support or requires a full script replacement.
3. `blocked_counterbore.py`: negative control: one kernel-valid bracket with a bridge blocking the coaxial screwdriver/screw approach above its central counterbore. Check whether validity/export gating notices access failure. A validity pass alone is not a false verification unless the system claims requirement compliance.

Checks are independently re-run from exported STEP; a successful tool response is never sufficient evidence.

All scripts include an identical optional `show_object(result)` output hook. AgentCAD captures this call; adapters that select a `result` variable can ignore the missing hook. This is transport plumbing shared by both inputs, not a geometry change.
