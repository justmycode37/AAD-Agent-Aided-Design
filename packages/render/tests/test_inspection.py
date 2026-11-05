"""Inspection answers are checked against geometry whose true values are known by construction."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from build123d import Axis, Box, Cylinder, Pos, Rot, Sphere, fillet
from PIL import Image

from aad_render import render
from aad_render import exact


def _ctx(shape, features=()):
    return SimpleNamespace(shape=shape, units="mm", features=list(features))


def _render(ctx, req, out):
    try:
        return render(ctx, req, out)
    except Exception as exc:
        if sys.platform.startswith("linux") and "headless gl" in str(exc).lower():
            pytest.skip(str(exc))
        raise


def _inspect(ctx, req, out):
    paths = _render(ctx, req, out)
    return json.loads(next(p for p in paths if p.name == "inspection.json").read_text()), paths


def _face(report, **wanted):
    return [f for f in report["faces"] if all(f.get(k) == v for k, v in wanted.items())]


def _pixel(pane, point):
    matrix = np.asarray(pane["world_to_pixel"])
    return matrix[:, :3] @ np.asarray(point, dtype=float) + matrix[:, 3]


def _plate_with_hole():
    return Box(40, 20, 6) - Pos(5, 2, 0) * Cylinder(3, 20)


# ---- exact facts


def test_face_facts_give_true_hole_size_position_and_kind(tmp_path):
    report, _ = _inspect(_ctx(_plate_with_hole()), {"views": ["top"], "size": 256}, tmp_path)
    hole = _face(report, type="cylinder")
    assert len(hole) == 1
    assert hole[0]["diameter"] == pytest.approx(6.0, abs=1e-9)
    assert hole[0]["internal"] is True
    assert hole[0]["axial_length"] == pytest.approx(6.0, abs=1e-9)
    assert hole[0]["axis_point"][:2] == pytest.approx([5.0, 2.0], abs=1e-9)
    assert report["bbox"]["size"] == pytest.approx([40.0, 20.0, 6.0], abs=1e-6)
    assert report["volume"] == pytest.approx(40 * 20 * 6 - math.pi * 9 * 6, rel=1e-9)
    assert report["counts"] == {"faces": 7, "edges": len(report["edges"]), "solids": 1}


def test_boss_is_not_called_a_hole(tmp_path):
    boss = Box(20, 20, 4) + Pos(0, 0, 5) * Cylinder(3, 8)
    report, _ = _inspect(_ctx(boss), {"views": ["top"], "size": 128}, tmp_path)
    assert _face(report, type="cylinder")[0]["internal"] is False


def test_edge_classes_tell_creases_from_fillet_tangents_and_seams(tmp_path):
    part = fillet(Box(30, 30, 10).edges().filter_by(Axis.Z), 3) - Cylinder(4, 30)
    report, _ = _inspect(_ctx(part), {"views": ["top"], "size": 128}, tmp_path)
    by_class = {}
    for edge in report["edges"]:
        by_class.setdefault(edge["class"], []).append(edge)
    # Four fillets, each meeting two side walls smoothly.
    assert len(by_class["tangent"]) == 8
    assert all(edge["normal_angle_deg"] < 1.0 for edge in by_class["tangent"])
    assert all(edge["type"] == "line" for edge in by_class["tangent"])
    assert len(by_class["seam"]) == 1  # The through hole's cylinder.
    assert all(edge["normal_angle_deg"] == pytest.approx(90.0, abs=1e-6) for edge in by_class["sharp"])


def test_section_outline_is_exact(tmp_path):
    report, _ = _inspect(
        _ctx(_plate_with_hole()),
        {"views": ["iso"], "section": {"plane": "XY", "offset": 1.0}, "size": 256},
        tmp_path,
    )
    section = report["section"]
    assert section["total_area"] == pytest.approx(40 * 20 - math.pi * 9, rel=1e-9)
    assert len(section["regions"]) == 1
    loops = section["regions"][0]["loops"]
    assert [loop["outer"] for loop in loops] == [True, False]
    assert loops[0]["perimeter"] == pytest.approx(120.0, abs=1e-9)
    assert sorted(segment["length"] for segment in loops[0]["segments"]) == pytest.approx([20, 20, 40, 40], abs=1e-9)
    segments = loops[0]["segments"]
    for segment, following in zip(segments, segments[1:] + segments[:1]):
        assert segment["end"] == pytest.approx(following["start"], abs=1e-9)
    circle = loops[1]["segments"][0]
    assert circle["type"] == "circle"
    assert circle["radius"] == pytest.approx(3.0, abs=1e-9)
    assert circle["center"][:2] == pytest.approx([5.0, 2.0], abs=1e-9)
    assert circle["center"][2] == pytest.approx(1.0, abs=1e-9)


# ---- pixels and world


def test_world_to_pixel_matches_the_pixels_actually_drawn(tmp_path):
    report, paths = _inspect(_ctx(Box(40, 20, 6)), {"views": ["top"], "size": 512, "dimensions": "none"}, tmp_path)
    pane = report["images"]["top.png"]["panes"][0]
    top = next(f for f in report["faces"] if f.get("normal") == [0.0, 0.0, 1.0])
    seen = next(item for item in pane["visible"] if item["selector"] == top["selector"])
    corner_low = _pixel(pane, (-20, 10, 3))
    corner_high = _pixel(pane, (20, -10, 3))
    assert seen["box"][0] == pytest.approx(corner_low[0], abs=1.0)
    assert seen["box"][1] == pytest.approx(corner_low[1], abs=1.0)
    assert seen["box"][2] == pytest.approx(corner_high[0], abs=1.0)
    assert seen["box"][3] == pytest.approx(corner_high[1], abs=1.0)
    assert pane["pixels_per_unit"] * 40 == pytest.approx(seen["box"][2] - seen["box"][0], abs=1.5)


def test_each_view_lists_only_what_it_shows(tmp_path):
    report, _ = _inspect(_ctx(Box(10, 10, 10)), {"views": ["iso", "top"], "size": 256}, tmp_path)
    assert len(report["images"]["iso.png"]["panes"][0]["visible"]) == 3
    assert len(report["images"]["top.png"]["panes"][0]["visible"]) == 1


def test_sheet_panes_share_one_orthographic_scale(tmp_path):
    report, _ = _inspect(_ctx(Box(100, 10, 30)), {"views": ["sheet"], "size": 1024}, tmp_path)
    panes = {pane["view"]: pane for pane in report["images"]["sheet.png"]["panes"]}
    assert panes["front"]["pixels_per_unit"] == panes["right"]["pixels_per_unit"] == panes["top"]["pixels_per_unit"]
    assert panes["front"]["rect"][:2] != panes["iso"]["rect"][:2]


def test_probe_at_a_pixel_reports_the_exact_point_and_wall(tmp_path):
    shape = _plate_with_hole()
    first, _ = _inspect(_ctx(shape), {"views": ["top"], "size": 512}, tmp_path / "a")
    pane = first["images"]["top.png"]["panes"][0]
    x, y = _pixel(pane, (-10, -4, 3))
    x_hole, y_hole = _pixel(pane, (5, 2, 3))
    report, _ = _inspect(
        _ctx(shape),
        {"views": ["top"], "size": 512, "probes": [{"image": "top", "x": x, "y": y}, {"image": "top", "x": x_hole, "y": y_hole}]},
        tmp_path / "b",
    )
    probe, through_hole = report["probes"]
    assert probe["hit"] is True and probe["surface"] == "plane"
    assert probe["point"] == pytest.approx([-10, -4, 3], abs=1e-6)
    assert probe["normal"] == pytest.approx([0, 0, 1], abs=1e-9)
    assert probe["wall_thickness"] == pytest.approx(6.0, abs=1e-6)
    assert probe["selector"].startswith("face:")
    assert through_hole["hit"] is False


def test_probe_finds_the_right_pane_of_a_sheet(tmp_path):
    shape = Box(40, 20, 6)
    first, _ = _inspect(_ctx(shape), {"views": ["sheet"], "size": 1024}, tmp_path / "a")
    pane = next(p for p in first["images"]["sheet.png"]["panes"] if p["view"] == "top")
    x, y = _pixel(pane, (7, 3, 3))
    report, _ = _inspect(_ctx(shape), {"views": ["sheet"], "size": 1024, "probes": [{"image": "sheet", "x": x, "y": y}]}, tmp_path / "b")
    assert report["probes"][0]["pixel"]["view"] == "top"
    assert report["probes"][0]["point"] == pytest.approx([7, 3, 3], abs=1e-6)


def test_world_ray_probe_on_a_curved_face_reads_radius_and_wall(tmp_path):
    tube = Cylinder(10, 30) - Cylinder(8, 40)
    report, _ = _inspect(
        _ctx(tube),
        {"views": ["top"], "size": 128, "probes": [{"point": [0, -50, 0], "direction": [0, 1, 0]}]},
        tmp_path,
    )
    probe = report["probes"][0]
    assert probe["surface"] == "cylinder"
    assert probe["point"] == pytest.approx([0, -10, 0], abs=1e-6)
    assert probe["min_radius"] == pytest.approx(10.0, abs=1e-6)
    assert probe["wall_thickness"] == pytest.approx(2.0, abs=1e-6)


def test_world_ray_can_start_far_from_a_small_part(tmp_path):
    report, _ = _inspect(_ctx(Box(4, 4, 4)), {
        "views": ["top"], "size": 128,
        "probes": [{"point": [0, 0, 10000], "direction": [0, 0, -1]}],
    }, tmp_path)
    assert report["probes"][0]["point"] == pytest.approx([0, 0, 2], abs=1e-6)


@pytest.mark.parametrize(
    "probe",
    [{"image": "top"}, {"image": "front", "x": 1, "y": 1}, {"image": "top", "x": 9000, "y": 1}, {"point": [0, 0, 0]}],
)
def test_bad_probes_are_rejected(tmp_path, probe):
    with pytest.raises(ValueError):
        _render(_ctx(Box(4, 4, 4)), {"views": ["top"], "size": 128, "probes": [probe]}, tmp_path)


def test_measure_gives_true_distance_angle_and_closest_points(tmp_path):
    box = Box(30, 20, 10)
    probe, _ = _inspect(_ctx(box), {"views": ["top"], "size": 128}, tmp_path / "a")
    top = next(f["selector"] for f in probe["faces"] if f.get("normal") == [0.0, 0.0, 1.0])
    bottom = next(f["selector"] for f in probe["faces"] if f.get("normal") == [0.0, 0.0, -1.0])
    side = next(f["selector"] for f in probe["faces"] if f.get("normal") == [1.0, 0.0, 0.0])
    report, _ = _inspect(
        _ctx(box),
        {
            "views": ["iso"],
            "size": 256,
            "measure": [{"a": top, "b": bottom}, {"a": top, "b": side}, {"a": "point:(0,0,0)", "b": "point:(3,4,0)"}],
        },
        tmp_path / "b",
    )
    parallel, touching, points = report["measurements"]
    assert parallel["distance"] == pytest.approx(10.0, abs=1e-9)
    assert parallel["angle_deg"] == pytest.approx(180.0, abs=1e-6)
    assert touching["distance"] == pytest.approx(0.0, abs=1e-9)
    assert touching["angle_deg"] == pytest.approx(90.0, abs=1e-6)
    assert points["distance"] == pytest.approx(5.0, abs=1e-9)
    assert points["delta"] == pytest.approx([3, 4, 0], abs=1e-9)


def test_unknown_selector_says_what_is_available(tmp_path):
    with pytest.raises(ValueError, match="this revision has 6"):
        _render(_ctx(Box(4, 4, 4)), {"views": ["iso"], "size": 128, "focus": "face:99"}, tmp_path)


# ---- zooming


def test_focus_zooms_to_a_face_and_refines_the_mesh(tmp_path):
    part = Box(200, 200, 10) - Pos(90, 90, 0) * Cylinder(1, 30)
    whole, _ = _inspect(_ctx(part), {"views": ["top"], "size": 512}, tmp_path / "a")
    hole = _face(whole, type="cylinder")[0]["selector"]
    close, paths = _inspect(_ctx(part), {"views": ["top"], "size": 512, "focus": hole, "dimensions": "none"}, tmp_path / "b")
    pane = close["images"]["top.png"]["panes"][0]
    assert pane["pixels_per_unit"] > whole["images"]["top.png"]["panes"][0]["pixels_per_unit"] * 20
    # The whole hole fits, at its true position.
    center = _pixel(pane, (90, 90, 5))
    assert center == pytest.approx([256, 256], abs=2.0)
    assert 2.0 * pane["pixels_per_unit"] > 100
    image = np.asarray(Image.open(paths[0]))
    # The world grid crosses the hole's center; most pixels around it must still be background.
    assert (image[246:266, 246:266].min(axis=2) > 235).mean() > 0.8
    assert image[256, 256 + 120].max() < 225  # And solid plate beyond it.


def test_focus_on_a_box_with_clipping_hides_what_is_outside(tmp_path):
    part = Box(40, 40, 10) + Pos(0, 0, 30) * Box(10, 10, 50)
    open_view, _ = _inspect(
        _ctx(part),
        {"views": ["top"], "size": 256, "focus": {"min": [-20, -20, -5], "max": [20, 20, 5]}},
        tmp_path / "a",
    )
    clipped, _ = _inspect(
        _ctx(part),
        {"views": ["top"], "size": 256, "focus": {"min": [-20, -20, -5], "max": [20, 20, 5], "clip": True}},
        tmp_path / "b",
    )
    def height_of_top(report):
        return report["images"]["top.png"]["panes"][0]["visible"]

    assert len(height_of_top(clipped)) >= 1
    assert height_of_top(open_view) != height_of_top(clipped)


def test_custom_camera_uses_its_direction_target_and_span(tmp_path):
    report, paths = _inspect(
        _ctx(Box(40, 20, 6)),
        {"camera": {"direction": [1, 1, 1], "target": [10, 0, 0], "span": 8}, "size": 400},
        tmp_path,
    )
    assert paths[0].name == "custom.png"
    pane = report["images"]["custom.png"]["panes"][0]
    assert pane["pixels_per_unit"] == pytest.approx(50.0)
    assert _pixel(pane, (10, 0, 0)) == pytest.approx([200, 200], abs=1e-6)
    assert pane["toward_camera"] == pytest.approx([1 / math.sqrt(3)] * 3, abs=1e-8)


def test_focus_keeps_the_camera_outside_the_whole_part(tmp_path):
    # Focusing on the bottom must still show the top occluding it, unless clipping was requested.
    part = Box(10, 10, 1000)
    settings = {"min": [-1, -1, -500], "max": [1, 1, -499], "margin": 0}
    request = {"views": ["top"], "size": 128, "focus": settings,
               "probes": [{"image": "top", "x": 64, "y": 64}]}
    report, _ = _inspect(_ctx(part), request, tmp_path / "whole")
    hit = report["probes"][0]
    assert hit["point"] == pytest.approx([0, 0, 500], abs=1e-6)
    assert hit["selector"] in [v["selector"] for v in report["images"]["top.png"]["panes"][0]["visible"]]
    report, _ = _inspect(_ctx(part), {**request, "focus": {**settings, "clip": True}}, tmp_path / "clipped")
    assert report["probes"][0]["point"] == pytest.approx([0, 0, -500], abs=1e-6)


@pytest.mark.parametrize(
    "req",
    [
        {"views": ["custom"]},
        {"camera": {}},
        {"camera": {"direction": [0, 0, 0]}},
        {"camera": {"direction": [1, 0, 0], "up": [2, 0, 0]}},
        {"camera": {"direction": [1, 0, 0], "span": -1}},
        {"dimensions": "maybe"},
        {"min_wall": 0},
        {"heatmap_range": [5, 1]},
        {"direction": [0, 0]},
        {"focus": {"min": [1, 1, 1], "max": [0, 0, 0]}},
    ],
)
def test_bad_requests_are_rejected(tmp_path, req):
    with pytest.raises(ValueError):
        _render(_ctx(Box(4, 4, 4)), {"size": 128, **req}, tmp_path)


# ---- thickness


def test_thinnest_wall_of_a_tube_is_exact(tmp_path):
    report, _ = _inspect(_ctx(Cylinder(10, 30) - Cylinder(8, 40)), {"views": ["iso"], "heatmap": "thickness", "size": 256}, tmp_path)
    thinnest = report["heatmap"]["thinnest"]
    assert thinnest["thickness"] == pytest.approx(2.0, abs=1e-6)
    assert thinnest["method"].startswith("brep")
    gap = np.linalg.norm(np.asarray(thinnest["point_a"]) - np.asarray(thinnest["point_b"]))
    assert gap == pytest.approx(2.0, abs=1e-6)


def test_small_thin_spot_on_a_large_flat_face_is_found(tmp_path):
    # The two big faces are two triangles each. Sampling only triangle centers would never see this.
    plate = Box(200, 200, 10) - Pos(61, -47, 2) * Cylinder(3, 12)  # Floor from z=-5 to z=-4.
    report, _ = _inspect(_ctx(plate), {"views": ["iso"], "heatmap": "thickness", "size": 256}, tmp_path)
    thinnest = report["heatmap"]["thinnest"]
    assert thinnest["thickness"] == pytest.approx(1.0, abs=1e-6)
    assert thinnest["point_a"][:2] == pytest.approx(thinnest["point_b"][:2], abs=1e-6)
    assert math.hypot(thinnest["point_a"][0] - 61, thinnest["point_a"][1] + 47) <= 3.0 + 1e-6


def test_min_wall_flags_only_real_violations(tmp_path):
    tube = Cylinder(10, 30) - Cylinder(8, 40)
    at_limit, _ = _inspect(_ctx(tube), {"views": ["iso"], "heatmap": "thickness", "min_wall": 2.0, "size": 256}, tmp_path / "a")
    assert at_limit["heatmap"]["rays_below_min_wall"] == 0
    above, _ = _inspect(_ctx(tube), {"views": ["iso"], "heatmap": "thickness", "min_wall": 2.5, "size": 256}, tmp_path / "b")
    assert above["heatmap"]["rays_below_min_wall"] > 0


def test_thickness_ray_count_stays_bounded_on_curved_parts(tmp_path):
    report, _ = _inspect(_ctx(Cylinder(20, 200) - Cylinder(18, 300)), {"views": ["iso"], "heatmap": "thickness", "size": 512}, tmp_path)
    assert report["heatmap"]["rays"] < 120_000


def test_section_thickness_measures_the_uncut_solid(tmp_path):
    tube = Cylinder(10, 30) - Cylinder(8, 40)
    report, _ = _inspect(
        _ctx(tube),
        {"views": ["iso"], "heatmap": "thickness", "section": {"plane": "YZ", "offset": 0}, "size": 256},
        tmp_path,
    )
    assert report["heatmap"]["thinnest"]["thickness"] == pytest.approx(2.0, abs=1e-6)
    assert all(item["min"] > 1.9 for item in report["heatmap"]["per_face"])


# ---- other heatmaps


def test_draft_reads_true_angles_and_finds_undrafted_area(tmp_path):
    report, _ = _inspect(_ctx(Box(10, 20, 30)), {"views": ["iso"], "heatmap": "draft", "direction": [0, 0, 1], "size": 128}, tmp_path)
    heat = report["heatmap"]
    assert heat["total_area"] == pytest.approx(2 * (10 * 20 + 10 * 30 + 20 * 30), abs=1e-6)
    assert heat["area_under_1deg"] == pytest.approx(2 * (10 * 30 + 20 * 30), abs=1e-6)
    assert {round(item["min_deg"]) for item in heat["per_face"]} == {0, 90, -90}


def test_overhang_uses_the_requested_build_direction(tmp_path):
    report, _ = _inspect(_ctx(Box(10, 20, 30)), {"views": ["iso"], "heatmap": "overhang", "direction": [1, 0, 0], "size": 128}, tmp_path)
    assert report["heatmap"]["area_facing_straight_down"] == pytest.approx(20 * 30, abs=1e-6)


def test_curvature_reads_true_radius_and_leaves_flats_unmarked(tmp_path):
    report, _ = _inspect(_ctx(Cylinder(5, 20)), {"views": ["iso"], "heatmap": "curvature", "size": 128}, tmp_path / "a")
    assert report["heatmap"]["min_radius"] == pytest.approx(5.0, abs=1e-6)
    assert len(report["heatmap"]["per_face"]) == 1
    report, _ = _inspect(_ctx(Sphere(7)), {"views": ["iso"], "heatmap": "curvature", "size": 128}, tmp_path / "b")
    assert report["heatmap"]["min_radius"] == pytest.approx(7.0, abs=1e-6)


# ---- picture content


def _dark(path):
    return (np.asarray(Image.open(path)).astype(int).sum(axis=2) < 200).sum()


def test_curved_silhouettes_get_an_outline_with_no_brep_edge_there(tmp_path):
    # A sphere has no visible edges from this side, so any outline comes from the depth pass.
    paths = _render(_ctx(Sphere(10)), {"views": ["top"], "size": 512, "dimensions": "none"}, tmp_path)
    image = np.asarray(Image.open(paths[0])).astype(int)
    row = image[256].sum(axis=1)
    assert row[256] > 300
    assert (row < 260).sum() >= 2  # Dark outline where the sphere meets the background.


def test_hidden_edges_show_in_line_style(tmp_path):
    # A pocket cut from below: its edges are all hidden from above.
    part = Box(40, 40, 10) - Pos(0, 0, -3) * Box(12, 12, 6)
    paths = _render(_ctx(part), {"views": ["top"], "size": 512, "style": "lines", "dimensions": "none"}, tmp_path)
    report = json.loads(paths[1].read_text())
    pane = report["images"]["top.png"]["panes"][0]
    x, y = _pixel(pane, (0, 6, 5))
    image = np.asarray(Image.open(paths[0])).astype(int)
    around = image[int(y) - 2 : int(y) + 3, int(x)]
    assert around.sum(axis=1).min() < 3 * 225  # A line where the hidden pocket wall runs.
    assert around.sum(axis=1).min() > 3 * 120  # Lighter than a visible edge.


def test_dimensions_can_be_turned_off(tmp_path):
    on = _render(_ctx(Box(40, 20, 6)), {"views": ["top"], "size": 512}, tmp_path / "a")[0]
    off = _render(_ctx(Box(40, 20, 6)), {"views": ["top"], "size": 512, "dimensions": "none"}, tmp_path / "b")[0]
    assert on.read_bytes() != off.read_bytes()


def test_earlier_finer_render_does_not_change_later_output(tmp_path):
    shape = Cylinder(10, 20)
    before = _render(_ctx(shape), {"views": ["iso"], "size": 256}, tmp_path / "a")[0].read_bytes()
    _render(_ctx(shape), {"views": ["iso"], "size": 2048}, tmp_path / "fine")
    _render(_ctx(shape), {"views": ["iso"], "size": 256, "focus": {"min": [9, -1, 0], "max": [10, 1, 1]}}, tmp_path / "zoom")
    after = _render(_ctx(shape), {"views": ["iso"], "size": 256}, tmp_path / "b")[0].read_bytes()
    assert before == after


# ---- engine selectors (issue 43)


def test_local_selectors_never_reach_the_engine_resolver(monkeypatch, tmp_path):
    import types

    calls = []

    def resolve(ctx, selector, expect=None):
        calls.append(selector)
        return [SimpleNamespace(kind="face", index=1, feature=None)]

    select = types.ModuleType("aad.engine.select")
    select.resolve = resolve
    monkeypatch.setitem(sys.modules, "aad", types.ModuleType("aad"))
    monkeypatch.setitem(sys.modules, "aad.engine", types.ModuleType("aad.engine"))
    monkeypatch.setitem(sys.modules, "aad.engine.select", select)
    ctx = _ctx(Box(4, 4, 4), [{"name": "body", "faces": [0, 2]}])
    _render(
        ctx,
        {"views": ["iso"], "size": 128, "highlight": ["face:0", "edge:0", "feature:body", "face(where='max_z')"]},
        tmp_path,
    )
    assert calls == ["face(where='max_z')"]
    with pytest.raises(ValueError, match="no matching face"):
        _render(ctx, {"views": ["iso"], "size": 128, "highlight": ["face:99"]}, tmp_path)


# ---- repeatability


CHILD = """
import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from build123d import Box, Cylinder, Pos
from aad_render import render
part = Box(40, 30, 10) - Pos(5, 2, 0) * Cylinder(3, 30)
request = ast.literal_eval(sys.argv[2])
render(SimpleNamespace(shape=part, units="mm", features=[]), request, Path(sys.argv[1]))
"""


@pytest.mark.parametrize(
    "request_text",
    [
        "{'views': ['sheet']}",
        "{'views': ['iso'], 'heatmap': 'thickness', 'min_wall': 3}",
        "{'views': ['iso'], 'section': {'plane': 'XZ', 'offset': 2}, 'heatmap': 'curvature'}",
        "{'views': ['top'], 'focus': {'min': [0, 0, 0], 'max': [8, 8, 5]}, 'probes': [{'image': 'top', 'x': 500, 'y': 500}]}",
        "{'views': ['front'], 'style': 'lines', 'measure': [{'a': 'face:0', 'b': 'face:3'}]}",
    ],
)
def test_images_and_reports_are_identical_in_two_processes(tmp_path, request_text):
    runs = []
    for name in ("a", "b"):
        out = tmp_path / name
        result = subprocess.run([sys.executable, "-c", CHILD, str(out), request_text], capture_output=True, text=True)
        if result.returncode and sys.platform.startswith("linux") and "headless gl" in result.stderr.lower():
            pytest.skip(result.stderr[-200:])
        assert result.returncode == 0, result.stderr
        runs.append({path.name: path.read_bytes() for path in sorted(out.iterdir())})
    assert runs[0] == runs[1]


def test_exact_module_has_no_import_time_kernel_dependency():
    code = "import sys, aad_render.exact; print('OCP' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip() == "False"


def test_report_needs_no_mesh_for_its_numbers(tmp_path):
    """The same facts at any render size: nothing in them may come from the mesh."""
    part = Box(40, 20, 6) - Pos(5, 2, 0) * Cylinder(3, 20)
    small, _ = _inspect(_ctx(part), {"views": ["top"], "size": 128}, tmp_path / "a")
    large, _ = _inspect(_ctx(part), {"views": ["top"], "size": 1024}, tmp_path / "b")
    for key in ("faces", "edges", "bbox", "volume", "area"):
        assert small[key] == large[key]
