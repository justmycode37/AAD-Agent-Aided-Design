import json
import math
from types import SimpleNamespace

import pytest

from aiad_drafting import sheet2d, checks, measurement_evidence, latest_sheet


def ctx(sheet):
    return SimpleNamespace(sheet=sheet, units=sheet.units, process="laser")


@pytest.mark.parametrize("units,scale", [("mm", 1), ("in", 1 / 25.4)])
def test_rotated_rectangle_and_obround_widths_come_from_native_geometry(units, scale):
    sheet = sheet2d("slots", units=units)
    sheet.rectangle("outer", 100 * scale, 60 * scale, origin=(-50 * scale, -30 * scale))
    phi = math.radians(27)
    def xy(x, y):
        return (scale * (x * math.cos(phi) - y * math.sin(phi)),
                scale * (x * math.sin(phi) + y * math.cos(phi)))
    sheet.polyline("rect", [xy(-20, -1), xy(-10, -1), xy(-10, 1), xy(-20, 1)], closed=True)
    radius = 1.5 * scale
    sheet.line("lower", xy(5, -1.5), xy(15, -1.5))
    sheet.arc("right", xy(15, 0), radius, -90 + 27, 90 + 27)
    sheet.line("upper", xy(15, 1.5), xy(5, 1.5))
    sheet.arc("left", xy(5, 0), radius, 90 + 27, 270 + 27)
    evidence = measurement_evidence(ctx(sheet))
    assert evidence["status"]["complete"]
    assert evidence["unsupported_openings"] == []
    measured = {opening["kind"]: opening for opening in evidence["openings"]}
    assert measured["rectangle"]["width"] == pytest.approx(2 * scale)
    assert measured["obround"]["width"] == pytest.approx(3 * scale)
    assert measured["obround"]["length"] == pytest.approx(13 * scale)
    assert set(measured["obround"]["source_entities"]) == {"lower", "right", "upper", "left"}
    for minimum, status in [(1.9 * scale, "pass"), (2.1 * scale, "fail")]:
        result = {item["id"]: item for item in checks(ctx(sheet), limits={"min_slot": minimum})}
        assert result["2d.laser.min_slot"]["status"] == status
        actual = json.loads(result["2d.laser.min_slot"]["actual"])
        assert len(actual["openings"]) == 2


def test_unknown_cutout_prevents_full_min_slot_pass_but_known_violation_still_fails():
    sheet = sheet2d("unknown")
    sheet.rectangle("outer", 50, 50)
    sheet.polyline("triangle", [(5, 5), (15, 5), (10, 20)], closed=True)
    sheet.circle("hole", (35, 30), 1)
    evidence = measurement_evidence(ctx(sheet))
    assert evidence["status"]["complete"]
    assert len(evidence["unsupported_openings"]) == 1
    for minimum, status in [(1, "skip"), (3, "fail")]:
        result = {item["id"]: item for item in checks(ctx(sheet), limits={"min_slot": minimum})}
        assert result["2d.laser.min_slot"]["status"] == status


def test_exterior_rectangles_are_not_interior_openings():
    sheet = sheet2d("outside")
    sheet.rectangle("outer", 10, 5)
    assert measurement_evidence(ctx(sheet))["openings"] == []
    result = {item["id"]: item for item in checks(ctx(sheet), limits={"min_slot": 20})}
    assert result["2d.laser.min_slot"]["status"] == "skip"


def test_read_only_validation_does_not_replace_the_active_model():
    inspected = sheet2d("inspected")
    inspected.rectangle("outline", 10, 10)
    current = sheet2d("current")
    checks(ctx(inspected))
    measurement_evidence(ctx(inspected))
    assert latest_sheet() is current
