import json
import math
from types import SimpleNamespace

import ezdxf
import pytest

from aad_drafting import DimensionSpec, DrawingRequest, make_drawing, sheet2d
from aad_drafting.drawing2d import prepare
from aad_drafting.sheet import _normalize_request
from examples.planar_sheet import model


def context(sheet):
    return SimpleNamespace(kind="2d", units=sheet["units"] if isinstance(sheet, dict) else sheet.units,
                           shape=None, sheet=sheet, process="laser")


@pytest.mark.parametrize("units", ["mm", "in"])
def test_native_sheet_draws_measured_xy_geometry_in_all_formats(tmp_path, units):
    sheet = model(units)
    ctx = context(sheet if units == "mm" else json.loads(json.dumps(sheet.to_dict())))
    ctx.units = units
    result = make_drawing(ctx, DrawingRequest(sheet="A3"), tmp_path / units)
    assert result.ok, [item for item in result.checks if item["status"] == "fail"]
    assert result.sheet["kind"] == "2d" and result.sheet["hole_count"] == 4
    assert len(result.views) == 1 and result.views[0]["kind"] == "planar"
    scale = 1 if units == "mm" else 1 / 25.4
    dimensions = {(record["kind"], record["axis"]): record for record in result.dimensions if record["kind"] == "overall"}
    assert dimensions["overall", "x"]["value"] == pytest.approx(100 * scale)
    assert dimensions["overall", "y"]["value"] == pytest.approx(60 * scale)
    assert not any(record["axis"] == "z" for record in result.dimensions)
    diameters = [record for record in result.dimensions if record["kind"] == "hole_diameter"]
    assert len(diameters) == 1 and diameters[0]["count"] == 4
    assert diameters[0]["value"] == pytest.approx(4.4 * scale)
    assert diameters[0]["source"]["cutouts"][0]["diameter"] == pytest.approx(4.4 * scale)
    assert all(record["source"]["type"].startswith("native_") for record in result.dimensions)
    assert set(result.artifacts) == {"svg", "dxf", "pdf"}
    assert result.preview.is_file()
    assert all(path.stat().st_size > 100 for path in result.artifacts.values())
    assert "THRU" not in result.artifacts["svg"].read_text()
    doc = ezdxf.readfile(result.artifacts["dxf"])
    assert doc.dxfversion == "AC1027" and not doc.audit().errors
    assert {entity.dxf.layer for entity in doc.modelspace()}.issuperset({"cut", "engrave"})


def test_default_a4_planar_dimensions_respect_the_reserved_view_padding(tmp_path):
    sheet = sheet2d("compact plate")
    sheet.rectangle("outline", 40, 20, origin=(0, 0))
    sheet.circle("hole", (10, 10), 2)

    result = make_drawing(context(sheet), DrawingRequest(formats=("svg",)), tmp_path / "compact")

    assert result.ok, [check for check in result.checks if check["status"] == "fail"]
    statuses = {check["id"]: check["status"] for check in result.checks}
    assert statuses["sheet_margins"] == "pass"
    assert statuses["text_margins"] == "pass"
    assert result.sheet["scale"] < 5
    assert result.preview.is_file()


def test_dimension_strokes_ignore_reference_geometry_outside_cut_bounds(tmp_path, monkeypatch):
    from aad_drafting import export
    sheet = sheet2d("datum")
    sheet.rectangle("outline", 100, 60)
    sheet.circle("mount", (20, 30), 3)
    sheet.line("reference", (100, 20), (120, 20), layer="reference")
    captured = []
    original = export.write_svg
    def writer(path, width, height, entities):
        captured.extend(entities)
        original(path, width, height, entities)
    monkeypatch.setattr(export, "write_svg", writer)
    result = make_drawing(context(sheet), DrawingRequest(sheet="A3", formats=("svg",)), tmp_path / "reference")
    assert result.ok
    overall_x = next(record for record in result.dimensions if record["kind"] == "overall" and record["axis"] == "x")
    assert overall_x["value"] == pytest.approx(100)
    extensions = [entity for entity in captured if entity.kind == "line" and entity.dimension_id == overall_x["id"]
                  and math.isclose(entity.points[0][0], entity.points[1][0], abs_tol=1e-8)
                  and abs(entity.points[0][1] - entity.points[1][1]) > 1]
    assert len(extensions) == 2
    actual_span_mm = abs(extensions[0].points[0][0] - extensions[1].points[0][0])
    assert actual_span_mm == pytest.approx(100 * result.sheet["scale"])


def test_explicit_xy_request_does_not_create_a_zero_thickness_dimension():
    sheet = model()
    result = prepare(context(sheet), _normalize_request(DrawingRequest(dimensions=(DimensionSpec("overall"),))))
    assert [record["axis"] for record in result["dimensions"]] == ["x", "y"]
    with pytest.raises(ValueError, match="no thickness"):
        prepare(context(sheet), _normalize_request(DrawingRequest(dimensions=(DimensionSpec("overall", "z"),))))
    with pytest.raises(ValueError, match="single top"):
        prepare(context(sheet), _normalize_request(DrawingRequest(views=("front",))))


def test_score_paths_are_measured_and_unknown_cut_profiles_are_reported():
    sheet = model()
    sheet.line("fold", (-30, 0), (30, 0), layer="score")
    prepared = prepare(context(sheet), _normalize_request(DrawingRequest()))
    measured = [record for record in prepared["dimensions"] if record["source"].get("entities") == ["fold"]]
    values = {record["axis"]: record["value"] for record in measured}
    assert values == pytest.approx({"x": 50, "y": 30, "length": 60, "angle": 0})
    assert next(row for row in prepared["profiles"] if row["kind"] == "SCORE LINE")["width"] == "-"
    triangle = sheet2d("triangle")
    triangle.polyline("outline", [(0, 0), (30, 0), (10, 20)], closed=True)
    prepared = prepare(context(triangle), _normalize_request(DrawingRequest()))
    failed = next(item for item in prepared["checks"] if item["id"] == "2d.undimensioned_features")
    assert failed["status"] == "fail" and "outline" in failed["actual"]


def test_tiny_source_annotation_is_not_enlarged_to_change_the_model(tmp_path):
    sheet = model()
    sheet.text("tiny", "reference", (0, 15), .001)
    before = sheet.to_dict()
    with pytest.raises(ValueError, match="below 2.5 mm"):
        make_drawing(context(sheet), DrawingRequest(sheet="A4"), tmp_path / "tiny")
    assert sheet.to_dict() == before


@pytest.mark.parametrize('units,factor,diameter', [('mm', 1.0, .004), ('in', 1/25.4, .0004)])
def test_small_nonzero_holes_remain_nonzero_on_the_sheet(tmp_path, units, factor, diameter):
    import xml.etree.ElementTree as ET
    sheet = sheet2d('small hole', units=units)
    sheet.rectangle('outline', 10*factor, 10*factor)
    sheet.circle('hole', (5*factor, 5*factor), diameter/2)
    result = make_drawing(context(sheet), DrawingRequest(sheet='A3'), tmp_path/units)
    assert result.ok, result.checks
    measured = next(record for record in result.dimensions if record['kind'] == 'hole_diameter')
    assert measured['value'] == pytest.approx(diameter)
    assert float(measured['display']) == pytest.approx(diameter)
    assert measured['precision'] >= len(measured['display'].split('.')[1])
    root = ET.parse(result.artifacts['svg']).getroot()
    svg = [node.text or '' for node in root.findall('.//{http://www.w3.org/2000/svg}text')
           if node.attrib.get('data-dimension-id') == measured['id']]
    dxf = [entity.dxf.text for entity in ezdxf.readfile(result.artifacts['dxf']).modelspace().query('TEXT')]
    expected = 'DIA ' + measured['display']
    assert any(expected in value for value in svg)
    assert any(expected in value for value in dxf)


@pytest.mark.parametrize('rotation', [45, 90, -30])
@pytest.mark.parametrize('align', ['left', 'center', 'right'])
def test_sheet_writers_preserve_rotated_text_and_its_baseline(tmp_path, rotation, align):
    import xml.etree.ElementTree as ET
    import numpy as np
    from PIL import Image
    from aad_drafting.export import text, write_png, write_svg, write_dxf
    from aad_drafting.layout import text_bounds
    label = text('ROTATED', 35, 35, 3, rotation=rotation, align=align)
    write_svg(tmp_path/'label.svg', 70, 70, [label])
    write_dxf(tmp_path/'label.dxf', 70, 70, [label])
    write_png(tmp_path/'label.png', 70, 70, [label])
    node = ET.parse(tmp_path/'label.svg').getroot().find('.//{http://www.w3.org/2000/svg}text')
    assert node.attrib['transform'] == f'rotate({-rotation} 35 35)'
    dxf = ezdxf.readfile(tmp_path/'label.dxf').modelspace().query('TEXT')[0]
    assert dxf.dxf.rotation == rotation
    with Image.open(tmp_path/'label.png') as preview:
        y, x = np.nonzero(np.asarray(preview.convert('L')) < 180)
    _, vectors = np.linalg.eigh(np.cov(np.column_stack((x,y)).T))
    angle = math.degrees(math.atan2(vectors[1,-1], vectors[0,-1])) % 180
    assert abs((angle+rotation+90) % 180 - 90) < 10
    bounds = text_bounds(label)
    assert x.min()/4 >= bounds.xmin-.5 and x.max()/4 <= bounds.xmax+.5
    assert (280-y.max())/4 >= bounds.ymin-.5 and (280-y.min())/4 <= bounds.ymax+.5
