from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from aiad_drafting.import2d import Imported2D, Import2DError, parse_dxf, parse_svg


DATA = Path(__file__).parent / "data"


def _entity(imported: Imported2D, name: str) -> dict:
    return next(entity for entity in imported.entities if entity["name"] == name)


def _single(imported: Imported2D, kind: str) -> dict:
    return next(entity for entity in imported.entities if entity["kind"] == kind)


def _assert_points(actual, expected):
    assert [coordinate for point in actual for coordinate in point] == pytest.approx(
        [coordinate for point in expected for coordinate in point]
    )


def test_dxf_declared_units_are_kept_and_explicit_output_units_convert(tmp_path):
    millimeters = parse_dxf(DATA / "units-mm.dxf")
    inches = parse_dxf(DATA / "units-in.dxf")
    inches_as_mm = parse_dxf(DATA / "units-in.dxf", units="mm")

    assert millimeters.units == "mm"
    assert _entity(millimeters, "line:31")["points"][1] == pytest.approx((25.4, 0.0))
    assert inches.units == "in"
    assert _entity(inches, "line:31")["points"][1] == pytest.approx((1.0, 0.0))
    assert inches_as_mm.units == "mm"
    assert _entity(inches_as_mm, "line:31")["points"][1] == pytest.approx((25.4, 0.0))
    circle = _entity(inches_as_mm, "circle:32")
    assert circle["points"][0] == pytest.approx((50.8, 76.2))
    assert circle["radius"] == pytest.approx(12.7)


def test_unitless_dxf_needs_an_explicit_interpretation():
    with pytest.raises(Import2DError, match="unitless.*pass units"):
        parse_dxf(DATA / "unitless.dxf")

    interpreted = parse_dxf(DATA / "unitless.dxf", units="in")
    assert interpreted.units == "in"
    _assert_points(_single(interpreted, "line")["points"], ((0.0, 0.0), (1.0, 2.0)))
    assert "interpreted as in" in interpreted.warnings[0]


def test_dxf_ocs_reflection_preserves_circle_centers_arc_sweep_and_handles():
    imported = parse_dxf(DATA / "reflected-ocs.dxf")
    circle = _single(imported, "circle")
    reflected_arc = next(entity for entity in imported.entities if entity["kind"] == "arc" and not entity["metadata"]["ccw"])
    rotated_arc = next(entity for entity in imported.entities if entity["kind"] == "arc" and entity["metadata"]["ccw"])

    assert circle["points"][0] == pytest.approx((-10.0, 20.0))
    assert circle["radius"] == pytest.approx(5.0)
    assert circle["metadata"]["extrusion"] == (0.0, 0.0, -1.0)
    assert circle["metadata"]["handle"]
    assert reflected_arc["points"][0] == pytest.approx((-3.0, 4.0))
    assert reflected_arc["start_angle"] == pytest.approx(150.0)
    assert reflected_arc["end_angle"] == pytest.approx(60.0)
    assert reflected_arc["metadata"]["ccw"] is False
    assert rotated_arc["points"][0] == pytest.approx((-4.0, 6.0))
    assert rotated_arc["start_angle"] == pytest.approx(20.0)
    assert rotated_arc["end_angle"] == pytest.approx(140.0)


def test_dxf_lwpolyline_keeps_closed_state_bulges_and_reflection():
    imported = parse_dxf(DATA / "bulge.dxf")
    polyline = _single(imported, "polyline")

    assert polyline["closed"] is True
    _assert_points(polyline["points"], ((0, 0), (-10, 0), (-10, 10), (0, 10)))
    assert polyline["metadata"]["source_bulges"] == pytest.approx([0, 1, -0.5, 0])
    assert polyline["metadata"]["bulges"] == pytest.approx([0, -1, 0.5, 0])
    assert len(polyline["points"]) == 4


def test_dxf_audit_errors_and_malformed_input_are_rejected():
    with pytest.raises(Import2DError, match="DXF audit found errors"):
        parse_dxf(DATA / "audit-error.dxf")
    with pytest.raises(Import2DError, match="Could not read DXF"):
        parse_dxf(DATA / "malformed.dxf")


def test_dxf_nonplanar_and_unsupported_entities_fail_with_entity_context():
    with pytest.raises(Import2DError, match="different start and end z"):
        parse_dxf(DATA / "nonplanar.dxf")
    with pytest.raises(Import2DError, match="tilted extrusion normal"):
        parse_dxf(DATA / "tilted-ocs.dxf")
    with pytest.raises(Import2DError, match="3DFACE.*unsupported"):
        parse_dxf(DATA / "unsupported.dxf")


def test_dxf_text_is_data_and_provenance_round_trips_as_json():
    imported = parse_dxf(DATA / "units-mm.dxf")
    text = _single(imported, "text")

    assert text["text"] == "A & <instruction>"
    assert text["height"] == pytest.approx(2.0)
    assert text["metadata"]["handle"]
    assert text["metadata"]["dxf_type"] == "TEXT"
    assert text["metadata"]["source_layer"] == "SCORE"
    assert imported.source["sha256"]
    assert imported.source["audit"] == {"errors": 0, "repairs": 0}

    serialized = json.loads(json.dumps(imported.to_dict()))
    restored = Imported2D.from_dict(serialized)
    assert restored.to_dict() == imported.to_dict()
    assert isinstance(restored.entities[0]["points"], tuple)


def test_svg_viewbox_layers_nested_transform_and_y_axis_conversion():
    imported = parse_svg(DATA / "layers-transform.svg")
    line = next(entity for entity in imported.entities if entity["metadata"]["source_id"] == "base-line")
    slot = _entity(imported, "slot")
    hole = _entity(imported, "hole")

    assert imported.units == "mm"
    assert imported.layers == ["cut", "score"]
    _assert_points(line["points"], ((10.0, 40.0), (20.0, 40.0)))
    assert slot["kind"] == "polyline"
    _assert_points(slot["points"], ((30.0, 40.0), (30.0, 30.0), (25.0, 30.0)))
    assert slot["metadata"]["source_group_ids"] == ["layer-cut", "rotated-group"]
    assert hole["points"][0] == pytest.approx((70.0, 30.0))
    assert hole["radius"] == pytest.approx(5.0)
    assert {layer["source_id"] for layer in imported.source["layers"] if "source_id" in layer} == {
        "layer-cut", "layer-score"
    }


def test_svg_physical_scale_converts_to_requested_inches():
    imported = parse_svg(DATA / "layers-transform.svg", units="in")
    line = next(entity for entity in imported.entities if entity["metadata"]["source_id"] == "base-line")
    hole = _entity(imported, "hole")

    assert imported.units == "in"
    _assert_points(line["points"], ((10 / 25.4, 40 / 25.4), (20 / 25.4, 40 / 25.4)))
    assert hole["radius"] == pytest.approx(5 / 25.4)
    assert imported.source["units"]["scale_rule"].startswith("SVG/CSS px are 1/96 inch")


def test_svg_path_commands_and_elliptic_geometry_remain_exact_records():
    imported = parse_svg(DATA / "exact-curves.svg")
    cubic = _entity(imported, "exact-curves.s000")
    quadratic = _entity(imported, "exact-curves.s001")
    ellipse_arc = _entity(imported, "exact-curves.s002")
    close_line = _entity(imported, "exact-curves.s003")
    ellipse = _entity(imported, "elliptical-hole")
    hv_lines = [
        entity for entity in imported.entities
        if entity["name"].startswith("horizontal-vertical.")
    ]
    closed_subpaths = [
        entity for entity in imported.entities
        if entity["name"].startswith("closed-subpaths.")
    ]
    circular_arc = _entity(imported, "circular-arc.s000")

    assert cubic["kind"] == "cubic_bezier"
    _assert_points(cubic["points"], ((10, 70), (12, 65), (18, 65), (20, 70)))
    _assert_points(cubic["metadata"]["control_points"], ((12, 65), (18, 65)))
    assert quadratic["kind"] == "quadratic_bezier"
    _assert_points(quadratic["points"], ((20, 70), (24, 74), (28, 70)))
    assert ellipse_arc["kind"] == "elliptical_arc"
    assert ellipse_arc["metadata"]["source_path_data"].startswith("M 10 10 C")
    assert ellipse_arc["metadata"]["ellipse"]["rx"] != pytest.approx(
        ellipse_arc["metadata"]["ellipse"]["ry"]
    )
    assert close_line["closed"] is True
    assert ellipse["kind"] == "ellipse"
    assert ellipse["closed"] is True
    assert ellipse["metadata"]["ellipse"]["center"] == pytest.approx((60, 50))
    assert ellipse["metadata"]["ellipse"]["rotation"] == pytest.approx(-25.0)
    assert len(hv_lines) == 4
    assert all(entity["kind"] == "line" for entity in hv_lines)
    assert not any(entity["kind"] == "polyline" and entity["name"].startswith("exact-curves") for entity in imported.entities)
    assert [entity["metadata"]["subpath_index"] for entity in closed_subpaths] == [0, 0, 1, 1]
    assert all(entity["metadata"]["subpath_closed"] for entity in closed_subpaths)
    assert circular_arc["kind"] == "arc"
    assert circular_arc["radius"] == pytest.approx(5.0)
    assert circular_arc["metadata"]["ccw"] is False
    assert circular_arc["metadata"]["ellipse"]["sweep"] < 0


def test_svg_text_is_escaped_data_with_id_and_group_provenance():
    imported = parse_svg(DATA / "exact-curves.svg")
    label = _entity(imported, "label")

    assert label["kind"] == "text"
    assert label["text"] == "A & B <data, not instructions>"
    assert label["height"] == pytest.approx(3.0)
    assert label["metadata"]["source_id"] == "label"
    assert label["metadata"]["source_group_ids"] == ["engrave-layer"]
    assert label["metadata"]["rotation"] == pytest.approx(0.0)


def test_svg_layer_map_and_json_round_trip_keep_source_ids():
    imported = parse_svg(DATA / "layers-transform.svg", layer_map={"Score": "Engraving"})
    hole = _entity(imported, "hole")

    assert hole["layer"] == "engrave"
    assert "engrave" in imported.layers
    restored = Imported2D.from_dict(json.loads(json.dumps(imported.to_dict())))
    assert restored.to_dict() == imported.to_dict()
    assert restored.source["layers"] == imported.source["layers"]


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("unknown-scale.svg", "no physical width or height"),
        ("percentage-scale.svg", "percentage units"),
        ("malformed.svg", "Could not parse SVG"),
    ],
)
def test_svg_unknown_scale_and_malformed_xml_are_rejected(filename, message):
    with pytest.raises(Import2DError, match=message):
        parse_svg(DATA / filename)


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("unsafe-script.svg", "active or external"),
        ("unsafe-external.svg", "active or external"),
        ("unsafe-href.svg", "load external content"),
        ("unsafe-entity.svg", "DTD"),
        ("unsupported-use.svg", "unsupported"),
        ("hidden.svg", "Hidden SVG geometry"),
    ],
)
def test_svg_active_external_and_unsupported_constructs_are_rejected(filename, message):
    with pytest.raises(Import2DError, match=message):
        parse_svg(DATA / filename)


def test_svg_rejects_non_utf8_xml_before_entity_expansion(tmp_path):
    path = tmp_path / "utf16-entity.svg"
    path.write_bytes(
        '<!DOCTYPE svg [<!ENTITY geometry "unsafe">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"/>'
        .encode("utf-16")
    )

    with pytest.raises(Import2DError, match="UTF-8 encoding"):
        parse_svg(path)


def test_live_sheet_preserves_exact_svg_beziers_and_ellipses():
    imported = parse_svg(DATA / "exact-curves.svg")
    sheet = imported.to_sheet("curves")
    from aiad_drafting.model2d import _entity_to_native, Sheet2D
    cubic = _entity_to_native(sheet, sheet.entities["exact-curves.s000"])
    assert cubic.geom_type.name == "BEZIER"
    assert (cubic.position_at(.5).X, cubic.position_at(.5).Y) == pytest.approx((15, 66.25))
    ellipse = _entity_to_native(sheet, sheet.entities["elliptical-hole"])
    assert ellipse.geom_type.name == "ELLIPSE" and ellipse.is_closed
    restored = Sheet2D.from_dict(json.loads(json.dumps(sheet.to_dict())))
    for name in ["exact-curves.s000", "exact-curves.s001", "exact-curves.s002", "elliptical-hole"]:
        assert _entity_to_native(sheet, sheet.entities[name]).length == pytest.approx(
            _entity_to_native(restored, restored.entities[name]).length)


def test_live_sheet_preserves_dxf_bulges_as_analytic_arcs():
    imported = parse_dxf(DATA / "bulge.dxf")
    sheet = imported.to_sheet()
    edges = list(sheet.to_shape().edges())
    assert len(edges) == 4
    assert [edge.geom_type.name for edge in edges].count("CIRCLE") == 2
    expected = [10, 5 * math.pi, 6.25 * 4 * math.atan(.5), 10]
    assert [edge.length for edge in edges] == pytest.approx(expected)
    assert sheet.history[-1]["source"] == imported.source
    assert all(entity.metadata["handle"] for entity in sheet.entities.values())


def test_live_sheet_preserves_clockwise_reflected_arc_endpoints_and_sweep():
    imported = parse_dxf(DATA / "reflected-ocs.dxf")
    sheet = imported.to_sheet()
    native = sheet.to_shape()
    record = next(entity for entity in imported.entities if entity["kind"] == "arc" and not entity["metadata"]["ccw"])
    from aiad_drafting.model2d import _entity_to_native
    edge = _entity_to_native(sheet, sheet.entities[record["name"]])
    cx, cy = record["points"][0]
    for point, angle in [(edge.start_point(), record["start_angle"]), (edge.end_point(), record["end_angle"])]:
        assert (point.X, point.Y) == pytest.approx((cx + record["radius"] * math.cos(math.radians(angle)),
                                                  cy + record["radius"] * math.sin(math.radians(angle))))
    assert edge.length == pytest.approx(record["radius"] * math.pi / 2)
    assert native.is_valid


def test_live_sheet_imports_named_entities_units_layers_and_source_metadata():
    imported = parse_dxf(DATA / "units-mm.dxf", units="in")
    sheet = imported.to_sheet("live")
    assert sheet.name == "live" and sheet.units == "in"
    assert set(sheet.entities) == {record["name"] for record in imported.entities}
    for record in imported.entities:
        entity = sheet.entities[record["name"]]
        assert entity.layer == record["layer"]
        assert entity.metadata["source_name"] == record["name"]
    assert sheet.entities["circle:32"].radius == pytest.approx(5 / 25.4)
    from aiad_drafting import Sheet2D
    assert Sheet2D.from_dict(json.loads(json.dumps(sheet.to_dict()))).to_dict() == sheet.to_dict()
    svg = parse_svg(DATA / "layers-transform.svg").to_sheet()
    assert svg.entities["hole"].radius == pytest.approx(5)


def test_svg_sheared_conics_have_correct_exact_basis_and_arc_midpoint(tmp_path):
    path = tmp_path / "shear.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">'
                    '<g transform="matrix(1 .3 .6 1 0 0)"><circle id="c" cx="20" cy="20" r="5"/>'
                    '<path id="a" d="M25 20 A5 5 0 0 1 20 25"/></g></svg>')
    imported = parse_svg(path)
    circle = _entity(imported, "c")["metadata"]["ellipse"]
    a, b = circle["axis_u"], circle["axis_v"]
    # Compare the conic's covariance to the independently transformed circle.
    assert (a[0]**2 + b[0]**2, a[0]*a[1] + b[0]*b[1], a[1]**2 + b[1]**2) == pytest.approx((34, -22.5, 27.25))
    arc = _entity(imported, "a.s000")["metadata"]["ellipse"]
    t = math.radians(arc["start_parameter"] + arc["sweep"] / 2)
    phi = math.radians(arc["rotation"])
    x = arc["center"][0] + arc["rx"] * math.cos(t) * math.cos(phi) - arc["ry"] * math.sin(t) * math.sin(phi)
    y = arc["center"][1] + arc["rx"] * math.cos(t) * math.sin(phi) + arc["ry"] * math.sin(t) * math.cos(phi)
    value = 20 + 5 / math.sqrt(2)
    assert (x, y) == pytest.approx((1.6 * value, 100 - 1.3 * value))
