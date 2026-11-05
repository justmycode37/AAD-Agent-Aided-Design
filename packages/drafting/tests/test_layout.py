from __future__ import annotations

import xml.etree.ElementTree as ET
from types import SimpleNamespace

from build123d import Box
import pytest

from aad_drafting import DrawingRequest, make_drawing
from aad_drafting.layout import text_bounds, text_width_mm
from aad_drafting.export import text
from examples.layout_samples import build_dense_mixed_hole_plate, build_two_part_assembly


def _check(result, check_id: str) -> dict:
    return next(check for check in result.checks if check["id"] == check_id)


def test_standard_view_alignment_is_preserved_for_first_and_third_angle(tmp_path):
    for style, side_sign, top_sign in (("iso", -1, -1), ("asme", 1, 1)):
        result = make_drawing(
            SimpleNamespace(shape=Box(60, 40, 8), units="mm"),
            DrawingRequest(
                views=("front", "top", "right", "iso"),
                dimensions="none",
                style=style,
                formats=("svg",),
            ),
            tmp_path / style,
        )
        views = {view["name"]: view for view in result.views}
        front, top, right = views["front"], views["top"], views["right"]
        assert top["center_mm"][0] == pytest.approx(front["center_mm"][0])
        assert right["center_mm"][1] == pytest.approx(front["center_mm"][1])
        assert side_sign * (right["center_mm"][0] - front["center_mm"][0]) > 0
        assert top_sign * (top["center_mm"][1] - front["center_mm"][1]) > 0
        assert result.ok, result.checks


def test_text_layout_uses_real_glyph_metrics():
    wide = text_width_mm("MMMM", 2.5)
    narrow = text_width_mm("iiii", 2.5)
    assert wide > narrow
    measured = text_bounds(text("MMMM", 20, 20, 2.5, align="center"))
    assert measured is not None
    assert measured.width == pytest.approx(wide)
    assert measured.ymin <= 20 < measured.ymax


def test_notes_revision_table_title_and_bom_wrap_without_truncation(tmp_path):
    context = build_two_part_assembly()
    context.children[0]["quantity"] = 99  # ignored; only child instances set the count
    long_title = "Two part fixture with a deliberately long descriptive title"
    long_note = "Measure the assembled geometry and retain the complete note text"
    change = "Initial production drawing with complete source-derived component information"
    result = make_drawing(
        context,
        DrawingRequest(
            sheet="A3",
            views=("front", "top", "right", "iso"),
            dimensions="auto",
            style="asme",
            title_block={"title": long_title, "drawing_no": "AAD-S4-ASM-TEST"},
            notes=(long_note,),
            revisions=({"revision": "A", "date": "2026-11-04", "description": change, "by": "AAD"},),
            bom=True,
            formats=("svg",),
        ),
        tmp_path,
    )
    root = ET.parse(result.artifacts["svg"]).getroot()
    labels = [item.text or "" for item in root.findall(".//{http://www.w3.org/2000/svg}text")]
    joined = " ".join(labels)
    for fragment in (long_title, long_note, change, "BILL OF MATERIALS", "REVISION HISTORY"):
        assert fragment in joined
    assert [row["quantity"] for row in result.bom] == [1, 1]
    assert [row["part_number"] for row in result.bom] == ["BASE-01", "PIN-01"]
    assert result.sheet["bom_item_count"] == 2
    assert result.ok, result.checks


def test_bom_is_rejected_when_context_has_no_assembly_children(tmp_path):
    with pytest.raises(ValueError, match="no assembly children"):
        make_drawing(
            SimpleNamespace(shape=Box(30, 20, 5), units="mm"),
            DrawingRequest(views=("front",), dimensions="none", bom=True, formats=("svg",)),
            tmp_path,
        )


def test_dense_mixed_hole_plate_has_visible_dimensions_and_no_layout_collisions(tmp_path):
    result = make_drawing(
        build_dense_mixed_hole_plate(),
        DrawingRequest(
            sheet="A3",
            views=("front", "top", "right", "iso"),
            dimensions="auto",
            style="iso",
            formats=("svg",),
        ),
        tmp_path,
    )
    root = ET.parse(result.artifacts["svg"]).getroot()
    labels = root.findall(".//{http://www.w3.org/2000/svg}text")
    displayed_ids = {item.get("data-dimension-id") for item in labels}
    assert all(dimension["id"] in displayed_ids for dimension in result.dimensions)
    assert len({dimension["id"] for dimension in result.dimensions}) == len(result.dimensions)
    assert _check(result, "hole_leader_coverage")["status"] == "pass"
    assert _check(result, "dimension_text_collisions")["status"] == "pass"
    assert _check(result, "text_view_geometry_collisions")["status"] == "pass"
    assert _check(result, "view_view_collisions")["status"] == "pass"
    assert result.ok, result.checks


def test_text_below_minimum_is_not_used_and_overlong_notes_fail_visibly(tmp_path):
    long_note = "uncuttable-note-" * 700
    result = make_drawing(
        SimpleNamespace(shape=Box(30, 20, 5), units="mm", name="Overflow review"),
        DrawingRequest(
            sheet="A4",
            views=("front",),
            dimensions="none",
            notes=(long_note,),
            formats=("svg",),
        ),
        tmp_path,
    )
    labels = ET.parse(result.artifacts["svg"]).getroot().findall(".//{http://www.w3.org/2000/svg}text")
    assert all(float(item.attrib["font-size"]) >= 2.5 for item in labels)
    assert len(result.notes[0]) == len(long_note)
    assert _check(result, "schedule_bounds")["status"] == "fail"
    assert _check(result, "text_margins")["status"] == "fail"
    assert not result.ok


def test_hole_leader_routes_around_other_bores():
    from aad_drafting.routing import hole_leader_path, segment_distance
    circles=[(0,(0,0),2),(1,(7,0),2),(2,(14,0),2)]
    index,path=hole_leader_path(circles,[0],(25,0))
    assert index==0 and len(path)>2
    assert path[-1] == (25,0)
    import math
    assert math.dist(path[0],(0,0)) == pytest.approx(2)
    assert all(segment_distance(center,a,b)>=radius+0.59
               for other,center,radius in circles if other!=0 for a,b in zip(path,path[1:]))


def test_png_glyphs_fit_the_measured_baseline_bounds(tmp_path):
    from aad_drafting.export import write_png
    from PIL import Image,ImageChops
    label=text("TITLE",5,10,2.5)
    write_png(tmp_path/"baseline.png",50,30,[label])
    with Image.open(tmp_path/"baseline.png") as im:
        left,top,right,bottom=ImageChops.difference(im,Image.new("RGB",im.size,"white")).getbbox()
        bounds=text_bounds(label)
        assert left/4 >= bounds.xmin-0.3
        assert right/4 <= bounds.xmax+0.3
        assert 30-bottom/4 >= bounds.ymin-0.3
        assert 30-top/4 <= bounds.ymax+0.3


def test_same_part_number_does_not_merge_different_geometry():
    from aad_drafting.layout import bom_from_context
    a,b=Box(10,10,3),Box(20,10,3)
    rows=bom_from_context(SimpleNamespace(children=[{"part_number":"X","shape":a},{"part_number":"X","shape":b}]))
    assert len(rows)==2 and all(r["quantity"]==1 for r in rows)
    rows=bom_from_context(SimpleNamespace(children=[{"part_number":"X","shape":a},{"part_number":"X","shape":a}]))
    assert len(rows)==1 and rows[0]["quantity"]==2


def test_cylinder_schedule_dimensions_are_rendered_and_table_rules_clear(tmp_path):
    from examples.layout_samples import build_simple_turned_part
    result=make_drawing(build_simple_turned_part(),DrawingRequest(views=("front","top","iso"),formats=("svg",)),tmp_path)
    root=ET.parse(result.artifacts["svg"]).getroot()
    ids={node.get("data-dimension-id") for node in root.findall(".//{http://www.w3.org/2000/svg}text")}
    cylinders=[d for d in result.dimensions if d["kind"].startswith("cylinder_")]
    assert len(cylinders)==16
    assert all(d["id"] in ids and d["source"]["face_indices"] for d in cylinders)
    assert sorted(d["value"] for d in cylinders if d["kind"]=="cylinder_length") == [8,10,10,21]
    assert _check(result,"table_rule_collisions")["status"]=="pass"
    assert _check(result,"dimension_view_collisions")["status"]=="pass"
    assert result.ok,result.checks


def test_open_cylindrical_cut_is_a_radius_not_a_through_hole(tmp_path):
    from build123d import Cylinder, Pos
    from aad_drafting.features import measure_cylinders
    shape=Box(20,20,10)-Cylinder(4,12)-Pos(-5,0,0)*Box(10,4,12)
    assert any(not f.full_circle for f in measure_cylinders(shape))
    result=make_drawing(SimpleNamespace(shape=shape,units="mm"),
                        DrawingRequest(sheet="A3",formats=("svg",)),tmp_path)
    labels=[node.text or "" for node in ET.parse(result.artifacts["svg"]).getroot().findall(".//{http://www.w3.org/2000/svg}text")]
    assert result.sheet["hole_count"]==0
    assert not any("THRU" in value for value in labels)
    assert "R 4" in labels
    assert result.ok,result.checks


def test_vertical_mixed_holes_have_non_crossing_leaders(tmp_path):
    from build123d import Cylinder, Pos
    shape=Box(80,80,8)
    for y,radius in ((-25,3),(0,4),(25,5)):
        shape-=Pos(0,y,0)*Cylinder(radius,10)
    result=make_drawing(SimpleNamespace(shape=shape,units="mm"),
                        DrawingRequest(sheet="A3",formats=("svg",)),tmp_path)
    assert _check(result,"hole_leader_collisions")["status"]=="pass"
    assert _check(result,"hole_leader_coverage")["status"]=="pass"
    assert result.ok,result.checks
