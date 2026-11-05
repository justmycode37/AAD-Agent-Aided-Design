from __future__ import annotations

import json
import math

import pytest

from aad_drafting.model2d import Entity2D, Point2D, Sheet2D, latest_sheet, sheet2d


@pytest.mark.parametrize("invalid", [
    lambda s: s.circle("bad", (1, 2), 0),
    lambda s: s.arc("bad", (1, 2), -1, 0, 90),
    lambda s: s.text("bad", "label", (1, 2), 0, layer="new"),
    lambda s: s.line("bad", (1, 2), (1, 2), layer="new"),
    lambda s: s.polyline("bad", [(1, 2), (3, 4), "missing"]),
    lambda s: s.rectangle("bad", 0, 5, origin=(1, 2)),
])
def test_failed_primitives_restore_points_layers_history_and_allow_retry(invalid):
    sheet = sheet2d("atomic")
    sheet.line("existing", (0, 0), (10, 0))
    before = sheet.to_dict()
    current = sheet2d("other")
    with pytest.raises(ValueError):
        invalid(sheet)
    assert sheet.to_dict() == before
    assert latest_sheet() is current
    assert sheet.circle("bad", (3, 4), 1).name == "bad"


def test_named_points_primitives_layers_and_exact_native_edges():
    sheet = sheet2d("primitives")
    origin = sheet.point("origin", 0, 0)
    assert sheet.point("origin", 0, 0) is origin

    line = sheet.line("baseline", origin, (30, 0))
    circle = sheet.circle("hole", (10, 12), 2.5)
    arc = sheet.arc("arc", (20, 12), 4, 350, 10)
    polyline = sheet.polyline("open_path", [(0, 20), (5, 25), (12, 20)])
    rectangle = sheet.rectangle("plate", 40, 30, origin=(0, 0))
    label = sheet.text("mark", "AAD", (3, 4), 2.5)

    assert line.points == ("origin", "baseline.end")
    assert circle.points == ("hole.center",)
    assert arc.points == ("arc.center",)
    assert polyline.points == ("open_path.p0", "open_path.p1", "open_path.p2")
    assert rectangle.kind == "rectangle" and rectangle.closed
    assert rectangle.points == tuple(f"plate.p{i}" for i in range(4))
    assert label.kind == "text" and label.layer == "engrave" and label.text == "AAD"
    assert {"cut", "score", "engrave"}.issubset(sheet.layers)
    assert sheet.points["origin"] is origin

    shared = Point2D("shared", 50, 0)
    sheet.line("shared_a", shared, (60, 0))
    sheet.line("shared_b", shared, (50, 10))
    assert sheet.points["shared"] is shared
    assert sheet.entities["shared_a"].points[0] == sheet.entities["shared_b"].points[0] == "shared"

    shape = sheet.to_shape()
    curves = [edge.geom_type.name for edge in shape.edges()]
    assert "LINE" in curves
    assert "CIRCLE" in curves
    assert any(edge.is_closed and edge.geom_type.name == "CIRCLE" for edge in shape.edges())
    native_arcs = [
        edge for edge in shape.edges()
        if edge.geom_type.name == "CIRCLE" and not edge.is_closed and edge.radius == pytest.approx(4)
    ]
    assert len(native_arcs) == 1
    assert native_arcs[0].length == pytest.approx(4 * math.radians(20), abs=1e-7)

    clockwise_sheet = sheet2d("clockwise")
    clockwise_sheet.arc("arc", (0, 0), 3, 10, 350, clockwise=True)
    clockwise_edge = clockwise_sheet.to_shape().edges()[0]
    start = clockwise_edge.start_point()
    tangent = clockwise_edge.tangent_at(0)
    assert clockwise_edge.length == pytest.approx(3 * math.radians(20), abs=1e-7)
    assert start.X * tangent.Y - start.Y * tangent.X < 0
    with pytest.raises(ValueError, match="Editable text"):
        sheet.to_shape(layers=("engrave",))


def test_exact_face_contains_nested_circle_hole_and_native_bounding_box():
    sheet = sheet2d("plate")
    sheet.rectangle("outline", 40, 30, origin=(0, 0))
    sheet.circle("hole", (20, 15), 5)

    face = sheet.to_face()
    assert face.is_valid
    assert face.area == pytest.approx(40 * 30 - math.pi * 5**2, abs=1e-7)
    bounds = sheet.bounding_box()
    assert (bounds.min.X, bounds.min.Y) == pytest.approx((0, 0), abs=1e-7)
    assert (bounds.max.X, bounds.max.Y) == pytest.approx((40, 30), abs=1e-7)

    nested = sheet2d("nested")
    nested.rectangle("outer", 40, 30)
    nested.circle("hole", (20, 15), 5)
    nested.circle("island", (20, 15), 2)
    nested_faces = nested.to_face().faces()
    assert sum(face.area for face in nested_faces) == pytest.approx(
        40 * 30 - math.pi * 5**2 + math.pi * 2**2,
        abs=1e-7,
    )


def test_sheet2d_dict_round_trip_preserves_native_entities_layers_history_and_constraints():
    sheet = sheet2d("fixture", units="in")
    datum = sheet.point("datum", 1.25, 2.5)
    sheet.line("edge", datum, (3, 2.5), layer="score")
    sheet.arc("sweep", (5, 5), 2, 10, 80, clockwise=True)
    sheet.text("label", "Editable", (2, 4), 0.2, layer="engrave")
    sheet.constraints.append({"kind": "fixed", "point": "datum"})

    data = json.loads(json.dumps(sheet.to_dict(), allow_nan=False))
    restored = Sheet2D.from_dict(data)

    assert restored.to_dict() == data
    assert restored.units == "in"
    assert restored.entities["edge"].points[0] == "datum"
    assert restored.points[restored.entities["edge"].points[0]] is restored.points["datum"]
    assert restored.entities["sweep"].metadata["clockwise"] is True
    assert restored.entities["label"].text == "Editable"
    assert restored.layers["score"] == sheet.layers["score"]
    assert restored.constraints == [{"kind": "fixed", "point": "datum"}]
    assert any(item["operation"] == "primitive" for item in restored.history)


def test_exact_offset_boolean_holes_patterns_extrusion_and_history():
    plate = sheet2d("laser_plate")
    plate.rectangle("outline", 40, 30, origin=(0, 0))
    plate.circle("pilot", (10, 15), 3)

    drill = sheet2d("drill")
    drill.circle("clearance", (28, 15), 4)
    cut = plate.difference(drill)
    result_face = cut.to_face()
    assert result_face.is_valid
    assert result_face.area == pytest.approx(40 * 30 - math.pi * 3**2 - math.pi * 4**2, abs=1e-6)
    result_circles = [entity for entity in cut.entities.values() if entity.kind == "circle"]
    assert sorted(entity.radius for entity in result_circles) == pytest.approx([3, 4])
    assert len([edge for edge in cut.to_shape().edges() if edge.geom_type.name == "CIRCLE"]) == 2
    boolean_history = cut.history[-1]
    assert boolean_history["operation"] == "difference"
    assert boolean_history["sources"][0]["entities"][0]["name"] == "outline"
    assert boolean_history["sources"][1]["entities"][0]["name"] == "clearance"
    assert boolean_history["parameters"]["other_layers"] == ["cut"]

    expanded = plate.offset(1)
    offset_circles = [entity for entity in expanded.entities.values() if entity.kind == "circle"]
    assert len(offset_circles) == 1
    assert offset_circles[0].radius == pytest.approx(4)
    assert expanded.history[-1]["parameters"]["distance"] == 1
    assert "pilot" in expanded.history[-1]["source_names"]

    patterned = plate.linear_pattern("pilot", count=3, spacing=(10, 0))
    assert [patterned.points[patterned.entities[name].points[0]].x for name in (
        "pilot", "pilot.linear1", "pilot.linear2"
    )] == pytest.approx([10, 20, 30])
    assert patterned.history[-1]["parameters"]["spacing"] == [10, 0]
    assert patterned.history[-1]["generated_names"] == ["pilot.linear1", "pilot.linear2"]
    assert [entity["name"] for entity in patterned.history[-1]["source_geometry"]["entities"]] == ["pilot"]

    radial_source = sheet2d("radial")
    radial_source.circle("bolt", (10, 0), 1)
    radial = radial_source.radial_pattern("bolt", count=4, center=(0, 0))
    centers = [
        radial.points[radial.entities["bolt" if i == 0 else f"bolt.radial{i}"].points[0]].as_tuple()
        for i in range(4)
    ]
    expected_centers = [(10, 0), (0, 10), (-10, 0), (0, -10)]
    for actual, expected in zip(centers, expected_centers):
        assert actual == pytest.approx(expected, abs=1e-7)

    solid = cut.extrude(5)
    assert solid.volume == pytest.approx(result_face.area * 5, abs=1e-6)
    assert solid.is_valid
    assert solid.aad_history[-1]["operation"] == "extrude"
    assert solid.aad_history[-1]["parameters"]["height"] == 5
    assert solid.aad_source_sheet == cut.to_dict()


def test_union_and_intersection_keep_exact_circular_arcs():
    first = sheet2d("first")
    first.circle("left", (0, 0), 5)
    second = sheet2d("second")
    second.circle("right", (5, 0), 5)

    overlap = 2 * 5**2 * math.acos(5 / 10) - 0.5 * 5 * math.sqrt(4 * 5**2 - 5**2)
    union = first.union(second)
    intersection = first.intersection(second)
    assert union.to_face().area == pytest.approx(2 * math.pi * 5**2 - overlap, abs=1e-6)
    assert intersection.to_face().area == pytest.approx(overlap, abs=1e-6)
    assert {entity.kind for entity in union.entities.values()} == {"arc"}
    assert {entity.kind for entity in intersection.entities.values()} == {"arc"}
    assert all(entity.radius == pytest.approx(5) for entity in intersection.entities.values())


def test_latest_sheet_and_context_manager_are_available_to_the_worker():
    sheet = sheet2d("active")
    assert latest_sheet() is sheet
    with sheet2d("scoped") as scoped:
        assert latest_sheet() is scoped
    assert latest_sheet() is scoped


@pytest.mark.parametrize(
    "build",
    [
        lambda sheet: sheet.line("zero", (1, 1), (1, 1)),
        lambda sheet: sheet.circle("zero", (0, 0), 0),
        lambda sheet: sheet.arc("zero", (0, 0), 1, 0, 360),
        lambda sheet: sheet.rectangle("zero", 10, 0),
        lambda sheet: sheet.polyline("zero", [(0, 0), (1, 1), (2, 2)], closed=True),
        lambda sheet: sheet.point("bad", float("inf"), 1),
    ],
)
def test_zero_or_nonfinite_primitive_geometry_is_rejected(build):
    with pytest.raises(ValueError):
        build(sheet2d("invalid"))


def test_zero_offset_and_zero_extrusion_are_rejected():
    sheet = sheet2d("plate")
    sheet.rectangle("outline", 10, 10)
    with pytest.raises(ValueError, match="Offset distance"):
        sheet.offset(0)
    with pytest.raises(ValueError, match="Extrusion height"):
        sheet.extrude(0)


def test_entity2d_is_a_public_dataclass_with_only_named_point_references():
    entity = Entity2D("segment", "line", "cut", ("a", "b"))
    assert entity.points == ("a", "b")
    point = Point2D("a", 1, 2)
    assert point.as_tuple() == (1, 2)
