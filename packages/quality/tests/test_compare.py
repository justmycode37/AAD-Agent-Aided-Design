from __future__ import annotations

from math import acos, cos, pi, radians, sin, tan
import struct
import numpy as np
import pytest
from build123d import Align, Box, Compound, Cylinder, Edge, Face, Pos, Sphere, Vector, Wire, export_step, extrude, import_step

import aad_quality.compare as comparison
from aad_quality._checks import record
from aad_quality.spec import spec_checks


def _polar(radius: float, angle: float) -> Vector:
    return Vector(radius * cos(angle), radius * sin(angle), 0)


def _involute(angle: float) -> float:
    return tan(angle) - angle


def _gear_wire(teeth: int, module: float = 2.5, pressure_angle: float = radians(20), facets: int = 7) -> Wire:
    pitch_radius = teeth * module / 2
    base_radius = pitch_radius * cos(pressure_angle)
    addendum_radius = pitch_radius + module
    root_radius = pitch_radius - 1.25 * module
    half_tooth = pi * module / 2 / (2 * pitch_radius)

    def involute_angle(radius: float) -> float:
        return half_tooth + _involute(pressure_angle) - _involute(acos(min(1.0, base_radius / radius)))

    first_radius = max(base_radius, root_radius)
    radii = [first_radius + (addendum_radius - first_radius) * (i / facets) ** 1.3 for i in range(facets + 1)]
    tooth_pitch = 2 * pi / teeth
    edges = []
    for tooth in range(teeth):
        center = tooth * tooth_pitch
        right = [_polar(radius, center - involute_angle(radius)) for radius in radii]
        left = [_polar(radius, center + involute_angle(radius)) for radius in reversed(radii)]
        if root_radius < base_radius:
            edges.append(Edge.make_line(_polar(root_radius, center - involute_angle(base_radius)), right[0]))
        edges.extend((Edge.make_spline(right), Edge.make_three_point_arc(right[-1], _polar(addendum_radius, center), left[0]), Edge.make_spline(left)))
        if root_radius < base_radius:
            edges.append(Edge.make_line(left[-1], _polar(root_radius, center + involute_angle(base_radius))))
        start = center + involute_angle(first_radius)
        end = center + tooth_pitch - involute_angle(first_radius)
        edges.append(Edge.make_three_point_arc(_polar(root_radius, start), _polar(root_radius, (start + end) / 2), _polar(root_radius, end)))
    return Wire(edges)


@pytest.fixture(scope="module")
def prototype_meshes(tmp_path_factory):
    output = tmp_path_factory.mktemp("reference-fixtures")
    gear = extrude(Face(_gear_wire(40)), 30, dir=(0, 0, 1))
    offset_bar = Pos(70, 0, 0) * Box(40, 12, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    asym = Compound(children=[gear, offset_bar, Pos(0, 60, 15) * Sphere(9)])
    asym_path = output / "asym.step"
    gear_path = output / "gear.step"
    cylinder_path = output / "cylinder.step"
    assert export_step(asym, str(asym_path))
    assert export_step(Compound(children=[gear]), str(gear_path))
    cylinder_shape = Cylinder(50, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
    assert export_step(Compound(children=[cylinder_shape]), str(cylinder_path))

    base = comparison.mesh_from_shape(import_step(str(asym_path)))
    gear_mesh = comparison.mesh_from_shape(import_step(str(gear_path)))
    cylinder_mesh = comparison.mesh_from_shape(import_step(str(cylinder_path)))
    a, b, c = np.radians([21, 34, -12])
    rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    rz = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    moved = comparison.TriangleMesh(base.vertices @ (rz @ ry @ rx).T + [13, -7, 22], base.faces, base.solid_ids, base.merge_for_closed)
    scaled = comparison.TriangleMesh(base.vertices * 1.02, base.faces, base.solid_ids, base.merge_for_closed)
    mirrored = comparison.TriangleMesh(base.vertices * [-1, 1, 1], base.faces[:, ::-1].copy(), base.solid_ids, base.merge_for_closed)
    return {"base": base, "moved": moved, "scaled": scaled, "mirrored": mirrored, "gear": gear_mesh, "cylinder": cylinder_mesh}


def test_prototype_fixture_scores_and_proper_registration(prototype_meshes):
    pairs = [
        ("moved", prototype_meshes["base"], prototype_meshes["moved"], 1.000),
        ("scaled", prototype_meshes["scaled"], prototype_meshes["base"], 0.088),
        ("mirrored", prototype_meshes["mirrored"], prototype_meshes["base"], 0.486),
        ("cylinder_gear", prototype_meshes["cylinder"], prototype_meshes["gear"], 0.532),
    ]
    scores = {}
    for name, model, reference, expected in pairs:
        result = comparison.compare_meshes(model, reference, compute_iou=False)
        scores[name] = result["F@0.1%D"]["F"]
        assert scores[name] == pytest.approx(expected, abs=0.01), name
        assert result["det_R"] == pytest.approx(1.0, abs=1e-5), name
        rotation = np.asarray(result["rotation"])
        assert np.linalg.det(rotation) == pytest.approx(1.0, abs=1e-5), name
        assert np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5), name
    assert scores["scaled"] < 0.99
    assert scores["mirrored"] < 0.99


def test_fixed_seed_sampling_is_deterministic(prototype_meshes):
    mesh = prototype_meshes["base"]
    first_areas = comparison._triangle_areas(mesh, comparison._Deadline(10))
    second_areas = comparison._triangle_areas(mesh, comparison._Deadline(10))
    first = comparison._sample(mesh, first_areas, 10_000, np.random.default_rng(7), comparison._Deadline(10))
    second = comparison._sample(mesh, second_areas, 10_000, np.random.default_rng(7), comparison._Deadline(10))
    assert np.array_equal(first, second)


def test_project_reference_rejects_traversal_and_symlinks(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    valid = assets / "valid.obj"
    valid.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    outside = tmp_path.parent / "outside.obj"
    outside.write_text("not a mesh")
    (assets / "link.obj").symlink_to(outside)
    with pytest.raises(comparison.ComparisonError, match="parent traversal"):
        comparison.resolve_project_reference(tmp_path, "../outside.obj")
    with pytest.raises(comparison.ComparisonError, match="symbolic link"):
        comparison.resolve_project_reference(tmp_path, "assets/link.obj")
    assert comparison.resolve_project_reference(tmp_path, "assets/valid.obj") == valid.resolve()


def test_corrupted_mesh_is_not_accepted_as_reference(tmp_path):
    corrupt = tmp_path / "corrupt.ply"
    corrupt.write_bytes(b"ply\nformat binary_little_endian 1.0\nend_header\ntruncated")
    with pytest.raises(comparison.ComparisonError):
        comparison.compare_shape_to_reference(Box(1, 1, 1), corrupt, compute_iou=False)


def test_binary_ply_triangle_records_are_parsed_with_declared_offsets(tmp_path):
    reference = tmp_path / "triangle.ply"
    header = (
        b"ply\nformat binary_little_endian 1.0\n"
        b"element vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
        b"element face 1\nproperty list uchar int vertex_indices\nend_header\n"
    )
    body = struct.pack("<9fB3i", 0, 0, 0, 1, 0, 0, 0, 1, 0, 3, 0, 1, 2)
    reference.write_bytes(header + body)

    mesh = comparison._read_reference(reference, units="mm", deadline=comparison._Deadline(5))

    assert np.array_equal(mesh.faces, np.array([[0, 1, 2]], dtype=np.int32))
    assert np.allclose(mesh.vertices, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])


def test_missing_optional_locator_is_a_spec_skip_not_a_pass(monkeypatch, tmp_path):
    reference = tmp_path / "ref.step"
    reference.write_text("invalid step")
    spec = {
        "requirements": [
            {"id": "same_surface", "matches_reference": {"path": "ref.step", "f_score": {"min": 0.99}}}
        ]
    }
    context = type("Context", (), {"shape": Box(1, 1, 1), "spec": spec, "units": "mm", "project_dir": tmp_path})()

    def missing_vtk():
        raise comparison.ComparisonUnavailable("VTK is missing in this worker")

    monkeypatch.setattr(comparison, "_vtk_modules", missing_vtk)
    check = record(spec_checks(context)[0])
    assert check["status"] == "skip"
    assert "VTK is missing" in check["actual"]


def test_iou_is_explicitly_unavailable_for_open_mesh():
    mesh = comparison.TriangleMesh(
        np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        np.array([[0, 1, 2]], dtype=np.int32),
    )
    result = comparison.compare_meshes(mesh, mesh, compute_iou=True, iou_resolution=16, time_budget_s=45)
    assert result["IoU"] == "not available"
    assert "requires closed solids" in result["iou_reason"]


def test_iou_for_closed_shape_uses_all_surface_crossings():
    mesh = comparison.mesh_from_shape(Box(2, 3, 4))
    result = comparison._volume_iou(mesh, mesh, comparison._Deadline(10), resolution=16)

    assert result["status"] == "available"
    assert result["value"] == 1.0
    assert result["closed_solids"] == {"model": "1/1", "reference": "1/1"}
