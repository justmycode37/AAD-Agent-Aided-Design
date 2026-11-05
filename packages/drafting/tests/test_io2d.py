from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import ezdxf
import pytest

from aad_drafting import io2d
from aad_drafting.import2d import parse_dxf, parse_svg
from aad_drafting.model2d import Sheet2D, _entity_to_native, latest_sheet


SVG_NS = "{http://www.w3.org/2000/svg}"


def _ctx(sheet, *, units=None, job_dir=None):
    values = {"sheet": sheet, "units": sheet.units if units is None else units}
    if job_dir is not None:
        values["job_dir"] = job_dir
    return SimpleNamespace(**values)


def test_svg_physical_viewbox_escaping_layers_roundtrip_and_read_only(tmp_path):
    sheet = Sheet2D("panel", units="mm")
    sheet.rectangle("outline", 30, 20, origin=(-15, -10), layer="cut")
    sheet.circle("hole", (0, 0), 2, layer="cut")
    sheet.line("score_line", (-12, 0), (12, 0), layer="score")
    label = sheet.text("label", '<script>alert("x")</script> & laser', (0, 0), 2, layer="engrave")
    label.metadata["rotation_deg"] = 30
    sheet.entities["outline"].metadata.update({
        "source_tag": "path",
        "source_element_attributes": {
            "onclick": "alert('bad')", "href": "https://invalid.example/", "data-original": "ignored"
        },
    })
    before = sheet.to_dict()
    latest = latest_sheet()
    ctx = _ctx(sheet, job_dir=tmp_path)

    path = io2d.export_2d(ctx, "svg", tmp_path / "panel.svg")

    assert path == (tmp_path / "panel.svg").resolve()
    assert path.is_file()
    assert sheet.to_dict() == before
    assert latest_sheet() is latest
    raw = path.read_text(encoding="utf-8")
    root = ET.fromstring(raw)
    viewbox = [float(value) for value in root.attrib["viewBox"].split()]
    assert viewbox[0] <= -15 and viewbox[1] <= -10
    assert viewbox[2] >= 30 and viewbox[3] >= 20
    assert "onclick" not in raw and "https://invalid.example" not in raw
    assert "data-original" not in raw and "<script>" not in raw
    assert "&lt;script&gt;" in raw and "&amp;" in raw
    assert root.find(f".//{SVG_NS}text").text == '<script>alert("x")</script> & laser'
    assert root.find(f".//{SVG_NS}text").attrib["transform"].startswith("rotate(-30 ")
    assert {node.attrib["data-layer"] for node in root.findall(f".//{SVG_NS}g")} == {
        "cut", "score", "engrave"
    }
    mapped_path = io2d.export_2d(
        {"sheet": before, "units": "mm"}, "svg", tmp_path / "panel-mapped.svg"
    )
    assert mapped_path.read_bytes() == path.read_bytes()

    # The independent static SVG importer recovers native shape and area.
    imported = parse_svg(path)
    assert sorted(entity["kind"] for entity in imported.entities if entity["layer"] == "cut") == [
        "circle", "polyline"
    ]

    cut_only = io2d.export_2d(ctx, "svg", tmp_path / "cut-only.svg", layers=("cut",))
    cut_root = ET.parse(cut_only).getroot()
    assert cut_root.attrib["width"] == "30mm" and cut_root.attrib["height"] == "20mm"
    assert {node.attrib["data-layer"] for node in cut_root.findall(f".//{SVG_NS}g")} == {"cut"}
    assert cut_root.find(f".//{SVG_NS}text") is None
    restored = parse_svg(cut_only).to_sheet()
    assert restored.to_face(layers=("cut",)).area == pytest.approx(sheet.to_face(layers=("cut",)).area)


def test_svg_preserves_analytic_arcs_beziers_and_rotated_ellipses(tmp_path):
    sheet = Sheet2D("curves", units="mm")
    sheet.arc("ccw_arc", (0, 0), 4, 0, 90, layer="cut")
    sheet.arc("cw_arc", (10, 0), 4, 0, 90, clockwise=True, layer="cut")
    sheet.bezier("quadratic", [(0, 10), (4, 15), (8, 10)], layer="score")
    sheet.bezier("cubic", [(10, 10), (12, 15), (16, 5), (18, 10)], layer="score")
    sheet.bezier("straight_cubic", [(0, 20), (1, 20), (2, 20), (3, 20)], layer="score")
    sheet.ellipse("full_ellipse", (20, 0), 2, 5, rotation=30, layer="engrave")
    sheet.ellipse("trimmed_ellipse", (30, 0), 2, 5, rotation=-25,
                  start_angle=20, end_angle=140, layer="engrave")
    output = io2d.export_2d(_ctx(sheet), "svg", tmp_path / "curves.svg")
    root = ET.parse(output).getroot()
    by_name = {node.attrib["data-entity"]: node for node in root.findall(".//*[@data-entity]")}
    assert by_name["ccw_arc"].attrib["d"].startswith("M ")
    assert by_name["ccw_arc"].attrib["d"].split()[8] == "0"  # SVG sweep is inverted by the y-up map.
    assert by_name["cw_arc"].attrib["d"].split()[8] == "1"
    assert " Q " in f" {by_name['quadratic'].attrib['d']} "
    assert " C " in f" {by_name['cubic'].attrib['d']} "
    assert "A 2 5 25" in by_name["trimmed_ellipse"].attrib["d"]
    full_ellipse = by_name["full_ellipse"]
    assert full_ellipse.tag == f"{SVG_NS}ellipse"
    assert full_ellipse.attrib["rx"] == "2" and full_ellipse.attrib["ry"] == "5"
    assert full_ellipse.attrib["transform"].startswith("rotate(-30 ")

    imported = parse_svg(output)
    assert {entity["kind"] for entity in imported.entities} >= {
        "arc", "quadratic_bezier", "cubic_bezier", "ellipse", "elliptical_arc"
    }
    view_x, view_y, view_width, view_height = [
        float(value) for value in root.attrib["viewBox"].split()
    ]
    source_ymin = -(view_y + view_height)
    for source_id, expected in (
        ("quadratic", [(0, 10), (4, 15), (8, 10)]),
        ("cubic", [(10, 10), (12, 15), (16, 5), (18, 10)]),
    ):
        curve = next(
            entity for entity in imported.entities
            if entity["kind"] in {"quadratic_bezier", "cubic_bezier"}
            and entity["metadata"].get("source_id") == source_id
        )
        recovered = [
            (point[0] + view_x, point[1] + source_ymin) for point in curve["points"]
        ]
        assert [coordinate for point in recovered for coordinate in point] == pytest.approx(
            [coordinate for point in expected for coordinate in point]
        )
    full_record = next(
        entity for entity in imported.entities
        if entity["kind"] == "ellipse" and entity["metadata"].get("source_id") == "full_ellipse"
    )
    assert full_record["metadata"]["ellipse"]["rx"] == pytest.approx(2)
    assert full_record["metadata"]["ellipse"]["ry"] == pytest.approx(5)
    assert full_record["metadata"]["ellipse"]["rotation"] == pytest.approx(30)
    arc_records = {
        entity["metadata"].get("source_id"): entity
        for entity in imported.entities
        if entity["kind"] == "arc"
    }
    assert arc_records["ccw_arc"]["metadata"]["ccw"] is True
    assert arc_records["cw_arc"]["metadata"]["ccw"] is False
    assert _entity_to_native(sheet, sheet.entities["ccw_arc"]).length == pytest.approx(2 * math.pi)
    assert _entity_to_native(sheet, sheet.entities["cw_arc"]).length == pytest.approx(6 * math.pi)
    assert _entity_to_native(sheet, sheet.entities["straight_cubic"]).length == pytest.approx(3)


def test_dxf_r2013_entities_beziers_ellipses_units_and_audit(tmp_path):
    sheet = Sheet2D("native", units="in")
    sheet.line("edge", (1, 2), (3, 4), layer="cut")
    sheet.circle("hole", (4, 5), 0.25, layer="cut")
    sheet.arc("counterclockwise", (0, 0), 2, 10, 130, layer="score")
    sheet.arc("clockwise", (10, 0), 3, 20, 100, layer="score", clockwise=True)
    sheet.polyline("closed_profile", [(0, 0), (4, 0), (4, 3), (0, 3)], closed=True, layer="cut")
    quadratic_controls = [(0, 0), (1, 3), (4, 2)]
    cubic_controls = [(5, 0), (6, 5), (8, -2), (9, 1)]
    sheet.bezier("quadratic", quadratic_controls, layer="engrave")
    sheet.bezier("cubic", cubic_controls, layer="engrave")
    sheet.ellipse("full_tall", (12, 4), 2, 5, rotation=30, layer="cut")
    sheet.ellipse("trimmed_tall", (20, -4), 2, 5, rotation=-25,
                  start_angle=25, end_angle=155, clockwise=True, layer="score")
    text = sheet.text("text", "A & B", (2, 8), 0.2, layer="engrave")
    text.metadata["rotation_deg"] = 37
    before = sheet.to_dict()
    latest = latest_sheet()

    path = io2d.export_2d(_ctx(sheet, job_dir=tmp_path), "dxf", tmp_path / "native.dxf")
    doc = ezdxf.readfile(path)
    audit = doc.audit()
    assert not audit.errors
    assert doc.dxfversion == "AC1027"  # R2013
    assert doc.header["$INSUNITS"] == 1  # inches
    msp = doc.modelspace()
    assert len(msp.query("LINE")) == 1
    assert len(msp.query("CIRCLE")) == 1
    assert len(msp.query("ARC")) == 2
    assert len(msp.query("LWPOLYLINE")) == 1
    assert len(msp.query("SPLINE")) == 2
    assert len(msp.query("ELLIPSE")) == 2
    assert len(msp.query("TEXT")) == 1
    assert len(msp.query("DIMENSION")) == 0
    assert {entity.dxf.layer for entity in msp} == {"cut", "score", "engrave"}

    line = msp.query("LINE")[0]
    assert (line.dxf.start.x, line.dxf.start.y) == pytest.approx((1, 2))
    assert (line.dxf.end.x, line.dxf.end.y) == pytest.approx((3, 4))
    assert _entity_to_native(sheet, sheet.entities["edge"]).length == pytest.approx(2 * math.sqrt(2))
    circle = msp.query("CIRCLE")[0]
    assert (circle.dxf.center.x, circle.dxf.center.y) == pytest.approx((4, 5))
    assert circle.dxf.radius == pytest.approx(0.25)
    arcs = {entity.dxf.layer: entity for entity in msp.query("ARC")}
    assert arcs["score"].dxf.start_angle == pytest.approx(100)
    assert arcs["score"].dxf.end_angle == pytest.approx(20)

    actual_controls = [
        [(float(point[0]), float(point[1])) for point in spline.control_points]
        for spline in msp.query("SPLINE")
    ]
    flatten = lambda controls: [coordinate for point in controls for coordinate in point]
    assert any(flatten(controls) == pytest.approx(flatten(quadratic_controls)) for controls in actual_controls)
    assert any(flatten(controls) == pytest.approx(flatten(cubic_controls)) for controls in actual_controls)
    assert sorted(len(spline.knots) for spline in msp.query("SPLINE")) == [6, 8]
    assert sorted(spline.dxf.degree for spline in msp.query("SPLINE")) == [2, 3]
    assert all(
        list(spline.knots) == [0.0] * (spline.dxf.degree + 1) + [1.0] * (spline.dxf.degree + 1)
        for spline in msp.query("SPLINE")
    )
    assert all(all(weight == pytest.approx(1.0) for weight in spline.weights)
               for spline in msp.query("SPLINE"))

    ellipses = {entity.dxf.layer: entity for entity in msp.query("ELLIPSE")}
    full = next(entity for entity in ellipses.values() if entity.dxf.center.x == pytest.approx(12))
    assert (full.dxf.major_axis.x, full.dxf.major_axis.y) == pytest.approx(
        (-2.5, 5 * math.cos(math.radians(30)))
    )
    assert full.dxf.ratio == pytest.approx(0.4)
    assert full.dxf.start_param == pytest.approx(0)
    assert full.dxf.end_param == pytest.approx(math.tau)
    trimmed = next(entity for entity in ellipses.values() if entity.dxf.center.x == pytest.approx(20))
    assert (trimmed.dxf.major_axis.x, trimmed.dxf.major_axis.y) == pytest.approx(
        (5 * math.sin(math.radians(25)), 5 * math.cos(math.radians(25)))
    )
    assert trimmed.dxf.ratio == pytest.approx(0.4)
    # Clockwise source geometry is represented by its geometrically equivalent
    # counterclockwise ELLIPSE interval, beginning at the source end point.
    assert trimmed.dxf.start_param == pytest.approx(math.radians(155 - 90))
    assert trimmed.dxf.end_param - trimmed.dxf.start_param == pytest.approx(math.radians(230))

    def dxf_ellipse_point(ellipse, parameter):
        center = ellipse.dxf.center
        major = ellipse.dxf.major_axis
        ratio = ellipse.dxf.ratio
        return (
            center.x + major.x * math.cos(parameter) - major.y * ratio * math.sin(parameter),
            center.y + major.y * math.cos(parameter) + major.x * ratio * math.sin(parameter),
        )

    def source_ellipse_point(center, rx, ry, rotation, parameter):
        phi, theta = math.radians(rotation), math.radians(parameter)
        return (
            center[0] + rx * math.cos(theta) * math.cos(phi) - ry * math.sin(theta) * math.sin(phi),
            center[1] + rx * math.cos(theta) * math.sin(phi) + ry * math.sin(theta) * math.cos(phi),
        )

    assert dxf_ellipse_point(trimmed, trimmed.dxf.start_param) == pytest.approx(
        source_ellipse_point((20, -4), 2, 5, -25, 155)
    )
    assert dxf_ellipse_point(trimmed, trimmed.dxf.end_param) == pytest.approx(
        source_ellipse_point((20, -4), 2, 5, -25, 25)
    )

    label = msp.query("TEXT")[0]
    assert label.dxf.text == "A & B"
    assert label.dxf.rotation == pytest.approx(37)
    assert sheet.to_dict() == before
    assert latest_sheet() is latest


def test_dxf_standard_roundtrip_parser_native_area_and_coordinate_units(tmp_path):
    sheet = Sheet2D("ring", units="mm")
    sheet.rectangle("outside", 40, 20, origin=(-20, -10), layer="cut")
    sheet.circle("inside", (0, 0), 3, layer="cut")
    sheet.arc("score_arc", (30, 0), 5, 0, 90, layer="score")
    path = io2d.export_2d(_ctx(sheet), "dxf", tmp_path / "ring.dxf")
    imported = parse_dxf(path)
    circle = next(entity for entity in imported.entities if entity["kind"] == "circle")
    assert circle["points"][0] == pytest.approx((0, 0))
    assert circle["radius"] == pytest.approx(3)
    restored = imported.to_sheet()
    expected_area = 40 * 20 - math.pi * 3**2
    assert sheet.to_face(layers=("cut",)).area == pytest.approx(expected_area)
    assert restored.to_face(layers=("cut",)).area == pytest.approx(expected_area)
    assert _entity_to_native(sheet, sheet.entities["score_arc"]).length == pytest.approx(5 * math.pi / 2)

    inch_sheet = Sheet2D("inch", units="in")
    inch_sheet.rectangle("inch_plate", 1, 0.5, origin=(0, 0), layer="cut")
    inch_svg = io2d.export_2d(_ctx(inch_sheet), "svg", tmp_path / "inch.svg")
    inch_dxf = io2d.export_2d(_ctx(inch_sheet), "dxf", tmp_path / "inch.dxf")
    assert ET.parse(inch_svg).getroot().attrib["width"] == "1in"
    dxf_doc = ezdxf.readfile(inch_dxf)
    assert dxf_doc.header["$INSUNITS"] == 1
    inch_roundtrip_mm = parse_dxf(inch_dxf, units="mm")
    points = next(entity["points"] for entity in inch_roundtrip_mm.entities if entity["kind"] == "polyline")
    flatten = [coordinate for point in points for coordinate in point]
    assert flatten == pytest.approx([0, 0, 25.4, 0, 25.4, 12.7, 0, 12.7])


@pytest.mark.parametrize(
    ("ctx_units", "sheet_units", "message"),
    [
        (None, "mm", "Context units"),
        ("cm", "mm", "Context units"),
        ("in", "mm", "do not match"),
        ("mm", None, "Sheet units"),
    ],
)
def test_invalid_or_missing_units_are_rejected(tmp_path, ctx_units, sheet_units, message):
    sheet = Sheet2D("units", units="mm")
    data = sheet.to_dict()
    if sheet_units is None:
        data.pop("units")
    ctx = {"sheet": data, "units": ctx_units}
    with pytest.raises(ValueError, match=message):
        io2d.export_2d(ctx, "svg", tmp_path / "bad.svg")
    assert not (tmp_path / "bad.svg").exists()


def test_output_confinement_unsupported_transforms_and_atomic_file_preservation(tmp_path, monkeypatch):
    job = tmp_path / "job"
    job.mkdir()
    outside = tmp_path / "outside" / "panel.svg"
    sheet = Sheet2D("confined", units="mm")
    sheet.rectangle("outline", 4, 3)
    ctx = _ctx(sheet, job_dir=job)
    with pytest.raises(ValueError, match="must be under ctx.job_dir"):
        io2d.export_2d(ctx, "svg", outside)
    assert not outside.parent.exists()

    target = job / "panel.svg"
    target.write_bytes(b"previous artifact")
    original_replace = io2d.os.replace

    def fail_publishing(source, destination, **kwargs):
        if Path(destination).name == target.name:
            raise OSError("simulated atomic publish failure")
        return original_replace(source, destination, **kwargs)

    monkeypatch.setattr(io2d.os, "replace", fail_publishing)
    with pytest.raises(OSError, match="simulated"):
        io2d.export_2d(ctx, "svg", target)
    assert target.read_bytes() == b"previous artifact"
    assert list(job.glob(".panel.svg.*.tmp")) == []

    monkeypatch.setattr(io2d.os, "replace", original_replace)
    sheet.entities["outline"].metadata["transform"] = [1, 0, 0, 1, 5, 0]
    with pytest.raises(ValueError, match="unsupported transform"):
        io2d.export_2d(ctx, "svg", target)
    assert target.read_bytes() == b"previous artifact"


def test_flattened_import_provenance_is_not_reapplied_or_reemitted(tmp_path):
    source = tmp_path / "imported.svg"
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="10mm" viewBox="0 0 20 10">'
        '<g transform="translate(2 0)"><text id="label" x="3" y="5" font-size="2" '
        'data-custom="source-only">Imported &amp; safe</text></g></svg>',
        encoding="utf-8",
    )
    sheet = parse_svg(source).to_sheet()
    output = io2d.export_2d(_ctx(sheet), "svg", tmp_path / "clean.svg")
    raw = output.read_text(encoding="utf-8")
    assert "data-custom" not in raw and "translate(2 0)" not in raw
    root = ET.fromstring(raw)
    assert root.find(f".//{SVG_NS}text").text == "Imported & safe"


def test_io2d_import_is_kernel_free_until_native_geometry_is_requested():
    package_src = Path(__file__).resolve().parents[1] / "src"
    code = "import sys; import aad_drafting.io2d; assert 'build123d' not in sys.modules; assert 'OCP' not in sys.modules"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(package_src)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-c", code], env=env, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('rotation', [0, 37])
@pytest.mark.parametrize('units', ['mm', 'in'])
def test_multiline_text_preserves_content_blank_lines_and_rotated_baselines(tmp_path, rotation, units):
    sheet = Sheet2D('multiline', units=units)
    entity = sheet.text('label', 'TOP <&>\r\n\r\nBOTTOM', (3, 7), 2, layer='engrave')
    entity.metadata['rotation_deg'] = rotation
    before = sheet.to_dict()
    svg = io2d.export_2d(_ctx(sheet), 'svg', tmp_path/'label.svg')
    dxf = io2d.export_2d(_ctx(sheet), 'dxf', tmp_path/'label.dxf')
    root = ET.parse(svg).getroot()
    spans = root.findall(f'.//{SVG_NS}tspan')
    assert [span.text or '' for span in spans] == ['TOP <&>', '', 'BOTTOM']
    assert [float(span.attrib['y']) for span in spans] == pytest.approx([-7, -4.4, -1.8])
    labels = list(ezdxf.readfile(dxf).modelspace().query('TEXT'))
    assert [label.dxf.text for label in labels] == ['TOP <&>', '', 'BOTTOM']
    phi = math.radians(rotation)
    for index, label in enumerate(labels):
        assert label.dxf.rotation == rotation
        assert tuple(label.dxf.insert) == pytest.approx((3+2.6*index*math.sin(phi), 7-2.6*index*math.cos(phi), 0))
    assert sheet.to_dict() == before
