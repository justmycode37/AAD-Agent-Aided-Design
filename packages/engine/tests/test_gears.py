from __future__ import annotations

import math
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

from build123d import GeomType, Pos

from aad import (
    gear_center_distance,
    gear_mesh_phase,
    gear_profile,
    gear_solid,
    helix_angle_for_center_distance,
    tapered_bevel_standin,
)
from aad.engine.gate import hard_checks
from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest, ModelContext


def _wrapped_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def _radius(point) -> float:
    return math.hypot(point.X, point.Y)


def _point_at_radius(edge, radius: float):
    """Find a point on a monotone involute spline at the requested radius."""
    lo, hi = 0.0, 1.0
    r_lo, r_hi = _radius(edge.position_at(lo)), _radius(edge.position_at(hi))
    if not min(r_lo, r_hi) - 1e-8 <= radius <= max(r_lo, r_hi) + 1e-8:
        raise AssertionError(f"radius {radius} is outside edge endpoints {r_lo}, {r_hi}")
    increasing = r_hi > r_lo
    for _ in range(55):
        middle = (lo + hi) / 2.0
        r_middle = _radius(edge.position_at(middle))
        if (r_middle < radius) == increasing:
            lo = middle
        else:
            hi = middle
    return edge.position_at((lo + hi) / 2.0)


def _circular_thickness(radius: float, first, second) -> float:
    angle1 = math.atan2(first.Y, first.X)
    angle2 = math.atan2(second.Y, second.X)
    return radius * abs(_wrapped_angle(angle2 - angle1))


def _sampled_profile_deviation(face, teeth: int, module: float, helix_angle: float = 0.0) -> float:
    wire = face.outer_wire()
    pitch = 2.0 * math.pi / teeth
    pressure = math.radians(20.0)
    beta = math.radians(helix_angle)
    transverse_module = module / math.cos(beta)
    transverse_pressure = math.atan(math.tan(pressure) / math.cos(beta))
    pitch_radius = teeth * transverse_module / 2.0
    base_radius = pitch_radius * math.cos(transverse_pressure)
    tip_radius = pitch_radius + module
    half_pitch = math.pi / (2.0 * teeth)
    involute_pitch = math.tan(transverse_pressure) - transverse_pressure
    splines = [edge for edge in wire.edges() if edge.geom_type == GeomType.BSPLINE]
    maximum = 0.0
    for edge in splines:
        for index in range(1, 201):
            point = edge.position_at(index / 201.0)
            radius = _radius(point)
            alpha = math.acos(min(1.0, base_radius / radius))
            involute = math.tan(alpha) - alpha
            half_angle = half_pitch + involute_pitch - involute
            angle = math.atan2(point.Y, point.X)
            center = round(angle / pitch) * pitch
            side = 1.0 if _wrapped_angle(angle - center) >= 0.0 else -1.0
            expected_angle = center + side * half_angle
            error = radius * abs(_wrapped_angle(angle - expected_angle))
            maximum = max(maximum, error)
    return maximum


class InvoluteGearProfileTests(unittest.TestCase):
    def test_importing_aad_and_gear_exports_does_not_load_the_kernel(self) -> None:
        code = (
            "import sys; import aad; assert 'build123d' not in sys.modules; "
            "from aad import gear_profile; assert callable(gear_profile); "
            "assert 'build123d' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", code], check=True, timeout=10)

    def test_analytic_tooth_thickness_and_sampled_involute_error(self) -> None:
        maximum_deviation = 0.0
        for module in (1.0, 2.5, 6.0):
            with self.subTest(module=module):
                face = gear_profile(40, module, profile_tolerance=0.005, undercut="ignore")
                self.assertTrue(face.is_valid)
                wire = face.outer_wire()
                edges = wire.edges()
                flanks = [edge for edge in edges if edge.geom_type == GeomType.BSPLINE]
                self.assertEqual(len(flanks), 80)

                alpha = math.radians(20.0)
                pitch_radius = 20.0 * module
                base_radius = pitch_radius * math.cos(alpha)
                tip_radius = pitch_radius + module
                root_radius = pitch_radius - 1.25 * module
                self.assertLess(root_radius, base_radius)
                self.assertAlmostEqual(wire.bounding_box().max.X, tip_radius, delta=0.005)

                inv = lambda angle: math.tan(angle) - angle
                half_pitch = math.pi / 80.0
                base_half = half_pitch + inv(alpha)

                def expected_thickness(radius: float) -> float:
                    if radius <= base_radius:
                        half_angle = base_half
                    else:
                        local_alpha = math.acos(base_radius / radius)
                        half_angle = half_pitch + inv(alpha) - inv(local_alpha)
                    return 2.0 * radius * half_angle

                pitch_points = [_point_at_radius(edge, pitch_radius) for edge in flanks[:2]]
                base_points = [
                    min((edge.position_at(0.0), edge.position_at(1.0)), key=_radius)
                    for edge in flanks[:2]
                ]
                tip_points = [
                    max((edge.position_at(0.0), edge.position_at(1.0)), key=_radius)
                    for edge in flanks[:2]
                ]
                self.assertTrue(all(abs(_radius(point) - base_radius) < 1e-6 for point in base_points))
                self.assertTrue(all(abs(_radius(point) - tip_radius) < 1e-6 for point in tip_points))
                root_angles = []
                for edge in edges:
                    if edge.geom_type != GeomType.LINE:
                        continue
                    for point in (edge.position_at(0.0), edge.position_at(1.0)):
                        angle = math.atan2(point.Y, point.X)
                        if abs(_radius(point) - root_radius) < 1e-6 and abs(_wrapped_angle(angle)) < math.pi / 40.0:
                            root_angles.append(angle)
                self.assertEqual(len(root_angles), 2)

                measured = {
                    "pitch": _circular_thickness(pitch_radius, *pitch_points),
                    "base": _circular_thickness(base_radius, *base_points),
                    "tip": _circular_thickness(tip_radius, *tip_points),
                    "root": root_radius * abs(root_angles[1] - root_angles[0]),
                }
                expected = {
                    "pitch": math.pi * module / 2.0,
                    "base": expected_thickness(base_radius),
                    "tip": expected_thickness(tip_radius),
                    "root": expected_thickness(root_radius),
                }
                for location in expected:
                    self.assertAlmostEqual(measured[location], expected[location], delta=2e-5)

                deviation = _sampled_profile_deviation(face, 40, module)
                maximum_deviation = max(maximum_deviation, deviation)
                self.assertLessEqual(deviation, 0.01, f"module {module}: {deviation:.8f} mm")
        self.assertLessEqual(maximum_deviation, 0.01)

    def test_helical_profile_uses_the_transverse_module_and_pressure_angle(self) -> None:
        teeth, module, beta_deg = 13, 2.5, 31.0
        face = gear_profile(
            teeth,
            module,
            helix_angle=beta_deg,
            profile_tolerance=0.005,
            undercut="ignore",
        )
        edges = face.outer_wire().edges()
        flanks = [edge for edge in edges if edge.geom_type == GeomType.BSPLINE]
        beta = math.radians(beta_deg)
        alpha_n = math.radians(20.0)
        alpha_t = math.atan(math.tan(alpha_n) / math.cos(beta))
        transverse_module = module / math.cos(beta)
        pitch_radius = teeth * transverse_module / 2.0
        base_radius = pitch_radius * math.cos(alpha_t)
        tip_radius = pitch_radius + module
        root_radius = pitch_radius - 1.25 * module
        half_pitch = math.pi / (2.0 * teeth)
        involute = lambda angle: math.tan(angle) - angle
        base_half = half_pitch + involute(alpha_t)

        def expected_thickness(radius: float) -> float:
            if radius <= base_radius:
                half_angle = base_half
            else:
                local_alpha = math.acos(base_radius / radius)
                half_angle = half_pitch + involute(alpha_t) - involute(local_alpha)
            return 2.0 * radius * half_angle

        pitch_points = [_point_at_radius(edge, pitch_radius) for edge in flanks[:2]]
        base_points = [min((edge.position_at(0), edge.position_at(1)), key=_radius) for edge in flanks[:2]]
        tip_points = [max((edge.position_at(0), edge.position_at(1)), key=_radius) for edge in flanks[:2]]
        self.assertTrue(all(abs(_radius(point) - base_radius) < 1e-6 for point in base_points))
        self.assertTrue(all(abs(_radius(point) - tip_radius) < 1e-6 for point in tip_points))

        root_angles = []
        for edge in edges:
            if edge.geom_type != GeomType.LINE:
                continue
            for point in (edge.position_at(0), edge.position_at(1)):
                angle = math.atan2(point.Y, point.X)
                if abs(_radius(point) - root_radius) < 1e-6 and abs(_wrapped_angle(angle)) < math.pi / teeth:
                    root_angles.append(angle)
        self.assertEqual(len(root_angles), 2)

        measured = (
            _circular_thickness(pitch_radius, *pitch_points),
            _circular_thickness(base_radius, *base_points),
            _circular_thickness(tip_radius, *tip_points),
            root_radius * abs(root_angles[1] - root_angles[0]),
        )
        expected = (
            math.pi * transverse_module / 2.0,
            expected_thickness(base_radius),
            expected_thickness(tip_radius),
            expected_thickness(root_radius),
        )
        for actual, analytic in zip(measured, expected, strict=True):
            self.assertAlmostEqual(actual, analytic, delta=2e-5)
        deviation = _sampled_profile_deviation(face, teeth, module, beta_deg)
        self.assertLessEqual(deviation, 0.01, f"helical profile deviation: {deviation:.8f} mm")

    def test_profile_shift_and_backlash_adjust_pitch_thickness(self) -> None:
        module = 2.0
        teeth = 40
        shift = 0.2
        backlash = 0.1
        face = gear_profile(
            teeth,
            module,
            profile_shift=shift,
            backlash=backlash,
            undercut="ignore",
        )
        flanks = [edge for edge in face.outer_wire().edges() if edge.geom_type == GeomType.BSPLINE]
        radius = teeth * module / 2.0
        points = [_point_at_radius(edge, radius) for edge in flanks[:2]]
        measured = _circular_thickness(radius, *points)
        expected = math.pi * module / 2.0 + 2.0 * shift * module * math.tan(math.radians(20.0)) - backlash
        self.assertAlmostEqual(measured, expected, delta=2e-5)

    def test_internal_profile_and_tapered_bevel_standin_are_valid(self) -> None:
        ring = gear_profile(60, 2.0, internal=True, backlash=0.02, undercut="ignore")
        self.assertTrue(ring.is_valid)
        self.assertEqual(len(ring.wires()), 2)
        internal_solid = gear_solid(60, 2.0, 8.0, internal=True, undercut="ignore")
        self.assertTrue(internal_solid.is_valid)
        self.assertEqual(len(internal_solid.solids()), 1)

        bevel = tapered_bevel_standin(24, 2.0, 12.0, tip_scale=0.75, bore=10.0, undercut="ignore")
        self.assertTrue(bevel.is_valid)
        self.assertEqual(len(bevel.solids()), 1)

    def test_undercut_is_detected_and_actionable(self) -> None:
        with self.assertWarnsRegex(UserWarning, "predicted rack undercut"):
            gear_profile(17, 2.5, undercut="warn")
        with self.assertRaisesRegex(ValueError, "increase teeth/profile_shift"):
            gear_profile(17, 2.5, undercut="error")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            gear_profile(13, 2.5, helix_angle=31.0, undercut="warn")
        self.assertFalse(caught)

    def test_mesh_calculations_round_trip_and_invalid_inputs_explain_repairs(self) -> None:
        target = 89.0
        beta = helix_angle_for_center_distance(2.5, 13, 48, target)
        self.assertAlmostEqual(beta, 31.0473, delta=0.001)
        self.assertAlmostEqual(gear_center_distance(2.5, 13, 48, beta), target, places=10)
        self.assertAlmostEqual(gear_center_distance(2.0, 20, 60, internal=True), 40.0)
        self.assertAlmostEqual(gear_mesh_phase(13, 48), 183.75)
        self.assertGreater(gear_mesh_phase(20, 60, internal=True), 0.0)
        with self.assertRaisesRegex(ValueError, "increase shaft spacing"):
            helix_angle_for_center_distance(2.5, 13, 48, 70.0)
        with self.assertRaisesRegex(ValueError, "increase the face width"):
            gear_solid(25, 2.0, 0.0)


class InvoluteGearSolidTests(unittest.TestCase):
    def test_nominal_meshes_have_backlash_and_closer_meshes_collide_across_the_face(self) -> None:
        cases = (
            ("helical 13/48", 2.5, 13, 48, 31.0),
            ("helical 54/28", 2.0, 54, 28, 22.9),
            ("spur 17/25", 2.5, 17, 25, 0.0),
            ("spur 17/49", 2.5, 17, 49, 0.0),
            ("spur 25/49", 2.5, 25, 49, 0.0),
        )
        width = 8.0
        allowance_per_gear = 0.05
        for name, module, z1, z2, beta in cases:
            with self.subTest(pair=name), warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                center_distance = gear_center_distance(module, z1, z2, beta)
                phase = gear_mesh_phase(z1, z2)
                gear1 = gear_solid(
                    z1,
                    module,
                    width,
                    helix_angle=beta,
                    hand="right",
                    backlash=allowance_per_gear,
                    undercut="ignore",
                )
                gear2 = gear_solid(
                    z2,
                    module,
                    width,
                    helix_angle=beta,
                    hand="left",
                    backlash=allowance_per_gear,
                    phase=phase,
                    x=center_distance,
                    undercut="ignore",
                )

            nominal = gear1 & gear2
            nominal_volume = 0.0 if nominal is None else nominal.volume
            self.assertLessEqual(nominal_volume, 1e-12, f"{name}: nominal intersection={nominal_volume}")

            transverse_module = module / math.cos(math.radians(beta))
            pitch = math.pi * transverse_module
            tooth_thickness = pitch / 2.0 - allowance_per_gear
            circular_backlash = pitch - tooth_thickness - tooth_thickness
            self.assertAlmostEqual(circular_backlash, 2.0 * allowance_per_gear)
            self.assertGreater(circular_backlash, 0.0)

            closer = gear1 & (Pos(-0.5, 0, 0) * gear2)
            self.assertIsNotNone(closer, f"{name}: expected collision 0.5 mm closer")
            self.assertGreater(closer.volume, 0.0)
            self.assertGreaterEqual(closer.bounding_box().size.Z, width * 0.95)

    def test_gear_passes_real_hard_checks(self) -> None:
        shapes = (
            ("spur", gear_solid(25, 2.0, 8.0, bore=8.0, backlash=0.04, undercut="ignore")),
            ("internal ring", gear_solid(60, 2.0, 8.0, internal=True, undercut="ignore")),
            ("tapered stand-in", tapered_bevel_standin(24, 2.0, 12.0, bore=10.0, undercut="ignore")),
        )
        expected_ids = {
            "shape_present",
            "brep_valid",
            "solid_count",
            "closed_shells",
            "edge_quality",
            "face_quality",
            "step_volume_drift",
            "mesh_watertight",
        }
        for label, gear in shapes:
            with self.subTest(shape=label), tempfile.TemporaryDirectory(prefix="aad-gear-gate-") as temporary:
                job_dir = Path(temporary) / "job"
                job_dir.mkdir()
                checks = hard_checks(ModelContext(shape=gear, units="mm", kind="part", job_dir=job_dir))
            errors = [check for check in checks if check.level == "error"]
            self.assertTrue(errors)
            self.assertTrue(
                all(check.status == "pass" for check in errors),
                {check.id: (check.status, check.actual) for check in errors},
            )
            self.assertEqual({check.id for check in errors}, expected_ids)

    def test_worker_builds_a_gear_with_the_sandboxed_wildcard_import(self) -> None:
        source = '''from aad import *
result = gear_solid(
    13,
    2.5,
    8,
    helix_angle=31,
    hand="right",
    backlash=0.05,
    bore=8,
    undercut="ignore",
)
'''
        with tempfile.TemporaryDirectory(prefix="aad-gear-worker-") as temporary:
            root = Path(temporary)
            result = run_build(
                BuildRequest(
                    source=source,
                    project_dir=root,
                    job_dir=root / "job",
                    timeout_s=120,
                )
            )
            artifact_exists = Path(result.artifacts["part.brep"]).is_file()
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(result.failure)
        self.assertEqual(result.metrics["solids"], 1)
        self.assertTrue(result.metrics["valid"])
        hard = [check for check in result.checks if check.suite == "hard" and check.level == "error"]
        self.assertTrue(hard)
        self.assertTrue(all(check.status == "pass" for check in hard))
        self.assertTrue(artifact_exists)


if __name__ == "__main__":
    unittest.main()
