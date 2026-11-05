from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build123d import Box, Compound, Cone, Cylinder, Face, Pos, Shell, Solid, Sphere

from aad.engine.gate import (
    GateTolerances,
    _check_mesh_arrays,
    _fresh_mesh_arrays,
    hard_checks,
    interference_checks,
)
from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest, ModelContext


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
            with self.subTest(shape=name), tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
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
                self.assertFalse(list(job_dir.glob("aad-gate-*")))

    def test_touching_assembly_bodies_are_checked_one_at_a_time(self) -> None:
        touching = Compound(children=[Box(10, 10, 10), Pos(10, 0, 0) * Box(10, 10, 10)])
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            assembly = check_map(hard_checks(context(touching, Path(tmp), kind="assembly")))["mesh_watertight"]
            part = check_map(hard_checks(context(touching, Path(tmp), kind="part")))["mesh_watertight"]
        self.assertEqual(assembly.status, "pass", assembly.actual)
        self.assertIn("bodies_checked=2", assembly.actual)
        self.assertEqual(part.status, "fail", part.actual)

    def test_assembly_with_loose_faces_keeps_the_whole_shape_mesh_check(self) -> None:
        loose = Shell([Pos(40, 0, 0) * Box(4, 4, 4).faces()[0]])
        shape = Compound(children=[Box(10, 10, 10), Pos(20, 0, 0) * Box(10, 10, 10), loose])
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            mesh = check_map(hard_checks(context(shape, Path(tmp), kind="assembly")))["mesh_watertight"]
        self.assertEqual(mesh.status, "fail", mesh.actual)
        self.assertIn("boundary_edges=", mesh.actual)
        self.assertNotIn("bodies_checked", mesh.actual)

    def test_open_shell_without_a_solid_is_reported_and_mesh_is_open(self) -> None:
        shape = Shell([Box(4, 4, 4).faces()[0]])
        self.assertEqual(len(shape.solids()), 0)
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(shape, Path(tmp))))
        self.assertEqual(checks["solid_count"].status, "fail")
        self.assertEqual(checks["closed_shells"].status, "fail")
        self.assertIn("open=1", checks["closed_shells"].actual)
        self.assertEqual(checks["mesh_watertight"].status, "fail")
        self.assertIn("boundary_edges=", checks["mesh_watertight"].actual)

    def test_empty_and_missing_shapes_return_checks_without_raising(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
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
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(invalid_solid, Path(tmp))))
        self.assertEqual(checks["brep_valid"].status, "fail")
        self.assertIn("BRepCheck_Analyzer.IsValid()=false", checks["brep_valid"].actual)

    def test_surface_poles_pass_and_stay_reported(self) -> None:
        for shape, poles in ((Sphere(5), 2), (Cone(5, 0, 10), 1)):
            self.assertTrue(shape.is_valid)
            with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
                checks = check_map(hard_checks(context(shape, Path(tmp))))
            self.assertEqual(checks["edge_quality"].status, "pass", checks["edge_quality"].actual)
            self.assertIn(f"surface_pole_edges={poles}", checks["edge_quality"].actual)
            self.assertIn("degenerate_edges=0", checks["edge_quality"].actual)
            self.assertTrue(checks["edge_quality"].heuristic)
            self.assertEqual(checks["mesh_watertight"].status, "pass", checks["mesh_watertight"].actual)
            self.assertNotIn("collapsed_triangles=0", checks["mesh_watertight"].actual)

    def test_collapsed_triangles_cannot_hide_an_open_mesh(self) -> None:
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
        triangles = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 0, 1]]  # Missing face, plus a collapsed one.
        check = _check_mesh_arrays(vertices, triangles, GateTolerances())
        self.assertEqual(check.status, "fail")
        self.assertIn("collapsed_triangles=1", check.actual)
        self.assertIn("boundary_edges=3", check.actual)

    def test_only_collapsed_triangles_do_not_pass(self) -> None:
        check = _check_mesh_arrays([[0, 0, 0], [1, 0, 0]], [[0, 0, 1]], GateTolerances())
        self.assertEqual(check.status, "fail")

    def test_undefined_zero_area_kernel_face_is_rejected(self) -> None:
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Face

        raw_face = TopoDS_Face()
        BRep_Builder().MakeFace(raw_face)
        shape = Face(raw_face)
        self.assertEqual(shape.area, 0)
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(shape, Path(tmp))))
        self.assertEqual(checks["brep_valid"].status, "fail")
        self.assertEqual(checks["face_quality"].status, "fail")
        self.assertIn("zero_area_faces=1", checks["face_quality"].actual)
        self.assertNotEqual(checks["mesh_watertight"].status, "pass")

    def test_tiny_edges_and_sliver_faces_use_configured_thresholds(self) -> None:
        thin_box = Box(1, 1, 0.0001)
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
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
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
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
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
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
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
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
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            part = check_map(hard_checks(context(shape, Path(tmp), kind="part")))
            assembly = check_map(hard_checks(context(shape, Path(tmp), kind="assembly")))
            unknown = check_map(hard_checks(context(shape, Path(tmp), kind="2d")))

        self.assertEqual(part["solid_count"].status, "fail")
        self.assertEqual(part["solid_count"].expected, "exactly 1 solid for a part")
        self.assertIn("must contain exactly one solid", part["solid_count"].message)
        self.assertIn("Use an assembly project", part["solid_count"].message)
        self.assertEqual(assembly["solid_count"].status, "pass")
        self.assertEqual(assembly["solid_count"].actual, "2 solid(s)")
        self.assertEqual(assembly["solid_count"].expected, "at least 1 solid for an assembly")
        self.assertTrue(all(c.status == "pass" for c in assembly.values() if c.level == "error"))
        self.assertEqual(assembly["multiple_solids"].status, "warn")
        self.assertEqual(assembly["multiple_solids"].level, "warn")
        self.assertTrue(assembly["multiple_solids"].heuristic)
        self.assertEqual(unknown["solid_count"].status, "skip")
        self.assertEqual(unknown["solid_count"].level, "error")

    def test_interference_treats_touching_boxes_as_clear(self) -> None:
        shape = Compound(children=[Box(2, 2, 2), Box(2, 2, 2).translate((2, 0, 0))])
        check = interference_checks(context(shape, Path("."), kind="assembly"))[0]

        actual = json.loads(check.actual)
        self.assertEqual(check.status, "pass")
        self.assertEqual(actual["volume_threshold_mm3"], 1e-6)
        self.assertEqual(actual["total_overlapping_pairs"], 0)
        self.assertEqual(actual["total_solid_pairs"], 1)
        self.assertEqual(actual["boolean_attempts"], 0)

    def test_interference_reports_labeled_overlaps_volume_and_location(self) -> None:
        left = Box(2, 2, 2)
        left.label = "left"
        right = Box(2, 2, 2).translate((1, 0, 0))
        right.label = "right"
        shape = Compound(children=[left, right])
        check = interference_checks(context(shape, Path("."), kind="assembly"))[0]

        actual = json.loads(check.actual)
        self.assertEqual(check.status, "fail")
        self.assertEqual(actual["total_overlapping_pairs"], 1)
        overlap = actual["reported_overlaps"][0]
        self.assertEqual([part["label"] for part in overlap["parts"]], ["left", "right"])
        self.assertTrue(all(part["path"] for part in overlap["parts"]))
        self.assertAlmostEqual(overlap["overlap_volume_mm3"], 4.0)
        for actual_coordinate, expected_coordinate in zip(overlap["location_mm"], (0.5, 0.0, 0.0)):
            self.assertAlmostEqual(actual_coordinate, expected_coordinate, places=9)

    def test_interference_reports_paths_and_separates_declared_contact(self) -> None:
        from aad import assembly

        asm = assembly("stack")
        asm.add("left", Box(2, 2, 2))
        asm.add("right", Box(2, 2, 2), at=Pos(1, 0, 0))
        asm.allow_overlap("left", "right", reason="press fit", max_volume_mm3=5)
        ctx = context(asm.compound(), Path("."), kind="assembly")
        ctx.meta = {"assembly": asm.record()}
        check = interference_checks(ctx)[0]
        actual = json.loads(check.actual)
        self.assertEqual(check.status, "warn")
        self.assertEqual(actual["confirmed_overlapping_pairs"], 1)
        self.assertEqual(actual["undeclared_overlapping_pairs"], 0)
        self.assertEqual(actual["allowed_overlap_pairs"], 1)
        allowed = actual["reported_allowed_overlaps"][0]
        self.assertEqual(allowed["reason"], "press fit")
        self.assertEqual([part["label"] for part in allowed["parts"]], ["left", "right"])
        self.assertTrue(all(part["path"] for part in allowed["parts"]))
        self.assertAlmostEqual(allowed["overlap_volume_mm3"], 4)

    def test_declared_contact_volume_limit_does_not_hide_excess_overlap(self) -> None:
        from aad import assembly

        asm = assembly("stack")
        asm.add("left", Box(2, 2, 2))
        asm.add("right", Box(2, 2, 2), at=Pos(1, 0, 0))
        asm.allow_overlap("left", "right", reason="small press fit", max_volume_mm3=2)
        ctx = context(asm.compound(), Path("."), kind="assembly")
        ctx.meta = {"assembly": asm.record()}
        actual = json.loads(interference_checks(ctx)[0].actual)
        self.assertEqual(actual["confirmed_overlapping_pairs"], 1)
        self.assertEqual(actual["allowed_overlap_pairs"], 0)

    def test_library_bearing_rolling_contacts_are_reported_as_intended(self) -> None:
        from aad import assembly
        from aad_quality.standard_parts import make_part

        asm = assembly("bearing_test")
        asm.add("bearing", make_part("bearing.deep_groove_ball.skt", size="M8-22-7"))
        ctx = context(asm.compound(), Path("."), kind="assembly")
        ctx.meta = {"assembly": asm.record()}
        actual = json.loads(interference_checks(ctx)[0].actual)
        self.assertGreater(actual["allowed_overlap_pairs"], 0)
        self.assertEqual(actual["confirmed_overlapping_pairs"], actual["allowed_overlap_pairs"])
        self.assertEqual(actual["undeclared_overlapping_pairs"], 0)
        self.assertTrue(all("Library bearing" in pair["reason"] for pair in actual["reported_allowed_overlaps"]))

    def test_interference_budget_overrun_is_unknown_not_pass(self) -> None:
        shape = Compound(children=[Box(2, 2, 2), Box(2, 2, 2).translate((1, 0, 0))])
        with patch("aad.engine.gate.monotonic", side_effect=[0.0, 2.0, 2.0]):
            check = interference_checks(context(shape, Path("."), kind="assembly"), time_budget_s=1)[0]
        self.assertEqual(check.status, "skip")
        self.assertIn("No pass is reported", check.message)

    def test_plain_multisolid_build_surfaces_interference_in_summary(self) -> None:
        source = "result = Compound(children=[Box(2,2,2), Pos(1,0,0) * Box(2,2,2)])"
        with tempfile.TemporaryDirectory(prefix="aad-interference-build-") as temporary:
            result = run_build(BuildRequest(source=source, project_dir=Path(temporary), kind="assembly"))
        self.assertTrue(any(check.id == "interference_pairs" and check.status == "warn" for check in result.checks))
        self.assertIn("BUILD INTERFERENCE REVIEW REQUIRED", result.summary)

    def test_short_timeout_build_returns_revision_with_unknown_interference(self) -> None:
        source = "import time\ntime.sleep(12)\nresult = Compound(children=[Box(2,2,2), Pos(1,0,0) * Box(2,2,2)])"
        with tempfile.TemporaryDirectory(prefix="aad-short-build-timeout-") as temporary:
            result = run_build(BuildRequest(source=source, project_dir=Path(temporary), kind="assembly", timeout_s=20))
        self.assertTrue(result.ok, result.failure.message if result.failure else result.summary)
        self.assertIn("part.brep", result.artifacts)
        check = next(check for check in result.checks if check.id == "interference_pairs")
        self.assertEqual(check.status, "skip")
        self.assertIn("aad_check(suites=[\"interference\"])", check.message)

    def test_interference_recognizes_clear_nested_parts(self) -> None:
        outer = Box(10, 10, 10) - Box(8, 8, 8)
        inner = Box(6, 6, 6)
        shape = Compound(children=[outer, inner])
        check = interference_checks(context(shape, Path("."), kind="assembly"))[0]

        actual = json.loads(check.actual)
        self.assertEqual(check.status, "pass")
        self.assertEqual(actual["candidate_pairs"], 1)
        self.assertEqual(actual["boolean_completions"], 1)
        self.assertEqual(actual["total_overlapping_pairs"], 0)

    def test_interference_caps_details_but_reports_the_total(self) -> None:
        shape = Compound(children=[Box(2, 2, 2) for _ in range(7)])
        check = interference_checks(context(shape, Path("."), kind="assembly"))[0]

        actual = json.loads(check.actual)
        self.assertEqual(check.status, "fail")
        self.assertEqual(actual["total_overlapping_pairs"], 21)
        self.assertEqual(actual["confirmed_overlapping_pairs"], 21)
        self.assertEqual(len(actual["reported_overlaps"]), 20)
        self.assertTrue(actual["reported_overlaps_truncated"])

    def test_interference_boolean_failure_is_unknown_not_a_pass(self) -> None:
        shape = Compound(children=[Box(2, 2, 2), Box(2, 2, 2).translate((1, 0, 0))])
        with patch("aad.engine.gate._solid_intersection_mm", side_effect=RuntimeError("forced common failure")):
            check = interference_checks(context(shape, Path("."), kind="assembly"))[0]

        actual = json.loads(check.actual)
        self.assertEqual(check.status, "skip")
        self.assertEqual(actual["unknown_boolean_pairs"], 1)
        self.assertEqual(actual["reported_unknown_pairs"][0]["status"], "unknown")
        self.assertIn("forced common failure", actual["reported_unknown_pairs"][0]["reason"])

    def test_interference_exhausted_budget_is_not_a_pass(self) -> None:
        shape = Compound(children=[Box(2, 2, 2), Box(2, 2, 2).translate((1, 0, 0))])
        check = interference_checks(context(shape, Path("."), kind="assembly"), time_budget_s=0)[0]

        actual = json.loads(check.actual)
        self.assertEqual(check.status, "skip")
        self.assertTrue(actual["budget_exhausted"])
        self.assertEqual(actual["unchecked_pairs"], 1)
        self.assertIsNone(actual["total_overlapping_pairs"])

    def test_nested_assemblies_use_native_solid_volumes_in_gate_and_metrics(self) -> None:
        source = 'result = Compound(children=[Compound(children=[Box(2,3,4).translate((3,5,7))])])'
        for units, factor in (("mm", 1), ("in", 25.4)):
            with self.subTest(units=units), tempfile.TemporaryDirectory(prefix="aad-gate-nested-") as temporary:
                nested = Compound(children=[Compound(children=[Box(2, 3, 4)])])
                ctx = context(nested, Path(temporary), kind="assembly", units=units)
                step = check_map(hard_checks(ctx))["step_volume_drift"]
                self.assertEqual(step.status, "pass", step.actual)
                self.assertAlmostEqual(actual_number(step, "source_volume") / factor**3, 24)
                self.assertAlmostEqual(actual_number(step, "step_volume") / factor**3, 24)
                result = run_build(BuildRequest(source=source, project_dir=Path(temporary), kind="assembly", units=units))
                self.assertTrue(result.ok, result.failure)
                self.assertEqual(result.status, "draft")
                self.assertFalse(result.requirements_proven)
                self.assertAlmostEqual(result.metrics["volume"], 24)
                for actual, expected in zip(result.metrics["center_of_mass"], (3, 5, 7)):
                    self.assertAlmostEqual(actual, expected)

    def test_step_roundtrip_drift_fault_injection_fails_with_measured_volumes(self) -> None:
        source = Box(10, 10, 10)
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            with patch("aad.engine.gate._import_step_shape", return_value=Box(10.1, 10, 10)):
                checks = check_map(hard_checks(context(source, Path(tmp))))

        step = checks["step_volume_drift"]
        self.assertEqual(step.status, "fail")
        self.assertAlmostEqual(actual_number(step, "source_volume"), 1000, places=8)
        self.assertAlmostEqual(actual_number(step, "step_volume"), 1010, places=8)
        self.assertIn("configured_absolute_threshold=1e-06 mm³", step.actual)

    def test_step_failures_skip_and_clean_unique_job_local_files_on_repeat(self) -> None:
        shape = Box(2, 3, 4)
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            sentinel = job / "roundtrip.step"
            sentinel.write_text("keep", encoding="utf-8")
            for _ in range(2):
                with patch("aad.engine.gate._import_step_shape", side_effect=RuntimeError("import fault")):
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
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            ctx = context(shape, Path(tmp))
            with patch("OCP.BRepCheck.BRepCheck_Analyzer", side_effect=RuntimeError("analyzer fault")):
                validity = check_map(hard_checks(ctx))["brep_valid"]
            with patch("aad.engine.gate._fresh_mesh_arrays", side_effect=RuntimeError("mesh fault")):
                mesh = check_map(hard_checks(ctx))["mesh_watertight"]
            with patch("build123d.export_step", return_value=True):
                step = check_map(hard_checks(ctx))["step_volume_drift"]
            self.assertFalse(list(Path(tmp).glob("aad-gate-*")))
        for check, detail in ((validity, "analyzer fault"), (mesh, "mesh fault"),
                              (step, "non-empty STEP file")):
            self.assertEqual(check.status, "skip")
            self.assertEqual(check.level, "error")
            self.assertIn(detail, check.actual)

    def test_invalid_roundtrip_volume_and_overflow_never_pass(self) -> None:
        shape = Box(2, 3, 4)
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            ctx = context(shape, Path(tmp))
            with patch("aad.engine.gate._import_step_shape", return_value=Compound(children=[])):
                zero = check_map(hard_checks(ctx, {"step_volume_abs_mm3": 1e10}))["step_volume_drift"]
            overflow = check_map(hard_checks(ctx, {"step_volume_rel": 1e308}))["step_volume_drift"]
        self.assertEqual(zero.status, "fail")
        self.assertEqual(overflow.status, "skip")
        self.assertEqual(overflow.level, "error")
        self.assertIn("not finite", overflow.actual)

    def test_worker_failure_names_the_actual_hard_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            result = run_build(BuildRequest(
                source="result = Box(0.001, 1, 1)", project_dir=Path(tmp), job_dir=Path(tmp) / "job"
            ))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure.code, "HARD_GATE_FAILED")
        self.assertTrue(result.metrics["valid"])
        self.assertIn("edge_quality", result.failure.message)
        self.assertNotIn("B-rep validity or solid-count", result.failure.message)

    def test_zero_volume_step_comparison_is_not_a_pass(self) -> None:
        shape = Shell([Box(4, 4, 4).faces()[0]])
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
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
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
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
        with tempfile.TemporaryDirectory(prefix="aad-gate-test-") as tmp:
            checks = check_map(hard_checks(context(Box(2, 2, 2), Path(tmp), units="cm")))
        self.assertEqual(checks["gate_units"].status, "fail")
        for check_id in ("edge_quality", "face_quality", "step_volume_drift", "mesh_watertight"):
            self.assertEqual(checks[check_id].status, "skip")
            self.assertEqual(checks[check_id].level, "error")


if __name__ == "__main__":
    unittest.main()
