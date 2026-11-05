import json
import math

import pytest

from aad_drafting import sheet2d, Sheet2D, latest_sheet
from aad_drafting.import2d import Imported2D, parse_svg
from aad_drafting.model2d import _entity_to_native


def test_exact_bezier_profile_has_measured_area_and_extrusion_history():
    sheet = sheet2d("curved_profile")
    sheet.bezier("crown", [(0, 0), (0, 4), (6, 4), (6, 0)])
    sheet.line("base", (6, 0), (0, 0))
    assert sheet.to_face().area == pytest.approx(14.4)
    solid = sheet.extrude(2)
    assert solid.volume == pytest.approx(28.8)
    assert solid.aad_source_sheet["entities"]["crown"]["kind"] == "bezier"


def test_ellipse_native_area_endpoints_patterns_and_json():
    sheet = sheet2d("conics")
    sheet.ellipse("outer", (0, 0), 10, 5, rotation=30)
    sheet.ellipse("hole", (0, 0), 2, 1, rotation=-15)
    assert sheet.to_face().area == pytest.approx(48 * math.pi)
    sheet.ellipse("arc", (30, 20), 3, 7, rotation=25, start_angle=110, end_angle=10,
                  clockwise=True, layer="score")
    entity = sheet.entities["arc"]
    edge = _entity_to_native(sheet, entity)
    phi = math.radians(25)
    for point, parameter in [(edge.start_point(), 110), (edge.end_point(), 10)]:
        t = math.radians(parameter)
        expected = (30 + 3 * math.cos(t) * math.cos(phi) - 7 * math.sin(t) * math.sin(phi),
                    20 + 3 * math.cos(t) * math.sin(phi) + 7 * math.sin(t) * math.cos(phi))
        assert (point.X, point.Y) == pytest.approx(expected)
    restored = Sheet2D.from_dict(json.loads(json.dumps(sheet.to_dict())))
    assert restored.to_face().area == pytest.approx(sheet.to_face().area)
    patterned = sheet.radial_pattern("arc", count=2, center=(0, 0), angle=90)
    rotated = _entity_to_native(patterned, patterned.entities["arc.radial1"])
    for original, result in [(edge.start_point(), rotated.start_point()), (edge.end_point(), rotated.end_point())]:
        assert (result.X, result.Y) == pytest.approx((-original.Y, original.X))


def test_sheared_svg_arc_native_endpoints_and_closed_ellipse_bounds(tmp_path):
    path = tmp_path / "sheared.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">'
                    '<g transform="matrix(1 .3 .6 1 0 0)"><circle id="c" cx="20" cy="20" r="5"/>'
                    '<path id="a" data-layer="score" d="M25 20 A5 5 0 0 1 20 25"/></g></svg>')
    imported = parse_svg(path)
    sheet = imported.to_sheet()
    circle = _entity_to_native(sheet, sheet.entities["c"])
    assert circle.geom_type.name == "ELLIPSE"
    bbox = sheet.bounding_box()
    assert (bbox.size.X, bbox.size.Y) == pytest.approx((2 * math.sqrt(34), 2 * math.sqrt(27.25)), abs=1e-6)
    assert sheet.to_face().area == pytest.approx(25 * .82 * math.pi)
    arc_record = next(record for record in imported.entities if record["kind"] == "elliptical_arc")
    edge = _entity_to_native(sheet, sheet.entities[arc_record["name"]])
    assert (edge.start_point().X, edge.start_point().Y) == pytest.approx(arc_record["points"][0])
    assert (edge.end_point().X, edge.end_point().Y) == pytest.approx(arc_record["points"][1])


def test_failed_import_and_curve_creation_restore_live_sheet():
    previous = sheet2d("previous")
    imported = Imported2D("mm", [
        {"name": "bad", "kind": "circle", "layer": "cut", "points": [(0, 0)],
         "radius": -1, "metadata": {}}
    ], ["cut"], {})
    with pytest.raises(ValueError):
        imported.to_sheet()
    assert latest_sheet() is previous
    before = previous.to_dict()
    with pytest.raises(ValueError):
        previous.ellipse("bad", (1, 2), 0, 3)
    with pytest.raises(ValueError):
        previous.bezier("bad", [(0, 0), (1, 1), "missing"])
    assert previous.to_dict() == before
