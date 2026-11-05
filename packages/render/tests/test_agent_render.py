from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Polygon, Pos, extrude
from PIL import Image

from aad_render import render


def _bracket():
    """A wall bracket whose base counterbore nicks the upright wall."""
    base = Box(80, 50, 6)
    wall = Pos(0, 22, 25.5) * Box(80, 6, 54)
    part = base + wall
    through = Pos(0, 19, 1) * Cylinder(2.5, 12)
    counterbore = Pos(0, 19, 2) * Cylinder(5, 4)
    return part - through - counterbore


def _two_hundred_face_part():
    points = [(-50, -30), (50, -30), (50, 30)]
    for tooth in range(1, 101):
        points.append((50 - (2 * tooth - 1) * 0.5, 28))
        points.append((50 - 2 * tooth * 0.5, 30))
    return extrude(Polygon(*points, align=None), amount=6)


def _context(shape, feature_name=None):
    from types import SimpleNamespace

    features = []
    if feature_name:
        features = [
            {
                "name": feature_name,
                "faces": [{"index": index} for index, _ in enumerate(shape.faces())],
                "edges": [{"index": index} for index, _ in enumerate(shape.edges())],
            }
        ]
    return SimpleNamespace(shape=shape, features=features, units="mm")


def _render_or_skip(ctx, req, out):
    try:
        return render(ctx, req, out)
    except Exception as exc:
        if sys.platform.startswith("linux") and _is_headless_gl_failure(exc):
            pytest.skip(f"VTK has no usable EGL/OSMesa/GL context in this Linux runner: {exc}")
        raise


def _is_headless_gl_failure(exc: BaseException) -> bool:
    message = str(exc).lower()
    signals = (
        "no usable headless gl",
        "could not create egl",
        "could not connect to display",
        "bad x server connection",
        "failed to create an opengl context",
        "vtkxopenglrenderwindow",
        "no glx",
        "opengl is not supported",
        "no vtk render window",
    )
    return any(signal in message for signal in signals)


def test_bracket_sheet_has_expected_dimensions_and_feature_legend(tmp_path):
    shape = _bracket()
    paths = _render_or_skip(
        _context(shape, feature_name="wall_bracket"),
        {"views": ["sheet"], "style": "shaded", "labels": "features", "size": 1024},
        tmp_path,
    )
    assert [path.name for path in paths] == ["sheet.png", "inspection.json", "labels.json"]
    with Image.open(paths[0]) as image:
        assert image.size == (1024, 1024)
        assert image.getbbox() == (0, 0, 1024, 1024)
    legend = json.loads(paths[-1].read_text())
    assert legend
    assert all({"selector", "feature", "kind", "index"} <= set(item) for item in legend.values())
    assert list(legend.values()) == [
        {
            "selector": "feature:wall_bracket",
            "feature": "wall_bracket",
            "kind": "feature",
            "index": None,
        }
    ]


@pytest.mark.parametrize(("mode", "kind"), [("faces", "face"), ("edges", "edge")])
def test_face_and_edge_labels_write_selector_legends(tmp_path, mode, kind):
    paths = _render_or_skip(
        _context(Box(20, 10, 5)),
        {"views": ["front"], "labels": mode, "size": 192},
        tmp_path,
    )
    assert [path.name for path in paths] == ["front.png", "inspection.json", "labels.json"]
    legend = json.loads(paths[-1].read_text())
    assert legend
    assert all(item["kind"] == kind for item in legend.values())
    assert all(item["selector"].startswith(f"{kind}:") for item in legend.values())


def test_section_is_capped_and_annotated_with_the_plane(tmp_path):
    from aad_render.agent_render import _parse_section, _section_shape

    shape = _bracket()
    section = _parse_section({"plane": "XY", "offset": 15}, "mm")
    cut = _section_shape(shape, section, 100.0)
    assert cut.bounding_box().max.Z == pytest.approx(15.0, abs=1e-4)
    paths = _render_or_skip(
        _context(shape),
        {
            "views": ["iso"],
            "section": {"plane": "XY", "offset": 15},
            "style": "shaded",
            "size": 512,
        },
        tmp_path,
    )
    assert paths[0].name == "iso.png"
    assert paths[0].stat().st_size > 2_000


def test_default_section_camera_faces_and_shows_cut_cap():
    from aad_render.agent_render import _camera, _face_section_camera, _make_scene, _parse_section, _section_shape, _shape_bounds

    shape = _bracket()
    bounds = _shape_bounds(shape)
    section = _parse_section({"plane": "XZ", "offset": 0}, "mm")
    cut = _section_shape(shape, section, 100.0)
    faces, edges = cut.faces(), cut.edges()
    scene = _make_scene(cut, faces, edges, [None] * len(faces), [None] * len(edges), {}, {}, set(), set(), set(),
                        bounds=_shape_bounds(cut), units="mm", section=section, tolerance=0.01, fine=None,
                        topology=None, hatch_span=100.0, classify_edges=False)
    assert any(face.section_cap for face in scene.faces)

    default = _camera("iso", bounds, 256, 256)
    aimed = _face_section_camera(default, section, bounds, 256, 256)
    toward = tuple(aimed.position[i] - aimed.focal_point[i] for i in range(3))
    assert sum(toward[i] * section.normal[i] for i in range(3)) > 0


@pytest.mark.parametrize("style", ["shaded", "lines", "xray"])
def test_all_orthographic_cameras_and_styles_render(tmp_path, style):
    paths = _render_or_skip(
        _context(_bracket()),
        {
            "views": ["iso", "front", "back", "left", "right", "top", "bottom"],
            "style": style,
            "size": 192,
        },
        tmp_path,
    )
    assert [path.name for path in paths] == [
        "iso.png",
        "front.png",
        "back.png",
        "left.png",
        "right.png",
        "top.png",
        "bottom.png",
        "inspection.json",
    ]
    for path in paths[:-1]:
        with Image.open(path) as image:
            assert image.size == (192, 192)


@pytest.mark.parametrize("heatmap", ["thickness", "overhang"])
def test_heatmaps_render_with_real_unit_context(tmp_path, heatmap):
    paths = _render_or_skip(
        _context(_bracket()),
        {"views": ["sheet"], "heatmap": heatmap, "style": "shaded", "size": 512},
        tmp_path,
    )
    assert paths[0].name == "sheet.png"
    assert paths[0].stat().st_size > 2_000


def test_highlight_accepts_raw_face_and_edge_indices(tmp_path):
    paths = _render_or_skip(
        _context(_bracket()),
        {
            "views": ["iso"],
            "highlight": ["face:0", "edge:0"],
            "style": "shaded",
            "size": 256,
        },
        tmp_path,
    )
    assert paths[0].exists()


def test_general_section_plane_string_is_supported():
    from aad_render.agent_render import _parse_section

    section = _parse_section(
        {"plane": "point=(1,2,3);normal=(0,0,2)", "offset": 4},
        "mm",
    )
    assert section.normal == (0.0, 0.0, 1.0)
    assert section.point == pytest.approx((1.0, 2.0, 7.0))
    assert "SECTION n=" in section.label


@pytest.mark.parametrize(
    ("plane", "normal", "point"),
    [
        ("XY", (0.0, 0.0, 1.0), (0.0, 0.0, 12.0)),
        ("XZ", (0.0, 1.0, 0.0), (0.0, 12.0, 0.0)),
        ("YZ", (1.0, 0.0, 0.0), (12.0, 0.0, 0.0)),
    ],
)
def test_axis_aligned_section_offsets(plane, normal, point):
    from aad_render.agent_render import _parse_section

    section = _parse_section({"plane": plane, "offset": 12}, "mm")
    assert section.normal == normal
    assert section.point == point


@pytest.mark.parametrize(
    ("label", "request_text", "filename"),
    [
        ("plain", "views=['front'], labels='none', heatmap=None, section=None", "front.png"),
        ("sheet", "views=['sheet'], labels='none', heatmap=None, section=None", "sheet.png"),
        ("thickness", "views=['iso'], labels='none', heatmap='thickness', section=None", "iso.png"),
        ("overhang", "views=['iso'], labels='none', heatmap='overhang', section=None", "iso.png"),
        ("labels", "views=['iso'], labels='faces', heatmap=None, section=None", "iso.png"),
        ("section", "views=['iso'], labels='none', heatmap=None, section={'plane': 'YZ', 'offset': 0}", "iso.png"),
    ],
)
def test_png_is_identical_in_two_processes(tmp_path, label, request_text, filename):
    script = rf"""
import sys
from pathlib import Path
from build123d import Box, Cylinder, Pos
from aad_render import render
from types import SimpleNamespace
part = Box(40, 20, 6) + Pos(0, 7, 10) * Box(40, 6, 20) - Pos(0, -3, 0) * Cylinder(3, 10)
render(SimpleNamespace(shape=part, units='mm', features=[]),
       SimpleNamespace(style='shaded', size=256, highlight=[], {request_text}),
       Path(sys.argv[1]))
"""
    outputs = []
    for index in range(2):
        destination = tmp_path / f"process-{index}"
        result = subprocess.run(
            [sys.executable, "-c", script, str(destination)],
            text=True,
            capture_output=True,
            check=False,
            env=os.environ.copy(),
        )
        if result.returncode:
            message = result.stdout + "\n" + result.stderr
            if sys.platform.startswith("linux") and _is_headless_gl_failure(RuntimeError(message)):
                pytest.skip(f"VTK has no usable EGL/OSMesa/GL context in this Linux runner: {message}")
            pytest.fail(f"render subprocess failed ({result.returncode}): {message}")
        outputs.append((destination / filename).read_bytes())
    assert outputs[0] == outputs[1], f"{label} render differs between processes"
    with Image.open(tmp_path / "process-0" / filename) as image:
        assert image.info == {}


def _scene_for(shape):
    from aad_render.agent_render import _make_scene, _shape_bounds

    faces, edges = shape.faces(), shape.edges()
    return _make_scene(
        shape,
        faces,
        edges,
        list(range(len(faces))),
        list(range(len(edges))),
        {},
        {},
        set(),
        set(),
        set(),
        bounds=_shape_bounds(shape),
        units="mm",
        section=None,
    )


def test_thickness_reads_real_wall_sizes():
    from aad_render.thickness import face_thickness

    scene = _scene_for(Box(60, 40, 6))
    values = face_thickness(scene)
    by_axis = {}
    for face in scene.faces:
        axis = max(range(3), key=lambda i: abs(face.normal[i]))
        by_axis.setdefault(axis, set()).add(round(values[face.index], 3))
    assert by_axis == {0: {60.0}, 1: {40.0}, 2: {6.0}}


def test_thickness_ignores_corners_and_finds_the_thin_wall():
    from aad_render.thickness import face_thickness

    # A 2 mm web between two 20 mm blocks: rays at the inside corners must not read near zero.
    part = Box(40, 40, 2) + Pos(-30, 0, 9) * Box(20, 40, 20) + Pos(30, 0, 9) * Box(20, 40, 20)
    values = face_thickness(_scene_for(part))
    assert values
    assert min(values.values()) == pytest.approx(2.0, abs=1.0e-6)


def test_labels_skip_faces_the_camera_cannot_see():
    from aad_render.agent_render import _Label, _camera, _visible_labels

    box = Box(10, 10, 10)
    scene = _scene_for(box)
    labels = [
        _Label(number, f"face:{face.index}", None, "face", face.index, face.anchor)
        for number, face in enumerate(scene.faces, start=1)
    ]
    camera = _camera("iso", scene.bounds, 512, 512)
    assert len(_visible_labels(labels, camera, scene)) == 3


def test_section_cap_is_hatched_and_colored(tmp_path):
    from aad_render.agent_render import _SECTION_CAP

    paths = _render_or_skip(
        _context(_bracket()),
        {"views": ["right"], "section": {"plane": "YZ", "offset": 0}, "size": 256},
        tmp_path,
    )
    data = Image.open(paths[0]).convert("RGB").tobytes()
    warm = sum(
        1
        for r, g, b in zip(data[0::3], data[1::3], data[2::3], strict=True)
        if r > g > b and r - b > 30
    )
    assert warm > 500, f"expected a tinted cap like {_SECTION_CAP}, got {warm} warm pixels"


def test_linux_without_display_or_osmesa_raises_instead_of_crashing(monkeypatch):
    from aad_render.backends import vtk_backend

    monkeypatch.setattr(vtk_backend.sys, "platform", "linux")
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "VTK_DEFAULT_OPENGL_WINDOW"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(vtk_backend.ctypes.util, "find_library", lambda name: None)
    with pytest.raises(RuntimeError, match="No usable headless GL"):
        vtk_backend.prepare_headless_gl()


def test_linux_without_display_uses_osmesa_when_installed(monkeypatch):
    from aad_render.backends import vtk_backend

    monkeypatch.setattr(vtk_backend.sys, "platform", "linux")
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "VTK_DEFAULT_OPENGL_WINDOW"):
        monkeypatch.delenv(name, raising=False)
    # Track this key before the production code writes it, so the fake library
    # selection cannot leak into later real renders on runners without OSMesa.
    monkeypatch.setenv("VTK_DEFAULT_OPENGL_WINDOW", "")
    monkeypatch.setattr(vtk_backend.ctypes.util, "find_library", lambda name: "libOSMesa.so.8")
    vtk_backend.prepare_headless_gl()
    assert os.environ["VTK_DEFAULT_OPENGL_WINDOW"] == "vtkOSOpenGLRenderWindow"


def _capture_render(monkeypatch, ctx, req, out):
    from aad_render import agent_render

    captured = []

    class CaptureBackend:
        def render(self, scene, camera, *, width, height, style):
            captured.append(scene)
            return Image.new("RGB", (width, height))

    monkeypatch.setattr(agent_render, "get_backend", lambda name: CaptureBackend())
    paths = render(ctx, {"views": ["iso"], "size": 256, **req}, out)
    return captured[0], paths


def test_semantic_face_highlight_does_not_expand_to_the_whole_feature(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from aad_render import agent_render

    monkeypatch.setattr(agent_render, "_engine_resolver", lambda: (
        lambda ctx, selector: [SimpleNamespace(kind="face", index=1, feature="body")]
    ))
    ctx = _context(Box(10, 10, 10), "body")
    scene, _ = _capture_render(monkeypatch, ctx, {"highlight": ["face(where='max_x')"]}, tmp_path)
    assert [face.source_index for face in scene.faces if face.highlighted] == [1]
    assert not any(line.highlighted for line in scene.lines)
    with pytest.raises(ValueError, match="no feature named"):
        render(ctx, {"highlight": ["feature:missing"]}, tmp_path)


@pytest.mark.parametrize("mode", ["faces", "edges"])
def test_section_labels_and_highlights_keep_original_revision_entities(monkeypatch, tmp_path, mode):
    shape = Box(10, 8, 4)
    scene, paths = _capture_render(
        monkeypatch,
        _context(shape, "plate"),
        {"section": {"plane": "XY", "offset": 0}, "highlight": ["face:0", "edge:0"], "labels": mode},
        tmp_path,
    )
    trimmed_face = next(face for face in scene.faces if face.source_index == 0)
    assert trimmed_face.highlighted and trimmed_face.feature == "plate"
    assert max(point[2] for point in trimmed_face.points) == pytest.approx(0.0)
    assert next(line for line in scene.lines if line.source_index == 0).highlighted
    assert any(face.section_cap and face.source_index is None for face in scene.faces)
    legend = json.loads(paths[-1].read_text())
    entities = scene.faces if mode == "faces" else [line for line in scene.lines if line.role == "edge"]
    known_sources = {entity.source_index for entity in entities if entity.source_index is not None}
    assert {item["index"] for item in legend.values()} == known_sources
    assert all(item["feature"] == "plate" for item in legend.values())


def test_overhang_uses_local_normals_on_a_curved_face(monkeypatch, tmp_path):
    from build123d import Sphere

    scene, _ = _capture_render(monkeypatch, _context(Sphere(5)), {"heatmap": "overhang"}, tmp_path)
    face = scene.faces[0]
    assert len(scene.faces) == 1
    assert len(face.triangle_colors) == len(face.triangles)
    assert len(set(face.triangle_colors)) > 20
    for triangle, color in zip(face.triangles, face.triangle_colors, strict=True):
        if color == (154, 160, 166):
            import numpy as np
            a, b, c = (np.asarray(face.points[index]) for index in triangle)
            assert np.linalg.norm(np.cross(b - a, c - a)) < 1.0e-14
            continue  # OCCT includes zero-area triangles at the sphere poles.
        z = sum(face.points[index][2] for index in triangle) / 3
        if z > 4.5:
            assert color[2] > color[0]  # upward normals are blue
        elif z < -4.5:
            assert color[0] > color[2]  # downward normals are red


def test_empty_compound_has_a_clear_input_error(tmp_path):
    from build123d import Compound

    with pytest.raises(ValueError, match="non-empty 3D shape"):
        render(_context(Compound(children=[])), {}, tmp_path)


def test_section_that_removes_the_whole_part_is_annotated(monkeypatch, tmp_path):
    scene, paths = _capture_render(
        monkeypatch,
        _context(Box(10, 8, 4), "plate"),
        {"section": {"plane": "XY", "offset": -100}, "labels": "faces"},
        tmp_path,
    )
    assert scene.faces == [] and scene.lines == []
    assert scene.bounds == ((-5, -4, -2), (5, 4, 2))
    assert "EMPTY: no geometry on retained side" in scene.section_label
    assert json.loads(paths[-1].read_text()) == {}


@pytest.mark.parametrize("size", [1, 16, 63])
def test_tiny_sheet_sizes_have_valid_viewports(tmp_path, size):
    paths = _render_or_skip(_context(Box(1, 1, 1)), {"size": size}, tmp_path)
    with Image.open(paths[0]) as image:
        assert image.size == (size, size)
