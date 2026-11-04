from __future__ import annotations

import json
import math

import pytest

from aiad_drafting.constraints import ConstraintError, ConstraintSketch


def test_coincidence_carries_an_unreferenced_endpoint_without_false_conflict():
    sketch = ConstraintSketch()
    sketch.add_point("A", 1_000_000, 0)
    sketch.add_point("B", 1_000_000.1, 0)
    sketch.add_point("C", 1_000_000.1, 0)
    sketch.add_line("AB", "A", "B")
    sketch.add_constraint("keep_C", "fixed", ["C"])
    sketch.add_constraint("join_A_C", "coincident", ["A", "C"])
    before = {name: (point.x, point.y) for name, point in sketch.points.items()}
    report = sketch.solve(inplace=False)
    assert report.ok and report.conflicting_constraints == []
    assert report.dof == 2 and report.rank == 4
    assert report.points["A"] == pytest.approx(report.points["C"])
    assert math.dist(report.points["A"], report.points["B"]) == pytest.approx(.1)
    assert {name: (point.x, point.y) for name, point in sketch.points.items()} == before


def test_degenerate_local_trial_is_failed_rather_than_an_infeasibility_claim():
    sketch = ConstraintSketch()
    sketch.add_point("A", 1_000_000, 0)
    sketch.add_point("B", 1_000_000.1, 0)
    sketch.add_line("AB", "A", "B")
    sketch.add_constraint("keep_B", "fixed", ["B"])
    sketch.add_constraint("join", "coincident", ["A", "B"])
    report = sketch.solve()
    assert not report.ok and report.status == "failed"
    assert report.conflicting_constraints == []
    assert "nondegenerate" in report.message


def _length(sketch: ConstraintSketch, name: str) -> float:
    line = sketch.lines[name]
    a, b = sketch.points[line.a], sketch.points[line.b]
    return math.hypot(b.x - a.x, b.y - a.y)


def _point_distance(sketch: ConstraintSketch, first: str, second: str) -> float:
    a, b = sketch.points[first], sketch.points[second]
    return math.hypot(b.x - a.x, b.y - a.y)


def test_fully_constrained_rectangle_has_correct_dimensions_and_zero_dof():
    sketch = ConstraintSketch()
    sketch.add_point("A", 0, 0)
    sketch.add_point("B", 4, 0)
    sketch.add_point("C", 5, 3)
    sketch.add_point("D", -1, 3)
    sketch.add_line("bottom", "A", "B")
    sketch.add_line("right", "B", "C")
    sketch.add_line("top", "D", "C")
    sketch.add_line("left", "A", "D")
    sketch.add_constraint("base_datum", "fixed", ["bottom"])
    sketch.add_constraint("height", "distance", ["right"], value=3)
    sketch.add_constraint("opposite_edges", "parallel", ["bottom", "top"])
    sketch.add_constraint("side_edges", "parallel", ["right", "left"])
    sketch.add_constraint("square_corner", "perpendicular", ["bottom", "right"])

    report = sketch.solve()

    assert report.ok
    assert report.status == "solved"
    assert report.rank == 8
    assert report.dof == 0
    assert report.points["A"] == pytest.approx((0, 0), abs=1e-7)
    assert report.points["B"] == pytest.approx((4, 0), abs=1e-7)
    assert report.points["C"] == pytest.approx((4, 3), abs=1e-6)
    assert report.points["D"] == pytest.approx((0, 3), abs=1e-6)
    assert _length(sketch, "bottom") == pytest.approx(4, abs=1e-7)
    assert _length(sketch, "right") == pytest.approx(3, abs=1e-6)
    assert all(item.status == "satisfied" for item in report.constraints.values())


def test_three_side_lengths_retain_three_rigid_motion_degrees_of_freedom():
    sketch = ConstraintSketch()
    sketch.add_point("A", 10, 20)
    sketch.add_point("B", 13, 20)
    sketch.add_point("C", 10, 24)
    sketch.add_constraint("ab", "distance", ["A", "B"], value=3)
    sketch.add_constraint("bc", "distance", ["B", "C"], value=5)
    sketch.add_constraint("ca", "distance", ["C", "A"], value=4)

    report = sketch.solve()

    assert report.ok
    assert report.status == "underconstrained"
    assert report.rank == 3
    assert report.dof == 3
    assert set(report.free_variables) == {
        "A.x", "A.y", "B.x", "B.y", "C.x", "C.y"
    }


def test_single_distance_reports_free_variables():
    sketch = ConstraintSketch()
    sketch.add_point("P", 0, 0)
    sketch.add_point("Q", 1, 0)
    sketch.add_constraint("separation", "distance", ["P", "Q"], value=5)

    report = sketch.solve(inplace=False)

    assert report.ok
    assert report.dof == 3
    assert report.free_variables
    assert _point_distance(sketch, "P", "Q") == pytest.approx(1)
    assert _point_distance_from_report(report.points, "P", "Q") == pytest.approx(5)


def _point_distance_from_report(points, first: str, second: str) -> float:
    a, b = points[first], points[second]
    return math.hypot(b[0] - a[0], b[1] - a[1])


def test_consistent_duplicate_constraint_is_named_as_redundant():
    sketch = ConstraintSketch()
    sketch.add_point("P", 0, 0)
    sketch.add_point("Q", 5, 0)
    sketch.add_constraint("width", "distance", ["P", "Q"], value=5)
    sketch.add_constraint("same_width_again", "distance", ["P", "Q"], value=5)

    report = sketch.solve()

    assert report.ok
    assert report.conflicting_constraints == []
    assert report.redundant_constraints == ["same_width_again"]
    assert report.constraints["width"].status == "satisfied"
    assert report.constraints["same_width_again"].status == "redundant"
    assert report.rank == 1
    assert report.dof == 3


def test_incompatible_named_distances_do_not_mutate_live_geometry():
    sketch = ConstraintSketch()
    sketch.add_point("P", 0, 0)
    sketch.add_point("Q", 5, 0)
    sketch.add_constraint("requested_width", "distance", ["P", "Q"], value=5)
    sketch.add_constraint("incompatible_width", "distance", ["P", "Q"], value=7)
    before = {name: (point.x, point.y) for name, point in sketch.points.items()}

    report = sketch.solve(inplace=True)

    assert not report.ok
    assert report.status == "conflicting"
    assert report.conflicting_constraints == ["incompatible_width"]
    assert report.constraints["requested_width"].status == "satisfied"
    assert report.constraints["requested_width"].residual == pytest.approx(0, abs=1e-7)
    assert report.constraints["incompatible_width"].status == "conflicting"
    assert report.constraints["incompatible_width"].residual == pytest.approx(2, abs=1e-6)
    assert {name: (point.x, point.y) for name, point in sketch.points.items()} == before


def test_angle_and_perpendicular_constraints_use_degrees():
    sketch = ConstraintSketch()
    sketch.add_point("A", 0, 0)
    sketch.add_point("B", 4, 0)
    sketch.add_point("C", 1, 3)
    sketch.add_line("horizontal", "A", "B")
    sketch.add_line("moving", "A", "C")
    sketch.add_constraint("fixed_base", "fixed", ["horizontal"])
    sketch.add_constraint("right_angle", "perpendicular", ["horizontal", "moving"])

    perpendicular = sketch.solve()

    assert perpendicular.ok
    assert perpendicular.constraints["right_angle"].residual == pytest.approx(0, abs=1e-6)
    assert sketch.points["C"].x == pytest.approx(0, abs=1e-6)

    angled = ConstraintSketch()
    angled.add_point("A", 0, 0)
    angled.add_point("B", 4, 0)
    angled.add_point("C", 1, 3)
    angled.add_line("base", "A", "B")
    angled.add_line("diagonal", "A", "C")
    angled.add_constraint("fixed_base", "fixed", ["base"])
    angled.add_constraint("forty_five_degrees", "angle", ["base", "diagonal"], value=45)

    angle_report = angled.solve()

    assert angle_report.ok
    assert angle_report.constraints["forty_five_degrees"].residual == pytest.approx(0, abs=1e-6)


def test_parallel_constraint_accepts_opposite_line_directions():
    sketch = ConstraintSketch()
    sketch.add_point("A", 0, 0)
    sketch.add_point("B", 5, 0)
    sketch.add_point("C", 4, 3)
    sketch.add_point("D", -1, 3)
    sketch.add_line("base", "A", "B")
    sketch.add_line("top_reversed", "C", "D")
    sketch.add_constraint("fixed_base", "fixed", ["base"])
    sketch.add_constraint("parallel_edges", "parallel", ["base", "top_reversed"])

    report = sketch.solve()

    assert report.ok
    assert report.constraints["parallel_edges"].residual == pytest.approx(0, abs=1e-6)


def test_equal_lengths_and_radii():
    sketch = ConstraintSketch()
    sketch.add_point("A", 0, 0)
    sketch.add_point("B", 4, 0)
    sketch.add_point("C", 10, 0)
    sketch.add_point("D", 13, 0)
    sketch.add_line("long", "A", "B")
    sketch.add_line("short", "C", "D")
    sketch.add_constraint("same_length", "equal", ["long", "short"])

    line_report = sketch.solve()

    assert line_report.ok
    assert _length(sketch, "long") == pytest.approx(_length(sketch, "short"), abs=1e-6)

    circles = ConstraintSketch()
    circles.add_point("O1", 0, 0)
    circles.add_point("O2", 10, 0)
    circles.add_circle("large", "O1", 4)
    circles.add_circle("small", "O2", 2)
    circles.add_constraint("same_radius", "equal", ["large", "small"])

    circle_report = circles.solve()

    assert circle_report.ok
    assert circles.circles["large"].radius == pytest.approx(
        circles.circles["small"].radius, abs=1e-6
    )
    assert circles.circles["large"].radius > 0


def test_symmetric_points_about_line_and_center():
    line_sketch = ConstraintSketch()
    line_sketch.add_point("U", 0, -10)
    line_sketch.add_point("V", 0, 10)
    line_sketch.add_point("P", 3, 4)
    line_sketch.add_point("Q", 7, 8)
    line_sketch.add_line("axis", "U", "V")
    line_sketch.add_constraint("fixed_axis", "fixed", ["axis"])
    line_sketch.add_constraint("mirror_pair", "symmetric", ["P", "Q", "axis"])

    line_report = line_sketch.solve()

    assert line_report.ok
    assert line_sketch.points["P"].x == pytest.approx(-line_sketch.points["Q"].x, abs=1e-6)
    assert line_sketch.points["P"].y == pytest.approx(line_sketch.points["Q"].y, abs=1e-6)

    center_sketch = ConstraintSketch()
    center_sketch.add_point("center", 2, 3)
    center_sketch.add_point("P", 0, 0)
    center_sketch.add_point("Q", 8, 5)
    center_sketch.add_constraint("fixed_center", "fixed", ["center"])
    center_sketch.add_constraint("point_mirror", "symmetric", ["P", "Q", "center"])

    center_report = center_sketch.solve()

    assert center_report.ok
    midpoint = (
        (center_sketch.points["P"].x + center_sketch.points["Q"].x) / 2,
        (center_sketch.points["P"].y + center_sketch.points["Q"].y) / 2,
    )
    assert midpoint == pytest.approx((2, 3), abs=1e-6)


def test_circle_line_tangency_and_fixed_circle_radius():
    sketch = ConstraintSketch()
    sketch.add_point("L0", 0, 0)
    sketch.add_point("L1", 10, 0)
    sketch.add_point("center", 2, 3)
    sketch.add_point("reference_center", 20, 20)
    sketch.add_line("axis", "L0", "L1")
    sketch.add_circle("moving", "center", 1)
    sketch.add_circle("reference", "reference_center", 1)
    sketch.add_constraint("fixed_axis", "fixed", ["axis"])
    sketch.add_constraint("fixed_reference", "fixed", ["reference"])
    sketch.add_constraint("same_radius", "equal", ["moving", "reference"])
    sketch.add_constraint("touch_axis", "tangent", ["moving", "axis"])

    report = sketch.solve()

    assert report.ok
    assert report.constraints["touch_axis"].residual == pytest.approx(0, abs=1e-6)
    assert sketch.circles["moving"].radius == pytest.approx(1, abs=1e-7)
    assert sketch.points["center"].y == pytest.approx(1, abs=1e-6)


@pytest.mark.parametrize(
    ("mode", "center_x", "r1", "r2", "expected_distance"),
    [
        ("external", 8, 3, 2, 6),
        ("internal", 5, 3, 1, 2),
    ],
)
def test_circle_circle_external_and_internal_tangency(
    mode: str, center_x: float, r1: float, r2: float, expected_distance: float
):
    sketch = ConstraintSketch()
    sketch.add_point("O1", 0, 0)
    sketch.add_point("O2", center_x, 0)
    sketch.add_circle("first", "O1", r1)
    sketch.add_circle("second", "O2", r2)
    sketch.add_constraint("fixed_first", "fixed", ["first"])
    if mode == "internal":
        sketch.add_point("OR", 20, 20)
        sketch.add_circle("radius_datum", "OR", r2)
        sketch.add_constraint("fixed_radius_datum", "fixed", ["radius_datum"])
        sketch.add_constraint("second_radius", "equal", ["second", "radius_datum"])
    else:
        sketch.add_constraint("equal_radii", "equal", ["first", "second"])
    sketch.add_constraint("touching_circles", "tangent", ["first", "second"], mode=mode)

    report = sketch.solve()

    assert report.ok
    assert report.constraints["touching_circles"].residual == pytest.approx(0, abs=1e-6)
    assert _point_distance(sketch, "O1", "O2") == pytest.approx(expected_distance, abs=1e-6)
    assert sketch.circles["first"].radius > 0
    assert sketch.circles["second"].radius > 0


def test_unknown_unsupported_and_degenerate_inputs_name_the_constraint():
    sketch = ConstraintSketch()
    sketch.add_point("P", 0, 0)
    sketch.add_point("Q", 1, 0)
    sketch.add_line("axis", "P", "Q")

    with pytest.raises(ConstraintError, match="missing_reference.*unknown entity"):
        sketch.add_constraint("missing_reference", "distance", ["P", "absent"], value=2)
    with pytest.raises(ConstraintError, match="unsupported_constraint.*unsupported kind"):
        sketch.add_constraint("unsupported_constraint", "curvature", ["axis"])
    with pytest.raises(ConstraintError, match="bad_distance.*positive"):
        sketch.add_constraint("bad_distance", "distance", ["P", "Q"], value=0)
    with pytest.raises(ConstraintError, match="bad_line_length.*positive"):
        sketch.add_constraint("bad_line_length", "distance", ["axis"], value=0)
    with pytest.raises(ConstraintError, match="bad_parallel.*supported entities"):
        sketch.add_constraint("bad_parallel", "parallel", ["P", "axis"])
    with pytest.raises(ConstraintError, match="unordered_refs.*ordered list or tuple"):
        sketch.add_constraint("unordered_refs", "distance", {"P", "Q"}, value=1)
    with pytest.raises(ValueError, match="zero_line.*degenerate"):
        sketch.add_line("zero_line", "P", "P")
    with pytest.raises(ValueError, match="zero_radius.*positive"):
        sketch.add_circle("zero_radius", "P", 0)
    with pytest.raises(ValueError, match="negative_radius.*positive"):
        sketch.add_circle("negative_radius", "P", -1)

    degenerate = ConstraintSketch()
    degenerate.add_point("A", 0, 0)
    degenerate.add_point("B", 1, 0)
    degenerate.add_line("segment", "A", "B")
    degenerate.add_constraint("segment_length", "distance", ["segment"], value=1)
    degenerate.points["B"].x = 0
    with pytest.raises(ConstraintError, match="segment_length.*degenerate"):
        degenerate.solve()


def test_constraint_parameter_edit_and_json_report_are_safe():
    sketch = ConstraintSketch()
    sketch.add_point("P", 0, 0)
    sketch.add_point("Q", 3, 0)
    sketch.add_constraint("span", "distance", ["P", "Q"], value=3)
    sketch.set_constraint_value("span", 5)

    preview = sketch.solve(inplace=False)

    assert preview.ok
    assert _point_distance_from_report(preview.points, "P", "Q") == pytest.approx(5, abs=1e-6)
    assert _point_distance(sketch, "P", "Q") == pytest.approx(3)
    assert json.loads(json.dumps(preview.to_dict()))["constraints"]["span"]["status"] == "satisfied"

    applied = sketch.solve()
    assert applied.ok
    assert _point_distance(sketch, "P", "Q") == pytest.approx(5, abs=1e-6)


def test_coincident_points_and_fixed_circle_snapshot():
    sketch = ConstraintSketch()
    sketch.add_point("P", 0, 0)
    sketch.add_point("Q", 4, 2)
    sketch.add_point("O", 8, 9)
    sketch.add_circle("C", "O", 3)
    sketch.add_constraint("join", "coincident", ["P", "Q"])
    sketch.add_constraint("lock_circle", "fixed", ["C"])
    sketch.points["O"].x = 50
    sketch.circles["C"].radius = 7

    report = sketch.solve()

    assert report.ok
    assert _point_distance(sketch, "P", "Q") == pytest.approx(0, abs=1e-6)
    assert sketch.points["O"].x == pytest.approx(8)
    assert sketch.points["O"].y == pytest.approx(9)
    assert sketch.circles["C"].radius == pytest.approx(3)
