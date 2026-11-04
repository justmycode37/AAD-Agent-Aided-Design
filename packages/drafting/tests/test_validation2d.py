from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aiad_drafting import Entity2D, Sheet2D, sheet2d
from aiad_drafting.validation2d import checks, measurement_evidence


def context(sheet, *, units=None, process=None):
    sheet_units = sheet.get("units") if isinstance(sheet, dict) else sheet.units
    return SimpleNamespace(sheet=sheet, units=units or sheet_units, process=process)


def check_map(ctx, *, limits=None):
    result = checks(ctx, limits=limits)
    assert len({item["id"] for item in result}) == len(result)
    assert all(item["suite"] == "2d" for item in result)
    assert all(item["id"].startswith("2d.") for item in result)
    assert all(item["level"] in {"error", "warn"} for item in result)
    assert all(item["status"] in {"pass", "fail", "warn", "skip"} for item in result)
    return {item["id"]: item for item in result}


def direct_closed_polyline(points, *, name="crossing", units="mm"):
    """Build a deliberately adversarial path that the public helper rejects."""
    sheet = sheet2d(name, units=units)
    point_names = []
    for index, (x, y) in enumerate(points):
        point_name = f"{name}.p{index}"
        sheet.point(point_name, x, y)
        point_names.append(point_name)
    sheet.entities[name] = Entity2D(
        name=name,
        kind="polyline",
        layer="cut",
        points=tuple(point_names),
        closed=True,
    )
    return sheet


def test_validation_import_does_not_load_the_cad_kernel():
    package_src = Path(__file__).parents[1] / "src"
    code = (
        "import sys; import aiad_drafting.validation2d; "
        "assert 'build123d' not in sys.modules; "
        "assert not any(name == 'OCP' or name.startswith('OCP.') for name in sys.modules)"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(package_src)
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_nested_true_circle_contours_report_only_odd_depth_interior_holes():
    sheet = sheet2d("nested")
    sheet.circle("outside", (-30, 0), 2)
    sheet.circle("outer", (0, 0), 20)
    sheet.circle("hole", (0, 0), 5)
    sheet.circle("island", (0, 0), 2)

    evidence = measurement_evidence(context(sheet.to_dict()))
    json.dumps(evidence, allow_nan=False)

    assert evidence["status"]["state"] == "complete"
    assert evidence["units"] == "mm"
    assert evidence["bounds"] == pytest.approx([-32, -20, 0, 20, 20, 0])
    assert evidence["bbox"] == pytest.approx([52, 40, 0])
    assert len(evidence["holes"]) == 1
    hole = evidence["holes"][0]
    assert hole["center"] == pytest.approx([0, 0, 0])
    assert hole["diameter"] == pytest.approx(10)
    assert hole["source_entities"] == ["hole"]
    contours = {item["source_entities"][0]: item for item in evidence["closed_contours"]}
    assert contours["outer"]["nesting_depth"] == 0
    assert contours["hole"]["nesting_depth"] == 1
    assert contours["island"]["nesting_depth"] == 2
    assert contours["outside"]["nesting_depth"] == 0
    assert all(item["valid"] for item in evidence["closed_contours"])


def test_full_circle_composed_of_true_arcs_is_recognized_as_a_hole():
    sheet = sheet2d("arc_hole")
    sheet.rectangle("outline", 30, 30, origin=(-15, -15))
    for index, (start, end) in enumerate(((0, 90), (90, 180), (180, 270), (270, 360))):
        sheet.arc(f"hole_arc_{index}", (0, 0), 3, start, end)

    evidence = measurement_evidence(context(sheet))

    assert evidence["status"]["state"] == "complete"
    assert len(evidence["holes"]) == 1
    assert evidence["holes"][0]["diameter"] == pytest.approx(6)
    assert evidence["holes"][0]["source_entities"] == [
        "hole_arc_0",
        "hole_arc_1",
        "hole_arc_2",
        "hole_arc_3",
    ]
    hole_edges = next(
        contour["edges"]
        for contour in evidence["closed_contours"]
        if contour["source_entities"][0] == "hole_arc_0"
    )
    assert {edge["analytic_type"] for edge in hole_edges} == {"circular_arc"}
    assert {edge["native_type"] for edge in hole_edges} == {"CIRCLE"}


def test_circle_from_two_semicircular_arcs_is_recognized():
    sheet = sheet2d("semicircle_hole")
    sheet.rectangle("outline", 20, 20, origin=(-10, -10))
    sheet.arc("lower", (0, 0), 2, 0, 180)
    sheet.arc("upper", (0, 0), 2, 180, 360)

    evidence = measurement_evidence(context(sheet))
    result = check_map(context(sheet))

    assert evidence["status"]["state"] == "complete"
    assert len(evidence["holes"]) == 1
    assert evidence["holes"][0]["diameter"] == pytest.approx(4)
    assert evidence["holes"][0]["source_entities"] == ["lower", "upper"]
    assert result["2d.self_intersections"]["status"] == "pass"


def test_open_cut_fails_but_open_score_and_text_remain_annotations():
    sheet = sheet2d("open_layers")
    sheet.rectangle("outline", 10, 8)
    sheet.line("open_cut", (1, 1), (2, 1), layer="cut")
    sheet.line("open_score", (1, 2), (2, 2), layer="score")
    sheet.text("label", "not a cutter", (1, 3), 0.5, layer="cut")

    evidence = measurement_evidence(context(sheet))
    result = check_map(context(sheet))

    assert result["2d.closed_contours"]["status"] == "fail"
    assert [path["source_entities"] for path in evidence["open_cut_paths"]] == [["open_cut"]]
    assert [entity["name"] for entity in evidence["annotations"]] == ["label"]
    assert all(entity["name"] != "open_score" for entity in evidence["cut_entities"])
    assert result["2d.self_intersections"]["status"] == "pass"

    score_only = sheet2d("score_only")
    score_only.line("score", (0, 0), (1, 0), layer="score")
    score_checks = check_map(context(score_only))
    assert score_checks["2d.closed_contours"]["status"] == "skip"
    assert score_checks["2d.measurement"]["status"] == "skip"


def test_connected_named_lines_form_one_measured_contour_with_provenance():
    sheet = sheet2d("named_edges")
    sheet.line("bottom", (0, 0), (4, 0))
    sheet.line("right", (4, 0), (4, 3))
    sheet.line("top", (4, 3), (0, 3))
    sheet.line("left", (0, 3), (0, 0))

    evidence = measurement_evidence(context(sheet))
    result = check_map(context(sheet))

    assert result["2d.closed_contours"]["status"] == "pass"
    assert len(evidence["closed_contours"]) == 1
    contour = evidence["closed_contours"][0]
    assert contour["valid"] is True
    assert contour["area"] == pytest.approx(12)
    assert contour["perimeter"] == pytest.approx(14)
    assert contour["source_entities"] == ["bottom", "right", "top", "left"]


@pytest.mark.parametrize(
    "points",
    [
        [(0, 0), (4, 4), (0, 4), (4, 0)],  # zero-area bow-tie
        [(0, 0), (4, 4), (0, 4), (3, 0)],  # crossing with nonzero shoelace area
    ],
)
def test_bow_tie_and_nonzero_area_self_crossings_are_not_proven(points):
    sheet = direct_closed_polyline(points)
    evidence = measurement_evidence(context(sheet))
    result = check_map(context(sheet))

    assert result["2d.self_intersections"]["status"] == "fail"
    assert result["2d.measurement"]["status"] == "fail"
    assert evidence["status"]["state"] == "invalid"
    assert not evidence["holes"]
    assert evidence["closed_contours"]
    assert all(contour["valid"] is False and contour["area"] is None for contour in evidence["closed_contours"])


def test_duplicate_and_partially_overlapping_cut_paths_are_detected():
    duplicate = sheet2d("duplicates")
    duplicate.line("first", (0, 0), (4, 0))
    duplicate.line("duplicate", (4, 0), (0, 0))
    duplicate_checks = check_map(context(duplicate))
    assert duplicate_checks["2d.overlaps"]["status"] == "fail"

    partial = sheet2d("partial_overlap")
    partial.line("first", (0, 0), (5, 0))
    partial.line("partial", (2, 0), (7, 0))
    partial_checks = check_map(context(partial))
    assert partial_checks["2d.overlaps"]["status"] == "fail"
    assert '"relation":"overlap"' in partial_checks["2d.overlaps"]["actual"]


def test_closed_contours_sharing_an_endpoint_are_touching_and_invalid():
    sheet = sheet2d("shared_endpoint")
    sheet.rectangle("first", 4, 4, origin=(0, 0))
    sheet.rectangle("second", 3, 3, origin=(4, 4))

    result = check_map(context(sheet))
    evidence = measurement_evidence(context(sheet))

    assert result["2d.closed_contours"]["status"] == "fail"
    assert result["2d.contour_intersections"]["status"] == "fail"
    assert evidence["status"]["state"] == "invalid"
    assert not evidence["holes"]


@pytest.mark.parametrize(
    ("second_center", "expected_relation"),
    [((4, 0), "touch"), ((3, 0), "cross")],
)
def test_tangent_and_crossing_circles_are_invalid_contour_intersections(second_center, expected_relation):
    sheet = sheet2d("circles")
    sheet.circle("first", (0, 0), 2)
    sheet.circle("second", second_center, 2)

    result = check_map(context(sheet))
    evidence = measurement_evidence(context(sheet))

    assert result["2d.contour_intersections"]["status"] == "fail"
    assert evidence["status"]["state"] == "invalid"
    assert not evidence["holes"]
    assert expected_relation in result["2d.contour_intersections"]["actual"]


def test_millimeters_inches_and_mismatched_context_units_are_explicit():
    inch_sheet = sheet2d("inch", units="in")
    inch_sheet.rectangle("outline", 2, 1)

    evidence = measurement_evidence(context(inch_sheet.to_dict(), units="in"))
    assert evidence["status"]["state"] == "complete"
    assert evidence["units"] == "in"
    assert evidence["bbox"] == pytest.approx([2, 1, 0])

    mismatch = context(inch_sheet, units="mm")
    result = check_map(mismatch)
    invalid_evidence = measurement_evidence(mismatch)
    assert result["2d.input"]["status"] == "fail"
    assert invalid_evidence["status"]["state"] == "invalid"
    assert invalid_evidence["bounds"] is None


def test_invalid_sheet_mapping_is_reported_without_vacuous_pass():
    invalid_mappings = [
        {"units": "mm", "points": {}, "entities": {"missing": {"kind": "line", "points": ["x", "y"]}}},
        {"points": {}, "entities": {}},
    ]
    for bad in invalid_mappings:
        ctx = SimpleNamespace(sheet=bad, units="mm")

        evidence = measurement_evidence(ctx)
        result = check_map(ctx)

        assert evidence["status"]["state"] == "invalid"
        assert evidence["unsupported"] == []
        assert result["2d.input"]["status"] == "fail"
        assert result["2d.closed_contours"]["status"] == "skip"


def test_exact_boundary_clearance_and_thin_gap_kerf_screen_are_reported():
    sheet = sheet2d("thin_gap")
    sheet.rectangle("left", 4, 10, origin=(0, 0))
    sheet.rectangle("right", 4, 10, origin=(4.2, 0))
    ctx = context(sheet, process="laser")

    evidence = measurement_evidence(ctx)
    result = check_map(ctx, limits={"kerf": 0.25, "min_slot": 0.5})

    assert len(evidence["boundary_clearances"]) == 1
    assert evidence["boundary_clearances"][0]["distance"] == pytest.approx(0.2, abs=1e-8)
    assert evidence["boundary_clearances"][0]["method"] == "OCCT BRepExtrema_DistShapeShape"
    assert result["2d.laser.kerf"]["status"] == "fail"
    assert result["2d.laser.kerf"]["heuristic"] is True
    assert result["2d.laser.min_slot"]["status"] == "skip"
    assert "general slot-width proof" in result["2d.laser.kerf"]["assumptions"].lower()


def test_circular_cutout_diameter_is_compared_with_explicit_min_slot():
    sheet = sheet2d("small_hole")
    sheet.rectangle("outline", 10, 10)
    sheet.circle("pilot", (5, 5), 0.4)
    ctx = context(sheet, process="laser")

    result = check_map(ctx, limits={"kerf": 0.1, "min_slot": 1.0})
    opening = result["2d.laser.circular_openings"]

    assert opening["status"] == "fail"
    assert opening["heuristic"] is True
    assert '"diameter":0.8' in opening["actual"]
    assert result["2d.laser.min_slot"]["status"] == "fail"


def test_missing_limits_skip_and_invalid_limits_fail_without_defaults():
    sheet = sheet2d("laser")
    sheet.rectangle("outline", 10, 10)
    ctx = context(sheet, process="laser")

    missing = check_map(ctx)
    assert missing["2d.laser.limits"]["status"] == "skip"
    assert missing["2d.laser.kerf"]["status"] == "skip"
    assert missing["2d.laser.circular_openings"]["status"] == "skip"
    assert missing["2d.laser.min_slot"]["status"] == "skip"

    invalid_limits = [
        None,
        [],
        {"kerf": 0, "min_slot": 1},
        {"kerf": float("inf"), "min_slot": 1},
        {"kerf": float("nan"), "min_slot": 1},
        {"kerf": True, "min_slot": 1},
        {"kerf": "0.2", "min_slot": 1},
        {"kerf": 0.2, "min_slot": -1},
        {"kerf": 0.2, "min_slot": 1, "material": "steel"},
    ]
    for limits in invalid_limits:
        # None is the documented missing-limits case; the other values are invalid.
        result = check_map(ctx, limits=limits)
        if limits is None:
            assert result["2d.laser.limits"]["status"] == "skip"
        else:
            assert result["2d.laser.limits"]["status"] == "fail"
            assert result["2d.laser.kerf"]["status"] == "skip"
            assert result["2d.laser.min_slot"]["status"] == "skip"


def test_partial_limits_skip_only_the_unprovided_measurement():
    sheet = sheet2d("partial_limits")
    sheet.rectangle("outline", 10, 10)
    sheet.circle("hole", (5, 5), 2)
    result = check_map(context(sheet, process="laser"), limits={"kerf": 0.1})

    assert result["2d.laser.limits"]["status"] == "pass"
    assert result["2d.laser.kerf"]["status"] == "pass"
    assert result["2d.laser.circular_openings"]["status"] == "skip"
    assert result["2d.laser.min_slot"]["status"] == "skip"
