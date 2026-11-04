"""Solve a named rectangular sketch with the independent S4 constraints API."""

from __future__ import annotations

import json

from aiad_drafting.constraints import ConstraintSketch


def main() -> None:
    sketch = ConstraintSketch()
    sketch.add_point("A", 0, 0)
    sketch.add_point("B", 40, 0)
    sketch.add_point("C", 43, 26)
    sketch.add_point("D", -2, 24)
    sketch.add_line("bottom", "A", "B")
    sketch.add_line("right", "B", "C")
    sketch.add_line("top", "D", "C")
    sketch.add_line("left", "A", "D")
    sketch.add_constraint("fixed_base", "fixed", ["bottom"])
    sketch.add_constraint("height", "distance", ["right"], value=25)
    sketch.add_constraint("opposite_edges", "parallel", ["bottom", "top"])
    sketch.add_constraint("side_edges", "parallel", ["right", "left"])
    sketch.add_constraint("square_corner", "perpendicular", ["bottom", "right"])

    report = sketch.solve()
    print(json.dumps(report.to_dict(), indent=2))
    if not report.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
