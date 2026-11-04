import json
import math
import subprocess
import sys

import pytest

from aiad_drafting import Sheet2D, sheet2d
from aiad_drafting.constraints import ConstraintError


def constrained_rectangle():
    sheet = sheet2d("constrained")
    sheet.polyline("outline", [(0, 0), (40, 0), (42, 20), (-2, 20)], closed=True)
    sheet.add_constraint("datum", "fixed", ["outline.edge0"])
    sheet.add_constraint("height", "distance", ["outline.edge1"], value=25)
    sheet.add_constraint("horizontal", "parallel", ["outline.edge0", "outline.edge2"])
    sheet.add_constraint("vertical", "parallel", ["outline.edge1", "outline.edge3"])
    sheet.add_constraint("square", "perpendicular", ["outline.edge0", "outline.edge1"])
    return sheet


def test_named_sheet_solves_and_native_face_extrusion_use_the_solution():
    sheet = constrained_rectangle()
    report = sheet.solve()
    assert report.ok and report.dof == 0 and report.rank == 8
    assert set(report.constraints) == {record["name"] for record in sheet.constraints}
    assert sheet.points["outline.p2"].as_tuple() == pytest.approx((40, 25), abs=1e-6)
    assert sheet.to_face().area == pytest.approx(1000, abs=1e-4)
    assert sheet.extrude(3).volume == pytest.approx(3000, abs=1e-4)
    assert sheet.history[-1]["operation"] == "solve"
    assert Sheet2D.from_dict(json.loads(json.dumps(sheet.to_dict()))).solve().dof == 0


def test_fixed_snapshot_survives_json_roundtrip_and_later_coordinate_edits():
    sheet = sheet2d("fixed")
    sheet.circle("hole", (5, 6), 2)
    sheet.add_constraint("keep_hole", "fixed", ["hole"])
    data = sheet.to_dict()
    data["points"]["hole.center"].update(x=15, y=16)
    data["entities"]["hole"]["radius"] = 4
    restored = Sheet2D.from_dict(data)
    report = restored.solve()
    assert report.ok and report.dof == 0
    assert restored.points["hole.center"].as_tuple() == pytest.approx((5, 6))
    assert restored.entities["hole"].radius == pytest.approx(2)


def test_preview_and_conflict_leave_all_live_sheet_data_unchanged():
    sheet = constrained_rectangle()
    before = sheet.to_dict()
    preview = sheet.solve(inplace=False)
    assert preview.ok
    assert sheet.to_dict() == before
    sheet.add_constraint("wrong_width", "distance", ["outline.edge0"], value=50)
    before = sheet.to_dict()
    report = sheet.solve()
    assert not report.ok
    assert report.conflicting_constraints == ["wrong_width"]
    assert sheet.to_dict() == before


def test_shared_named_points_and_circle_radius_changes_propagate():
    sheet = sheet2d("shared")
    sheet.point("A", 0, 0)
    sheet.point("B", 5, 0)
    sheet.line("base", "A", "B")
    sheet.circle("circle", "B", 1)
    sheet.circle("reference", (20, 10), 2)
    sheet.add_constraint("datum", "fixed", ["A"])
    sheet.add_constraint("width", "distance", ["base"], value=10)
    sheet.add_constraint("reference_size", "fixed", ["reference"])
    sheet.add_constraint("same_radius", "equal", ["circle", "reference"])
    result = sheet.solve()
    assert result.ok
    assert math.dist(sheet.points["A"].as_tuple(), sheet.points["B"].as_tuple()) == pytest.approx(10)
    assert sheet.entities["circle"].points[0] == sheet.entities["base"].points[1] == "B"
    assert sheet.entities["circle"].radius == pytest.approx(2)


def test_underconstraint_and_invalid_references_are_named():
    sheet = sheet2d("open")
    sheet.line("edge", (0, 0), (5, 0))
    sheet.add_constraint("width", "distance", ["edge"], value=8)
    report = sheet.solve()
    assert report.ok and report.dof == 3
    assert all(not name.startswith("point:") for name in report.free_variables)
    assert math.dist(*report.points.values()) == pytest.approx(8)
    sheet.arc("curve", (2, 5), 1, 0, 90)
    before = sheet.to_dict()
    with pytest.raises(ConstraintError, match="bad_tangent.*curve"):
        sheet.add_constraint("bad_tangent", "tangent", ["curve", "edge"])
    assert sheet.to_dict() == before


def test_public_model_and_solver_imports_do_not_load_cad_or_scipy():
    subprocess.run([sys.executable, "-c", "from aiad_drafting import Sheet2D, ConstraintSketch; "
                    "import sys; assert not any(n in sys.modules for n in ('build123d', 'OCP', 'scipy'))"],
                   check=True)


def test_structurally_impossible_tangency_and_ambiguous_names_fail_before_solving():
    sheet = sheet2d("invalid")
    sheet.point("A", 0, 0)
    sheet.point("B", 10, 0)
    sheet.line("edge", "A", "B")
    sheet.circle("circle", "B", 2)
    with pytest.raises(ConstraintError, match="endpoint"):
        sheet.add_constraint("impossible", "tangent", ["circle", "edge"])
    assert sheet.constraints == []
    sheet.point("edge", 30, 30)
    with pytest.raises(ConstraintError, match="ambiguous.*edge"):
        sheet.add_constraint("ambiguous", "fixed", ["edge"])
    assert sheet.constraints == []
