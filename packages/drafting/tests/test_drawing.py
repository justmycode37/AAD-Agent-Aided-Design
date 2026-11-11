from __future__ import annotations

import xml.etree.ElementTree as ET
from types import SimpleNamespace

from ezdxf import readfile
from PIL import Image, ImageChops
import pytest
from build123d import Box

from aad_drafting import DimensionSpec, DrawingRequest, make_drawing
from examples.first_sheet import build_sample_context


@pytest.fixture(scope="module")
def drawing(tmp_path_factory):
    output = tmp_path_factory.mktemp("drawing")
    result = make_drawing(build_sample_context(), DrawingRequest(), output)
    return result


def test_dimensions_match_geometry_and_keep_sources(drawing):
    dimensions = drawing.dimensions
    overall = {
        item["axis"]: item["value"]
        for item in dimensions
        if item["kind"] == "overall"
    }
    assert overall == {"x": 80.0, "y": 50.0, "z": 6.0}

    diameter = next(item for item in dimensions if item["kind"] == "hole_diameter")
    assert diameter["value"] == 6.0
    assert diameter["count"] == 4
    assert diameter["source"]["type"] == "cylindrical_through_hole"
    assert diameter["source"]["measurement"] == "2 * cylinder.radius"
    assert len(diameter["source"]["face_indices"]) == 4

    coordinates = {
        axis: sorted(item["value"] for item in dimensions if item["kind"] == "hole_coordinate" and item["axis"] == axis)
        for axis in ("x", "y")
    }
    assert coordinates == {"x": [13.0, 63.0], "y": [10.0, 43.0]}
    for dimension in dimensions:
        assert isinstance(dimension["source"], dict)
        assert isinstance(dimension["value"], float)


def test_svg_parses_and_retains_exact_projected_circles(drawing):
    svg_path = drawing.artifacts["svg"]
    root = ET.parse(svg_path).getroot()
    assert root.tag.endswith("svg")
    circles = root.findall(".//{http://www.w3.org/2000/svg}circle")
    visible_circles = [item for item in circles if item.attrib.get("data-layer") == "VISIBLE"]
    assert len(visible_circles) >= 4
    assert all(float(item.attrib["r"]) > 0 for item in visible_circles)


def test_all_formats_have_valid_headers_and_dxf_audits(drawing):
    assert drawing.artifacts["svg"].read_bytes().startswith(b"<?xml")
    assert drawing.artifacts["dxf"].read_bytes().lstrip().startswith(b"0\nSECTION")

    pdf_bytes = drawing.artifacts["pdf"].read_bytes()
    assert pdf_bytes.startswith(b"%PDF-")
    assert b"/Subtype /Image" not in pdf_bytes

    dxf = readfile(drawing.artifacts["dxf"])
    assert dxf.dxfversion == "AC1027"
    auditor = dxf.audit()
    assert not auditor.errors
    circles = list(dxf.modelspace().query("CIRCLE"))
    hole_circles = [
        circle
        for circle in circles
        if circle.dxf.layer == "VISIBLE" and abs(circle.dxf.radius - 3.0*drawing.sheet["scale"]) < 1e-7
    ]
    assert len(hole_circles) == 4


def test_png_preview_renders_with_visible_drawing_content(drawing):
    assert drawing.preview.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(drawing.preview) as image:
        image.verify()
    with Image.open(drawing.preview) as image:
        assert image.width > 800
        assert image.height > 500
        difference = ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, "white"))
        assert difference.getbbox() is not None


def test_mapping_requests_and_explicit_dimensions_are_supported(tmp_path):
    result = make_drawing(
        build_sample_context(),
        {
            "sheet": "letter",
            "views": [{"name": "front"}, {"name": "top"}],
            "dimensions": [
                {"kind": "overall", "axis": "x", "precision": 1},
                {"kind": "hole_diameter", "index": 0},
            ],
            "style": "asme",
            "title_block": {"title": "Explicit request"},
            "formats": ["svg", "png"],
        },
        tmp_path,
    )
    assert result.sheet["name"] == "LETTER"
    assert result.sheet["scale"] > 0
    assert {item["kind"] for item in result.dimensions} == {"overall", "hole_diameter"}
    assert result.artifacts["png"] == result.preview
    assert result.preview.exists()


def test_inch_context_selects_a_physical_sheet_scale(tmp_path):
    context = build_sample_context()
    context.units = "in"
    result = make_drawing(
        context,
        DrawingRequest(
            sheet="A4",
            dimensions="auto",
            formats=("svg",),
        ),
        tmp_path,
    )
    overall = {
        item["axis"]: item["value"]
        for item in result.dimensions
        if item["kind"] == "overall"
    }
    assert overall == {"x": 80.0, "y": 50.0, "z": 6.0}
    assert result.sheet["units"] == "in"
    assert result.sheet["scale"] < 0.05
    assert next(check for check in result.checks if check["id"] == "sheet_margins")["status"] == "pass"


def test_r2013_dxf_keeps_partial_circle_as_arc(tmp_path):
    from build123d import Box, Cylinder, Pos
    from types import SimpleNamespace

    half_round = Cylinder(10.0, 6.0) & (Pos(5.0, 0.0, 0.0) * Box(10.0, 25.0, 8.0))
    context = SimpleNamespace(shape=half_round, units="mm", name="Half-round sample")
    result = make_drawing(
        context,
        {
            "sheet": "A4",
            "views": ["top"],
            "dimensions": "none",
            "formats": ["dxf"],
        },
        tmp_path / "arc",
    )
    document = readfile(result.artifacts["dxf"])
    auditor = document.audit()
    assert not auditor.errors
    assert document.dxfversion == "AC1027"
    assert len(document.modelspace().query("ARC")) >= 1


@pytest.mark.parametrize("index", [99, -1, 0.9, True])
def test_invalid_hole_selection_fails_clearly(tmp_path, index):
    with pytest.raises(ValueError, match="[Hh]ole"):
        make_drawing(build_sample_context(), DrawingRequest(
            views=("front", "top"),
            dimensions=(DimensionSpec("hole_coordinate", "x", index=index),),
            formats=("svg",)), tmp_path)


def test_mixed_hole_diameters_and_spacing_appear_on_sheet(tmp_path):
    from build123d import Box, Cylinder, Pos
    from types import SimpleNamespace
    plate = Box(80, 50, 6)
    for x,y,r in [(-27,-15,3), (23,-15,4), (-27,18,3), (23,18,4)]:
        plate -= Pos(x,y,0) * Cylinder(r, 8)
    for dimensions in ("auto", [DimensionSpec("hole_spacing", "x")]):
        result = make_drawing(SimpleNamespace(shape=plate, units="mm"),
                              {"dimensions": dimensions, "formats": ["svg", "dxf"]}, tmp_path)
        root = ET.parse(result.artifacts["svg"]).getroot()
        labels = root.findall(".//{http://www.w3.org/2000/svg}text")
        ids = {e.get("data-dimension-id") for e in labels}
        assert all(d["id"] in ids for d in result.dimensions)
        assert len({d["id"] for d in result.dimensions}) == len(result.dimensions)
        svg_text = [e.text for e in labels]
        dxf_text = [e.dxf.text for e in readfile(result.artifacts["dxf"]).modelspace().query("TEXT")]
        expected = ["2X DIA 6 THRU", "2X DIA 8 THRU"] if dimensions == "auto" else ["50"]
        assert all(t in svg_text and t in dxf_text for t in expected)
        assert result.ok, result.checks


def test_custom_view_cannot_mislabel_named_axes(tmp_path):
    from aad_drafting import DrawingView
    with pytest.raises(ValueError, match="fixed direction"):
        make_drawing(build_sample_context(), DrawingRequest(
            views=(DrawingView("front", direction=(1,0,0)),)), tmp_path)


def test_missing_view_dimension_is_reported_as_failure(tmp_path):
    result = make_drawing(build_sample_context(), {"views": ["iso"], "formats": ["svg"]}, tmp_path)
    assert not result.ok
    assert next(c for c in result.checks if c["id"] == "dimension_coverage")["status"] == "fail"


def test_raised_boss_does_not_hide_a_plate_through_hole_and_blind_holes_are_excluded():
    from build123d import Box, Cylinder, Pos
    from aad_drafting.geometry import through_holes, shape_bounds
    plate = Box(80,50,6) - Pos(-20,0,0)*Cylinder(3,8)
    raised = plate + Pos(20,0,5)*Cylinder(8,10)
    holes = through_holes(raised, shape_bounds(raised))
    assert len(holes) == 1 and holes[0].radius == 3
    blind = Box(80,50,6) - Pos(-20,0,3)*Cylinder(3,6)
    assert through_holes(blind, shape_bounds(blind)) == []
    counterbore = plate - Pos(-20,0,3)*Cylinder(5,6)
    holes = through_holes(counterbore, shape_bounds(counterbore))
    assert len(holes) == 1 and holes[0].radius == 3


def test_clockwise_pdf_arc_and_complementary_arc_keys():
    from aad_drafting.export import arc, _reportlab_arc_angles
    from aad_drafting.geometry import Curve, _curve_key
    assert _reportlab_arc_angles(arc((0,0), 2, 90, 0, False)) == (90, -90)
    first = Curve("arc", False, center=(0,0), radius=2, start_angle=0, end_angle=180)
    second = Curve("arc", False, center=(0,0), radius=2, start_angle=180, end_angle=0)
    assert _curve_key(first) != _curve_key(second)


def test_cosmetic_thread_metadata_is_shown_as_a_drawing_note(tmp_path):
    designation = "M3 × 0.5"
    context = SimpleNamespace(
        shape=Box(6, 6, 8),
        units="mm",
        kind="part",
        meta={
            "aad_component_geometries": [
                {"thread": {"designation": designation, "representation": "cosmetic"}}
            ]
        },
    )
    result = make_drawing(
        context,
        DrawingRequest(views=("iso",), dimensions="none", formats=("svg",)),
        tmp_path / "cosmetic-thread",
    )
    note = "M3 × 0.5 external thread: cosmetic representation; thread form omitted from B-rep."
    assert result.ok
    assert note in result.notes
