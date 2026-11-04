from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from build123d import Box, Compound, Cylinder, Face, Shell, Solid, Sphere

from aiad.engine.gate import GateTolerances, _check_mesh_arrays, _fresh_mesh_arrays, hard_checks
from aiad.engine.jobs import run_build
from aiad.engine.types import BuildRequest, ModelContext


ROOT = Path(__file__).resolve().parents[3]


def context(shape, job_dir: Path, *, units: str = "mm", kind: str = "part") -> ModelContext:
    return ModelContext(shape=shape, units=units, kind=kind, job_dir=job_dir)


def check_map(checks):
    return {check.id: check for check in checks}


def actual_number(check, name: str) -> float:
    values = dict(field.strip().split("=", 1) for field in check.actual.split(";"))
    return float(values[name].split()[0])


class GeometricGateTests(unittest.TestCase):
    def test_box_cylinder_and_holed_part_pass_every_error_check(self) -> None:
        shapes = {
            "box": Box(20, 10, 6),
            "cylinder": Cylinder(5, 12),
            "holed_part": Box(20, 20, 6) - Cylinder(3, 10),
        }
        for name, shape in shapes.items():
            with self.subTest(shape=name), tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
                job_dir = Path(tmp) / "job"
                checks = hard_checks(context(shape, job_dir))
                errors = [check for check in checks if check.level == "error"]
                self.assertTrue(errors)
                self.assertTrue(
                    all(check.status == "pass" for check in errors),
                    {check.id: (check.status, check.actual) for check in errors},
                )
                self.assertEqual(
                    {check.id for check in errors},
                    {"shape_present", "brep_valid", "solid_count", "closed_shells", "edge_quality",
                     "face_quality", "step_volume_drift", "mesh_watertight"},
                )
                self.assertEqual(check_map(checks)["solid_count"].actual, "1 solid(s)")
                self.assertIn("source_volume=", check_map(checks)["step_volume_drift"].actual)
                mesh = check_map(checks)["mesh_watertight"]
                self.assertGreater(actual_number(mesh, "minimum_triangle_area"), 1e-12)
                self.assertIn("configured_deflection=0.1 mm", mesh.actual)
                self.assertIn("configured_angular_deflection=0.35 rad", mesh.actual)
                self.assertFalse(list(job_dir.glob("aiad-gate-*")))

    def test_open_shell_without_a_solid_is_reported_and_mesh_is_open(self) -> None:
        shape = Shell([Box(4, 4, 4).faces()[0]])
        self.assertEqual(len(shape.solids()), 0)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(shape, Path(tmp))))
        self.assertEqual(checks["solid_count"].status, "fail")
        self.assertEqual(checks["closed_shells"].status, "fail")
        self.assertIn("open=1", checks["closed_shells"].actual)
        self.assertEqual(checks["mesh_watertight"].status, "fail")
        self.assertIn("boundary_edges=", checks["mesh_watertight"].actual)

    def test_empty_and_missing_shapes_return_checks_without_raising(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            empty_checks = check_map(hard_checks(context(Compound(children=[]), Path(tmp) / "empty")))
            missing_checks = check_map(hard_checks(context(None, Path(tmp) / "missing")))

        self.assertEqual(empty_checks["solid_count"].status, "fail")
        self.assertEqual(empty_checks["mesh_watertight"].status, "skip")
        self.assertEqual(missing_checks["shape_present"].status, "fail")
        self.assertTrue(
            all(missing_checks[check_id].status == "skip" for check_id in (
                "brep_valid",
                "closed_shells",
                "solid_count",
                "edge_quality",
                "face_quality",
                "step_volume_drift",
                "mesh_watertight",
            ))
        )
        self.assertTrue(all(check.level == "error" for check in missing_checks.values()))

    def test_invalid_open_solid_is_rejected_by_brepcheck(self) -> None:
        open_shell = Shell([Box(4, 4, 4).faces()[0]])
        invalid_solid = Solid(open_shell)
        self.assertFalse(invalid_solid.is_valid)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(invalid_solid, Path(tmp))))
        self.assertEqual(checks["brep_valid"].status, "fail")
        self.assertIn("BRepCheck_Analyzer.IsValid()=false", checks["brep_valid"].actual)

    def test_degenerate_kernel_edges_are_reported_even_for_a_valid_sphere(self) -> None:
        shape = Sphere(5)
        self.assertTrue(shape.is_valid)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            edge_check = check_map(hard_checks(context(shape, Path(tmp))))["edge_quality"]
        self.assertEqual(edge_check.status, "fail")
        self.assertNotIn("degenerate_edges=0", edge_check.actual)
        self.assertTrue(edge_check.heuristic)

    def test_undefined_zero_area_kernel_face_is_rejected(self) -> None:
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Face

        raw_face = TopoDS_Face()
        BRep_Builder().MakeFace(raw_face)
        shape = Face(raw_face)
        self.assertEqual(shape.area, 0)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(shape, Path(tmp))))
        self.assertEqual(checks["brep_valid"].status, "fail")
        self.assertEqual(checks["face_quality"].status, "fail")
        self.assertIn("zero_area_faces=1", checks["face_quality"].actual)
        self.assertNotEqual(checks["mesh_watertight"].status, "pass")

    def test_tiny_edges_and_sliver_faces_use_configured_thresholds(self) -> None:
        thin_box = Box(1, 1, 0.0001)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            defaults = check_map(hard_checks(context(thin_box, Path(tmp) / "default")))
            configured = check_map(
                hard_checks(
                    context(thin_box, Path(tmp) / "configured"),
                    {"min_edge_length_mm": 0.00001, "min_face_area_mm2": 0.00001,
                     "min_face_width_mm": 0.00001},
                )
            )

        self.assertEqual(defaults["edge_quality"].status, "fail")
        self.assertIn("configured_threshold=0.01 mm", defaults["edge_quality"].actual)
        self.assertEqual(defaults["face_quality"].status, "fail")
        self.assertEqual(configured["edge_quality"].status, "pass")
        self.assertEqual(configured["face_quality"].status, "pass")

    def test_reported_values_preserve_a_close_threshold_comparison(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            edge = check_map(hard_checks(
                context(Box(1, 1, 1), Path(tmp)), {"min_edge_length_mm": 1.000000000001}
            ))["edge_quality"]
        minimum = actual_number(edge, "minimum_edge_length")
        threshold = actual_number(edge, "configured_threshold")
        self.assertEqual(edge.status, "fail")
        self.assertLess(minimum, threshold)

    def test_large_area_narrow_faces_are_detected_as_sliver_proxies(self) -> None:
        shape = Box(100, 1, 0.0005)
        common = {"min_edge_length_mm": 0, "min_face_area_mm2": 0.00001}
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(shape, Path(tmp)), common))
            relaxed = check_map(hard_checks(
                context(shape, Path(tmp)), {**common, "min_face_width_mm": 0.0001}
            ))
        face = checks["face_quality"]
        self.assertEqual(face.status, "fail")
        self.assertIn("small_area_faces=0", face.actual)
        self.assertNotIn("sliver_faces=0", face.actual)
        self.assertIn("configured_width_threshold=0.001 mm", face.actual)
        self.assertTrue(face.heuristic)
        self.assertIn("not a manufacturing limit", face.assumptions)
        self.assertEqual(relaxed["face_quality"].status, "pass")

    def test_inch_step_roundtrip_and_thresholds_use_millimetres(self) -> None:
        shape = Box(1, 2, 3)
        small = Box(0.0005, 1, 1)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            mm = check_map(hard_checks(context(small, Path(tmp), units="mm")))
            inches = check_map(hard_checks(context(small, Path(tmp), units="in")))
            step = check_map(hard_checks(context(shape, Path(tmp), units="in")))["step_volume_drift"]
        self.assertEqual(mm["edge_quality"].status, "fail")
        self.assertEqual(inches["edge_quality"].status, "pass")
        self.assertIn("minimum_edge_length=0.0127 mm", inches["edge_quality"].actual)
        self.assertEqual(step.status, "pass")
        self.assertAlmostEqual(actual_number(step, "source_volume"), 98322.384, places=6)
        self.assertAlmostEqual(actual_number(step, "step_volume"), 98322.384, places=6)
        self.assertIn("mm³", step.actual)

    def test_multiple_solids_follow_part_and_assembly_policies(self) -> None:
        shape = Compound(children=[Box(2, 2, 2), Box(2, 2, 2).translate((5, 0, 0))])
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            part = check_map(hard_checks(context(shape, Path(tmp), kind="part")))
            assembly = check_map(hard_checks(context(shape, Path(tmp), kind="assembly")))
            unknown = check_map(hard_checks(context(shape, Path(tmp), kind="2d")))

        self.assertEqual(part["solid_count"].status, "fail")
        self.assertEqual(part["solid_count"].expected, "exactly 1 solid for a part")
        self.assertEqual(assembly["solid_count"].status, "pass")
        self.assertEqual(assembly["solid_count"].actual, "2 solid(s)")
        self.assertEqual(assembly["solid_count"].expected, "at least 1 solid for an assembly")
        self.assertTrue(all(c.status == "pass" for c in assembly.values() if c.level == "error"))
        self.assertEqual(assembly["multiple_solids"].status, "warn")
        self.assertEqual(assembly["multiple_solids"].level, "warn")
        self.assertTrue(assembly["multiple_solids"].heuristic)
        self.assertEqual(unknown["solid_count"].status, "skip")
        self.assertEqual(unknown["solid_count"].level, "error")

    def test_step_roundtrip_drift_fault_injection_fails_with_measured_volumes(self) -> None:
        source = Box(10, 10, 10)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            with patch("aiad.engine.gate._import_step_shape", return_value=Box(10.1, 10, 10)):
                checks = check_map(hard_checks(context(source, Path(tmp))))

        step = checks["step_volume_drift"]
        self.assertEqual(step.status, "fail")
        self.assertAlmostEqual(actual_number(step, "source_volume"), 1000, places=8)
        self.assertAlmostEqual(actual_number(step, "step_volume"), 1010, places=8)
        self.assertIn("configured_absolute_threshold=1e-06 mm³", step.actual)

    def test_step_failures_skip_and_clean_unique_job_local_files_on_repeat(self) -> None:
        shape = Box(2, 3, 4)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            sentinel = job / "roundtrip.step"
            sentinel.write_text("keep", encoding="utf-8")
            for _ in range(2):
                with patch("aiad.engine.gate._import_step_shape", side_effect=RuntimeError("import fault")):
                    step = check_map(hard_checks(context(shape, job)))["step_volume_drift"]
                self.assertEqual(step.status, "skip")
                self.assertEqual(step.level, "error")
                self.assertIn("import fault", step.actual)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
                self.assertEqual(sorted(p.name for p in job.iterdir()), ["roundtrip.step"])
            self.assertEqual(check_map(hard_checks(context(shape, job)))["step_volume_drift"].status, "pass")
            self.assertEqual(sorted(p.name for p in job.iterdir()), ["roundtrip.step"])

    def test_unavailable_kernel_and_mesh_checks_are_error_skips(self) -> None:
        shape = Box(2, 3, 4)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            ctx = context(shape, Path(tmp))
            with patch("OCP.BRepCheck.BRepCheck_Analyzer", side_effect=RuntimeError("analyzer fault")):
                validity = check_map(hard_checks(ctx))["brep_valid"]
            with patch("aiad.engine.gate._fresh_mesh_arrays", side_effect=RuntimeError("mesh fault")):
                mesh = check_map(hard_checks(ctx))["mesh_watertight"]
            with patch("build123d.export_step", return_value=True):
                step = check_map(hard_checks(ctx))["step_volume_drift"]
            self.assertFalse(list(Path(tmp).glob("aiad-gate-*")))
        for check, detail in ((validity, "analyzer fault"), (mesh, "mesh fault"),
                              (step, "non-empty STEP file")):
            self.assertEqual(check.status, "skip")
            self.assertEqual(check.level, "error")
            self.assertIn(detail, check.actual)

    def test_invalid_roundtrip_volume_and_overflow_never_pass(self) -> None:
        shape = Box(2, 3, 4)
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            ctx = context(shape, Path(tmp))
            with patch("aiad.engine.gate._import_step_shape", return_value=SimpleNamespace(volume=0)):
                zero = check_map(hard_checks(ctx, {"step_volume_abs_mm3": 1e10}))["step_volume_drift"]
            overflow = check_map(hard_checks(ctx, {"step_volume_rel": 1e308}))["step_volume_drift"]
        self.assertEqual(zero.status, "fail")
        self.assertEqual(overflow.status, "skip")
        self.assertEqual(overflow.level, "error")
        self.assertIn("not finite", overflow.actual)

    def test_worker_failure_names_the_actual_hard_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            result = run_build(BuildRequest(
                source="result = Box(0.001, 1, 1)", project_dir=Path(tmp), job_dir=Path(tmp) / "job"
            ))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure.code, "INVALID_SOLID")
        self.assertIn("edge_quality", result.failure.message)
        self.assertNotIn("B-rep validity or solid-count", result.failure.message)

    def test_zero_volume_step_comparison_is_not_a_pass(self) -> None:
        shape = Shell([Box(4, 4, 4).faces()[0]])
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(shape, Path(tmp))))

        step = checks["step_volume_drift"]
        self.assertEqual(step.status, "skip")
        self.assertEqual(step.level, "error")
        self.assertIn("source volume is 0 mm³", step.actual)

    def test_mesh_fixtures_expose_open_and_nonmanifold_edge_degrees(self) -> None:
        fixtures = ROOT / "fixtures" / "gate"
        open_mesh = json.loads((fixtures / "mesh_open.json").read_text(encoding="utf-8"))
        nonmanifold_mesh = json.loads((fixtures / "mesh_nonmanifold.json").read_text(encoding="utf-8"))
        degenerate_mesh = json.loads((fixtures / "mesh_degenerate.json").read_text(encoding="utf-8"))
        pinched_mesh = json.loads((fixtures / "mesh_pinched_vertex.json").read_text(encoding="utf-8"))
        duplicate_mesh = json.loads((fixtures / "mesh_duplicate.json").read_text(encoding="utf-8"))

        with self.subTest(mesh="open"):
            check = _check_mesh_arrays(open_mesh["vertices"], open_mesh["triangles"], GateTolerances())
            self.assertEqual(check.status, "fail")
            self.assertIn("boundary_edges=3", check.actual)
        with self.subTest(mesh="nonmanifold"):
            check = _check_mesh_arrays(
                nonmanifold_mesh["vertices"], nonmanifold_mesh["triangles"], GateTolerances()
            )
            self.assertEqual(check.status, "fail")
            self.assertIn("nonmanifold_edges=1", check.actual)
        with self.subTest(mesh="degenerate"):
            check = _check_mesh_arrays(
                degenerate_mesh["vertices"], degenerate_mesh["triangles"], GateTolerances()
            )
            self.assertEqual(check.status, "fail")
            self.assertIn("degenerate_triangles=1", check.actual)
        with self.subTest(mesh="pinched vertex"):
            check = _check_mesh_arrays(pinched_mesh["vertices"], pinched_mesh["triangles"], GateTolerances())
            self.assertEqual(check.status, "fail")
            self.assertIn("edge_degree_range=2..2", check.actual)
            self.assertIn("nonmanifold_vertices=1", check.actual)
        with self.subTest(mesh="duplicate face"):
            check = _check_mesh_arrays(duplicate_mesh["vertices"], duplicate_mesh["triangles"], GateTolerances())
            self.assertEqual(check.status, "fail")
            self.assertIn("edge_degree_range=2..2", check.actual)
            self.assertIn("duplicate_triangles=1", check.actual)

    def test_welded_coincident_tetrahedron_vertices_form_a_closed_mesh(self) -> None:
        points = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
        faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
        vertices = [
            (points[index][0] + face_number * 1e-8, points[index][1], points[index][2])
            for face_number, face in enumerate(faces) for index in face
        ]
        triangles = [[start, start + 1, start + 2] for start in range(0, 12, 3)]
        welded = _check_mesh_arrays(vertices, triangles, GateTolerances())
        exact = _check_mesh_arrays(vertices, triangles, GateTolerances(mesh_vertex_weld_mm=0))
        self.assertEqual(welded.status, "pass")
        self.assertIn("input_vertices=12; welded_vertices=4", welded.actual)
        self.assertIn("edge_degree_range=2..2", welded.actual)
        self.assertEqual(exact.status, "fail")

    def test_mesh_bad_winding_and_malformed_indices_do_not_pass(self) -> None:
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
        inconsistent = [[0, 1, 2], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
        check = _check_mesh_arrays(vertices, inconsistent, GateTolerances())
        self.assertEqual(check.status, "fail")
        self.assertIn("winding_conflicts=3", check.actual)
        for triangle in ([0, 1, 10], [0, 1, 1.9], [False, 1, 2]):
            with self.subTest(triangle=triangle):
                invalid = _check_mesh_arrays(vertices, [triangle], GateTolerances())
                self.assertEqual(invalid.status, "skip")
                self.assertEqual(invalid.level, "error")

    def test_absolute_mesh_resolution_is_physically_consistent_in_mm_and_inches(self) -> None:
        radius = 25.4
        vertices, triangles = _fresh_mesh_arrays(Cylinder(radius, 50.8), 0.1, 2.0)
        inch_vertices, inch_triangles = _fresh_mesh_arrays(Cylinder(1, 2), 0.1 / 25.4, 2.0)
        self.assertEqual(len(triangles), len(inch_triangles))
        self.assertEqual(len(vertices), len(inch_vertices))
        sagittas = []
        for triangle in triangles:
            points = [vertices[index] for index in triangle]
            # Lateral triangles span height; their horizontal edges are
            # circle-boundary chords, unlike diagonals within the planar caps.
            if max(p[2] for p in points) - min(p[2] for p in points) < 1e-6:
                continue
            for a, b in zip(points, points[1:] + points[:1]):
                if abs(a[2] - b[2]) < 1e-6:
                    chord = math.dist(a, b)
                    sagittas.append(radius - math.sqrt(max(0, radius**2 - chord**2 / 4)))
        self.assertTrue(sagittas)
        self.assertLessEqual(max(sagittas), 0.1)

    def test_fresh_meshing_honors_tighter_angle_without_changing_source_cache(self) -> None:
        from OCP.BRep import BRep_Tool
        from OCP.TopLoc import TopLoc_Location

        shape = Cylinder(10, 20)
        _, cached = shape.tessellate(10, 2.0)
        def counts():
            return [BRep_Tool.Triangulation_s(face.wrapped, TopLoc_Location()).NbTriangles()
                    for face in shape.faces()]
        original_counts = counts()
        _, refined = _fresh_mesh_arrays(shape, 10, 0.05)
        self.assertGreater(len(refined), len(cached))
        self.assertEqual(counts(), original_counts)
        self.assertEqual(len(shape.tessellate(10, 2.0)[1]), len(cached))

    def test_mesh_vertices_farther_than_weld_tolerance_stay_distinct(self) -> None:
        tolerance = GateTolerances(mesh_vertex_weld_mm=0.001)
        vertices = [
            [0, 0, 0], [1, 0, 0], [0, 1, 0],
            [0.01, 0, 0], [1.01, 0, 0], [0.01, 1, 0],
        ]
        check = _check_mesh_arrays(vertices, [[0, 1, 2], [3, 4, 5]], tolerance)
        self.assertIn("welded_vertices=6", check.actual)
        self.assertIn("boundary_edges=6", check.actual)

    def test_invalid_tolerances_skip_threshold_checks_as_errors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            for invalid in (
                {"min_edge_length_mm": -1}, {"min_face_width_mm": float("nan")},
                {"mesh_deflection_mm": 0}, {"mesh_vertex_weld_mm": True}, {1: 1, "typo": 1},
                {"min_edge_length_mm": 10 ** 400},
            ):
                with self.subTest(tolerances=invalid):
                    checks = check_map(hard_checks(context(Box(2, 2, 2), Path(tmp)), invalid))
                    self.assertEqual(checks["gate_tolerances"].status, "fail")
                    for check_id in ("edge_quality", "face_quality", "step_volume_drift", "mesh_watertight"):
                        self.assertEqual(checks[check_id].status, "skip")
                        self.assertEqual(checks[check_id].level, "error")

    def test_unknown_units_withhold_every_threshold_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(Box(2, 2, 2), Path(tmp), units="cm")))
        self.assertEqual(checks["gate_units"].status, "fail")
        for check_id in ("edge_quality", "face_quality", "step_volume_drift", "mesh_watertight"):
            self.assertEqual(checks[check_id].status, "skip")
            self.assertEqual(checks[check_id].level, "error")


if __name__ == "__main__":
    unittest.main()
