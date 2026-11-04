from __future__ import annotations

import math

from build123d import Align, Box, Cylinder, Pos
from ezdxf import readfile
import pytest

from aiad_drafting import DrawingView
from aiad_drafting.export import arc, circle, line, polyline, write_dxf
from aiad_drafting.views import project_drawing_view, section_plane_trace


def _drilled_plate():
    plate = Box(80.0, 50.0, 6.0)
    hole_centers = ((-20.0, -10.0), (20.0, -10.0), (-20.0, 10.0), (20.0, 10.0))
    for x, y in hole_centers:
        plate = plate - Pos(x, y, 0.0) * Cylinder(3.0, 6.0)
    return plate, hole_centers


def _stepped_turned_part():
    lower = Cylinder(20.0, 12.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    middle = Pos(0, 0, 12) * Cylinder(
        12.0, 28.0, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    upper = Pos(0, 0, 40) * Cylinder(
        8.0, 10.0, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    return lower + middle + upper


def _distance_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-20:
        return math.dist(point, start)
    parameter = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / length_squared,
        ),
    )
    closest = (start[0] + parameter * dx, start[1] + parameter * dy)
    return math.dist(point, closest)


def _sample_curve(curve, steps: int = 64):
    if curve.kind in {"line", "polyline"}:
        return curve.points
    if curve.kind == "circle":
        return tuple(
            (
                curve.center[0] + curve.radius * math.cos(2 * math.pi * index / steps),
                curve.center[1] + curve.radius * math.sin(2 * math.pi * index / steps),
            )
            for index in range(steps)
        )
    if curve.kind == "arc":
        start, end = math.radians(curve.start_angle), math.radians(curve.end_angle)
        sweep = (end - start) % (2 * math.pi) if curve.ccw else -((start - end) % (2 * math.pi))
        return tuple(
            (
                curve.center[0] + curve.radius * math.cos(start + sweep * index / steps),
                curve.center[1] + curve.radius * math.sin(start + sweep * index / steps),
            )
            for index in range(steps + 1)
        )
    return ()


def _as_dxf_entities(projection):
    entities = []
    for curve in projection.curves:
        layer = "HIDDEN" if curve.hidden else "VISIBLE"
        if curve.kind == "line":
            entities.append(line(*curve.points[0], *curve.points[-1], layer=layer))
        elif curve.kind == "polyline":
            entities.append(polyline(curve.points, layer=layer))
        elif curve.kind == "circle":
            entities.append(circle(curve.center, curve.radius, layer=layer))
        elif curve.kind == "arc":
            entities.append(
                arc(
                    curve.center,
                    curve.radius,
                    curve.start_angle,
                    curve.end_angle,
                    curve.ccw,
                    layer=layer,
                )
            )
    return entities


def test_drilled_plate_section_uses_measured_boundary_and_clips_hatching(tmp_path):
    plate, hole_centers = _drilled_plate()
    section = project_drawing_view(
        plate,
        DrawingView(name="section A-A", kind="section", plane="XY", offset=0.0),
    )

    model_bounds = plate.bounding_box()
    assert section.bounds.xmin == pytest.approx(model_bounds.min.X, abs=1e-7)
    assert section.bounds.xmax == pytest.approx(model_bounds.max.X, abs=1e-7)
    assert section.bounds.ymin == pytest.approx(model_bounds.min.Y, abs=1e-7)
    assert section.bounds.ymax == pytest.approx(model_bounds.max.Y, abs=1e-7)

    boundaries = section.metadata["boundary_curves"]
    hole_circles = [curve for curve in boundaries if curve.kind == "circle"]
    assert len(hole_circles) == len(hole_centers)
    assert sorted(curve.radius for curve in hole_circles) == pytest.approx([3.0] * 4)
    assert section.metadata["section_region_count"] == 1

    hatches = section.metadata["hatch_curves"]
    assert hatches
    for hatch in hatches:
        assert hatch.kind == "line"
        for hole_center in hole_centers:
            assert _distance_to_segment(hole_center, *hatch.points) >= 3.0 - 1e-6

    trace = section_plane_trace(
        plate,
        DrawingView(name="section A-A", kind="section", plane="XY", offset=0.0),
    )
    assert trace.parent == "front"
    assert trace.curve.kind == "line"
    assert trace.curve.points[0][1] == pytest.approx(0.0, abs=1e-7)
    assert trace.curve.points[1][1] == pytest.approx(0.0, abs=1e-7)

    dxf_path = tmp_path / "plate-section.dxf"
    write_dxf(dxf_path, 100.0, 80.0, _as_dxf_entities(section))
    document = readfile(dxf_path)
    assert not document.audit().errors
    assert len(document.modelspace().query("CIRCLE")) == 4


def test_stepped_turned_section_matches_the_occt_cut_profile():
    turned = _stepped_turned_part()
    section = project_drawing_view(
        turned,
        DrawingView(name="longitudinal", kind="section", plane="XZ", offset=0.0),
    )

    bounds = turned.bounding_box()
    assert section.bounds.xmin == pytest.approx(bounds.min.X, abs=1e-7)
    assert section.bounds.xmax == pytest.approx(bounds.max.X, abs=1e-7)
    assert section.bounds.ymin == pytest.approx(bounds.min.Z, abs=1e-7)
    assert section.bounds.ymax == pytest.approx(bounds.max.Z, abs=1e-7)

    segments = {
        tuple(sorted(tuple(round(value, 6) for value in point) for point in curve.points))
        for curve in section.metadata["boundary_curves"]
        if curve.kind == "line"
    }
    expected_profile_edges = {
        ((-20.0, 0.0), (-20.0, 12.0)),
        ((-12.0, 12.0), (-12.0, 40.0)),
        ((-8.0, 40.0), (-8.0, 50.0)),
        ((8.0, 40.0), (8.0, 50.0)),
        ((12.0, 12.0), (12.0, 40.0)),
        ((20.0, 0.0), (20.0, 12.0)),
        ((-20.0, 12.0), (-12.0, 12.0)),
        ((-12.0, 40.0), (-8.0, 40.0)),
        ((8.0, 40.0), (12.0, 40.0)),
        ((12.0, 12.0), (20.0, 12.0)),
    }
    assert expected_profile_edges <= segments
    assert section.metadata["hatch_curve_count"] > 0
    assert section.metadata["section_region_count"] == 1


def test_section_offset_outside_solid_fails_clearly():
    plate, _ = _drilled_plate()
    with pytest.raises(ValueError, match=r"Section plane XY at offset 4 does not intersect"):
        project_drawing_view(
            plate,
            DrawingView(name="outside", kind="section", plane="XY", offset=4.0),
        )


def test_circular_detail_clips_curves_and_preserves_arcs_and_circles(tmp_path):
    washer = Cylinder(10.0, 6.0) - Cylinder(4.0, 6.0)
    detail = project_drawing_view(
        washer,
        DrawingView(
            name="detail B",
            kind="detail",
            parent="top",
            center=(4.0, 0.0),
            radius=8.0,
            scale=2.0,
        ),
    )

    assert detail.bounds.xmin == pytest.approx(-12.0)
    assert detail.bounds.xmax == pytest.approx(20.0)
    assert detail.bounds.ymin == pytest.approx(-16.0)
    assert detail.bounds.ymax == pytest.approx(16.0)
    assert any(curve.kind == "arc" and curve.radius == pytest.approx(20.0) for curve in detail.curves)
    circles = [curve for curve in detail.curves if curve.kind == "circle"]
    assert any(curve.center == pytest.approx((4.0, 0.0)) and curve.radius == pytest.approx(16.0) for curve in circles)
    assert any(curve.center == pytest.approx((-4.0, 0.0)) and curve.radius == pytest.approx(8.0) for curve in circles)
    assert not any(curve.kind == "circle" and curve.radius == pytest.approx(20.0) for curve in detail.curves)
    assert all(
        math.dist(point, (4.0, 0.0)) <= 16.0 + 1e-6
        for curve in detail.curves
        for point in _sample_curve(curve)
    )

    dxf_path = tmp_path / "washer-detail.dxf"
    write_dxf(dxf_path, 50.0, 50.0, _as_dxf_entities(detail))
    document = readfile(dxf_path)
    assert not document.audit().errors
    assert len(document.modelspace().query("ARC")) >= 1
    assert len(document.modelspace().query("CIRCLE")) >= 2


def test_detail_with_no_selected_curves_fails_clearly():
    washer = Cylinder(10.0, 6.0) - Cylinder(4.0, 6.0)
    with pytest.raises(ValueError, match="Detail crop selected no projected curves"):
        project_drawing_view(
            washer,
            DrawingView(
                name="empty detail",
                kind="detail",
                parent="top",
                center=(100.0, 100.0),
                radius=2.0,
            ),
        )


def test_public_sheet_accepts_section_detail_mapping_and_preserves_layers(tmp_path):
    from aiad_drafting import make_drawing
    from types import SimpleNamespace
    plate, _ = _drilled_plate()
    result = make_drawing(SimpleNamespace(shape=plate, units="mm"), {
        "sheet": "A3", "dimensions": "none",
        "views": [{"name":"section a-a", "kind":"section", "plane":"XY"},
                  {"name":"detail b", "kind":"detail", "parent":"top", "center":(-30,-10), "radius":15, "scale":2.5}],
    }, tmp_path)
    assert result.ok, result.checks
    assert [v["kind"] for v in result.views] == ["section", "detail"]
    assert result.views[1]["scale_multiplier"] == 2.5
    doc = readfile(result.artifacts["dxf"])
    assert not doc.audit().errors
    assert list(doc.modelspace().query('LINE[layer=="HATCH"]'))


def test_mapping_section_outside_model_is_not_silently_orthographic(tmp_path):
    from aiad_drafting import make_drawing
    from types import SimpleNamespace
    plate, _ = _drilled_plate()
    with pytest.raises(ValueError, match="does not intersect"):
        make_drawing(SimpleNamespace(shape=plate, units="mm"), {
            "dimensions":"none", "views":[{"name":"outside", "kind":"section", "plane":"XY", "offset":8}]}, tmp_path)


def test_detail_coincident_with_model_circle_keeps_selected_geometry():
    detail = project_drawing_view(Cylinder(10,6), DrawingView(
        name="coincident", kind="detail", parent="top", center=(0,0), radius=10))
    assert any(c.kind == "circle" and c.radius == 10 for c in detail.curves)


def test_custom_parent_frame_is_inherited_and_parent_names_normalized(tmp_path):
    from aiad_drafting import make_drawing
    from types import SimpleNamespace
    plate=Box(80,50,6)-Pos(20,0,0)*Cylinder(2,8)
    result=make_drawing(SimpleNamespace(shape=plate,units="mm"),{
        "sheet":"A3", "dimensions":"none", "formats":["dxf"],
        "views":[{"name":"angled", "direction":(0,0,1), "up":(0,1,0)},
                 {"name":"detail c", "kind":"DETAIL", "parent":"ANGLED", "center":(20,0), "radius":6}]},tmp_path)
    doc=readfile(result.artifacts["dxf"])
    # Hole, crop circle and parent crop marker are all analytic circles.
    assert len(doc.modelspace().query("CIRCLE")) >= 4
    assert "DETAIL_MARKERS" in doc.layers
    assert result.views[1]["direction"] == [0,0,1]


def test_standalone_detail_with_unknown_parent_frame_fails():
    with pytest.raises(ValueError,match="requires a direction"):
        project_drawing_view(Cylinder(10,6),DrawingView(name="d",kind="detail",parent="unknown",center=(0,0),radius=5))


def test_hidden_arc_png_is_dashed_and_dxf_pattern_has_strokes(tmp_path):
    from aiad_drafting.export import write_png
    from PIL import Image, ImageChops
    a=arc((20,20),10,20,160,True,dash=True)
    b=arc((20,20),10,20,160,True,dash=False)
    write_png(tmp_path/"dashed.png",50,40,[a])
    write_png(tmp_path/"solid.png",50,40,[b])
    with Image.open(tmp_path/"dashed.png") as d, Image.open(tmp_path/"solid.png") as solid:
        assert ImageChops.difference(d,solid).getbbox() is not None
        count=lambda im: sum(im.convert("L").histogram()[:128])
        assert 10 < count(d) < count(solid)*0.9
    write_dxf(tmp_path/"hidden.dxf",50,40,[a])
    doc=readfile(tmp_path/"hidden.dxf")
    pattern=doc.linetypes.get("AIAD_HIDDEN").pattern_tags.compile()
    assert pattern == pytest.approx((1.4,0.9))
