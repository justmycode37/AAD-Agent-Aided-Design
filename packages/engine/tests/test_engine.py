from __future__ import annotations

import json
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from aad.engine.jobs import _finish_phase_timings, run_build
from aad.engine.types import BuildRequest, BuildResult

ROOT = Path(__file__).resolve().parents[3]


class EngineBootstrapTests(unittest.TestCase):
    def test_types_import_does_not_import_kernel(self) -> None:
        code = (
            "import sys; from typing import get_type_hints; "
            "import aad.engine.types as engine_types; "
            "get_type_hints(engine_types.ModelContext); "
            "assert 'build123d' not in sys.modules; "
            "assert 'OCP' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", code], check=True, timeout=10)

    def test_box_script_returns_metrics_and_loadable_brep(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
            root = Path(temporary)
            result = run_build(
                BuildRequest(
                    source="result = Box(20, 10, 6)",
                    project_dir=root,
                    job_dir=root / "job",
                    rev=3,
                )
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(result.status, "draft")
            self.assertFalse(result.requirements_proven)
            self.assertEqual(result.metrics["bbox"], [20.0, 10.0, 6.0])
            self.assertAlmostEqual(result.metrics["volume"], 1200.0, places=5)
            self.assertAlmostEqual(result.metrics["area"], 760.0, places=5)
            self.assertTrue(result.metrics["valid"])
            self.assertEqual(result.metrics["solids"], 1)
            self.assertEqual(result.metrics["faces"], 6)
            self.assertEqual(result.metrics["edges"], 12)
            self.assertEqual(result.metrics["build_ms"], result.build_ms)
            phase_timings = result.metrics["phase_timings_ms"]
            # The model worker measures extraction and BREP writing. Hard,
            # spec, process and scene checks run later in the trusted validator.
            for phase in ("model", "write", "other"):
                self.assertIn(phase, phase_timings)
                self.assertGreaterEqual(phase_timings[phase], 0)
            self.assertIn("validation_ms", result.metrics)
            self.assertIn("validation_only_ms", result.metrics)
            self.assertIn("scene_ms", result.metrics)
            self.assertGreaterEqual(result.metrics["validation_ms"], result.metrics["scene_ms"])
            self.assertEqual(result.context.metrics["phase_timings_ms"], phase_timings)
            self.assertEqual(result.context.features[0].name, "main")
            self.assertEqual(len(result.context.features[0].faces), 6)
            self.assertIsNone(result.context.shape)
            brep_path = Path(result.artifacts["part.brep"])
            self.assertTrue(brep_path.is_file())

            from build123d import import_brep

            loaded = import_brep(brep_path)
            self.assertTrue(loaded.is_valid)
            self.assertEqual(len(loaded.solids()), 1)
            self.assertAlmostEqual(loaded.volume, 1200.0, places=5)

    def test_slow_build_summary_names_the_longest_timed_phase(self) -> None:
        result = BuildResult(
            summary="Built the model.",
            metrics={"phase_timings_ms": {"model": 1_000.0, "gate.mesh_watertight": 22_500.0}},
        )
        _finish_phase_timings(result, 25_000.0)
        self.assertIn("Slowest phase: gate.mesh_watertight (22.5 s)", result.summary)
        self.assertIn("other", result.metrics["phase_timings_ms"])
        self.assertEqual(result.metrics["build_ms"], 25_000.0)


    def test_buildpart_fixture_produces_implicit_main_feature(self) -> None:
        source = (ROOT / "fixtures" / "models" / "fan_mount.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
            root = Path(temporary)
            result = run_build(
                BuildRequest(
                    source=source,
                    project_dir=root,
                    job_dir=root / "job",
                    params={"thickness": 8.0},
                    process="cnc_3axis",
                )
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(result.status, "draft")
            self.assertEqual(result.context.params["thickness"], 8.0)
            self.assertEqual(result.context.features[0].name, "main")
            self.assertEqual(result.metrics["bbox"], [60.0, 40.0, 8.0])
            self.assertEqual(result.metrics["solids"], 1)
            self.assertTrue(result.metrics["valid"])
            self.assertTrue(Path(result.artifacts["part.brep"]).is_file())
            process_checks = [check for check in result.checks if check.suite == "process"]
            self.assertTrue(process_checks)
            if importlib.util.find_spec("aad_quality") is None:
                self.assertTrue(any(check.id == "process_evaluator_unavailable" and check.status == "skip"
                                    for check in process_checks))
            if importlib.util.find_spec("aad_quality") is not None:
                self.assertTrue(any(check.id == "spec:empty" for check in result.checks))

    def test_syntax_and_runtime_failures_report_source_location(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
            root = Path(temporary)
            syntax = run_build(
                BuildRequest(source="result = Box(10, 10, 6", project_dir=root, job_dir=root / "syntax")
            )
            self.assertEqual(syntax.failure.code, "SYNTAX_ERROR")
            self.assertEqual(syntax.failure.phase, "syntax")
            self.assertEqual(syntax.failure.file, "model.py")
            self.assertEqual(syntax.failure.line, 1)

            runtime = run_build(
                BuildRequest(
                    source="result = Box(10, 10, 6)\nraise RuntimeError('fixture failure')",
                    project_dir=root,
                    job_dir=root / "runtime",
                )
            )
            self.assertEqual(runtime.failure.code, "RUNTIME_ERROR")
            self.assertEqual(runtime.failure.phase, "runtime")
            self.assertEqual(runtime.failure.file, "model.py")
            self.assertEqual(runtime.failure.line, 2)
            self.assertIn("fixture failure", runtime.failure.kernel_message)

    def test_model_allocation_failure_returns_out_of_memory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aad-engine-oom-") as temporary:
            root = Path(temporary)
            result = run_build(
                BuildRequest(source="raise MemoryError('bounded allocation fixture')", project_dir=root),
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure.code, "OUT_OF_MEMORY")
        self.assertEqual(result.failure.phase, "oom")
        self.assertIn("bounded allocation fixture", result.failure.kernel_message)

    def test_kernel_refusals_get_their_documented_codes(self):
        cases = {
            "KERNEL_FILLET_FAILED": "with BuildPart() as b:\n    Box(10, 10, 10)\n    fillet(b.edges(), radius=8)\nresult = b.part",
            "KERNEL_CHAMFER_FAILED": "with BuildPart() as b:\n    Box(10, 10, 10)\n    chamfer(b.edges(), length=8)\nresult = b.part",
            "KERNEL_SWEEP_FAILED": "with BuildPart() as b:\n    with BuildSketch(Plane.XZ):\n        Rectangle(10, 10)\n    revolve(axis=Axis.Z)\nresult = b.part",
            "KERNEL_OTHER": "result = Box(0, 1, 1)",
            # Script and API mistakes are not kernel refusals.
            "RUNTIME_ERROR": "raise ValueError('my own check')",
        }
        for code, body in cases.items():
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = run_build(BuildRequest(
                    source="from build123d import *\n" + body, project_dir=root, job_dir=root / "job",
                ))
                self.assertEqual(result.failure.code, code, result.failure.kernel_message)
                self.assertEqual(result.failure.phase, "kernel" if code != "RUNTIME_ERROR" else "runtime")
                self.assertIsNotNone(result.failure.line)
                self.assertTrue(result.failure.repair)

    def test_builder_misuse_and_typos_stay_runtime_errors(self):
        for body in (
            "with BuildPart():\n    with BuildSketch():\n        Polyline((0, 0), (1, 1), (1, 0))",
            "Box(1, 1, 1).nope",
            "Box(1, 1, 1, color=2)",
        ):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = run_build(BuildRequest(
                    source="from build123d import *\n" + body, project_dir=root, job_dir=root / "job",
                ))
                self.assertEqual(result.failure.code, "RUNTIME_ERROR", result.failure.kernel_message)

    def test_optional_hook_failures_warn_without_discarding_brep(self) -> None:
        source = """from pathlib import Path
import sys
Path("aad_quality.py").write_text(
    "def spec_checks(ctx):\\n    raise RuntimeError('quality hook fixture')\\n"
)
Path("aad_render.py").write_text(
    "def make_scene(ctx, out):\\n    raise RuntimeError('scene hook fixture')\\n"
    "def render(ctx, req, out):\\n    raise RuntimeError('render hook fixture')\\n"
)
sys.path.insert(0, str(Path.cwd()))
result = Box(4, 5, 6)
"""
        with tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
            root = Path(temporary)
            result = run_build(
                BuildRequest(
                    source=source,
                    project_dir=root,
                    job_dir=root / "job",
                    spec={"requirements": [{"id": "fit"}]},
                    preview=False,
                )
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(result.status, "draft")
            self.assertTrue(Path(result.artifacts["part.brep"]).is_file())
            self.assertIsNone(result.verification_receipt)
            self.assertFalse(any(check.id == "spec_checks_hook_failed" for check in result.checks))
            scene_failure = next((check for check in result.checks if check.id == "scene_generation_failed"), None)
            if importlib.util.find_spec("aad_render") is None:
                self.assertIsNotNone(scene_failure)
                self.assertIn("clean scene", scene_failure.message)
            else:
                self.assertIsNone(scene_failure)
            # The BREP is rendered by the clean parent-owned process, which
            # ignores the model's forged aad_render module.
            if result.scene is not None:
                self.assertAlmostEqual(result.scene.bbox[3] - result.scene.bbox[0], 4.0)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup")
    def test_cleanup_kills_descendants_after_the_worker_leader_exits(self) -> None:
        # Trusted lifecycle fixture, deliberately outside the model launcher:
        # macOS correctly denies subprocess creation from sandboxed model code.
        program = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print('leader exited', flush=True)"
        with tempfile.TemporaryDirectory(prefix="aad-trusted-cleanup-") as temporary:
            process = subprocess.Popen([sys.executable, "-c", program], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                process.wait(timeout=3)
                started = time.monotonic()
                with patch("aad.engine.jobs.sandbox.popen", return_value=process):
                    built = run_build(BuildRequest(source="trusted cleanup fixture", project_dir=Path(temporary),
                                                   job_dir=Path(temporary) / "job", timeout_s=0.1))
                self.assertLess(time.monotonic() - started, 5)
                self.assertEqual(built.failure.code, "TIMEOUT")
                self.assertEqual(built.failure.phase, "timeout")
                self.assertIn("leader exited", built.stdout)
                self.assertEqual(process.returncode, 0)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate(timeout=2)

    @unittest.skipUnless(os.name == "posix", "POSIX orphan-pipe cleanup")
    def test_pipe_drain_is_bounded_when_a_descendant_leaves_the_group(self) -> None:
        from aad.engine.jobs import _kill_worker
        with tempfile.TemporaryDirectory(prefix="aad-trusted-cleanup-") as temporary:
            pid_file = Path(temporary) / "child.pid"
            # This trusted test process may create an orphan holding the pipes;
            # production model launches always go through sandbox.popen.
            program = (
                "import subprocess,sys; from pathlib import Path; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], start_new_session=True); "
                f"Path({str(pid_file)!r}).write_text(str(child.pid)); "
                "print('orphan pipe log', flush=True); print('orphan pipe detail', file=sys.stderr, flush=True)"
            )
            process = subprocess.Popen([sys.executable, "-c", program], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                process.wait(timeout=3)
                started = time.monotonic()
                with patch("aad.engine.jobs.sandbox.popen", return_value=process):
                    built = run_build(BuildRequest(source="trusted orphan-pipe fixture", project_dir=Path(temporary),
                                                   job_dir=Path(temporary) / "job", timeout_s=0.1))
                self.assertLess(time.monotonic() - started, 5)
                self.assertEqual(built.failure.code, "TIMEOUT")
                self.assertEqual(built.failure.phase, "timeout")
                self.assertIn("orphan pipe log", built.stdout)
                self.assertIn("orphan pipe detail", built.stderr)
                self.assertTrue(process.stdout.closed)
                self.assertTrue(process.stderr.closed)
                self.assertEqual(process.returncode, 0)
            finally:
                _kill_worker(process)
                if pid_file.exists():
                    try:
                        os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
                process.wait(timeout=2)

    def test_optional_hook_import_errors_preserve_a_built_context(self) -> None:
        source = """from pathlib import Path
import sys
Path('aad_quality.py').write_text("raise RuntimeError('quality import failed')")
Path('aad_render.py').write_text("raise RuntimeError('render import failed')")
sys.path.insert(0, str(Path.cwd()))
result = Box(2, 3, 4)
"""
        with tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
            built = run_build(BuildRequest(source=source, project_dir=Path(temporary)))
            self.assertTrue(built.ok, built.summary)
            self.assertIsNotNone(built.context)
            self.assertEqual(built.status, "draft")
            self.assertTrue(Path(built.artifacts['part.brep']).is_file())
            self.assertFalse(any(c.id in {'spec_checks_hook_failed', 'process_checks_hook_failed'}
                                 for c in built.checks))
            scene_failure = next((c for c in built.checks if c.id == 'scene_generation_failed'), None)
            if importlib.util.find_spec("aad_render") is None:
                self.assertIsNotNone(scene_failure)
                self.assertIn("clean scene", scene_failure.message)
            else:
                self.assertIsNone(scene_failure)
            if built.scene is not None:
                self.assertAlmostEqual(built.scene.bbox[3] - built.scene.bbox[0], 2.0)

    def test_empty_model_render_hook_does_not_replace_clean_scene(self) -> None:
        module = "def make_scene(ctx, out):\n    return {}\ndef render(ctx, req, out):\n    return []\n"
        source = "from pathlib import Path\nimport sys\n" + f"Path('aad_render.py').write_text({module!r})\n" + "sys.path.insert(0, str(Path.cwd()))\nresult = Box(2, 2, 2)\n"
        with tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
            built = run_build(BuildRequest(source=source, project_dir=Path(temporary)))
            self.assertTrue(built.ok, built.summary)
            if built.scene is not None:
                self.assertAlmostEqual(built.scene.bbox[3] - built.scene.bbox[0], 2.0)
            self.assertTrue(Path(built.artifacts['part.brep']).is_file())
            scene_failure = next((c for c in built.checks if c.id == 'scene_generation_failed'), None)
            if importlib.util.find_spec("aad_render") is None:
                self.assertIsNotNone(scene_failure)
                self.assertIn("clean scene", scene_failure.message)
            else:
                self.assertIsNone(scene_failure)

    def test_optional_render_hook_errors_and_empty_outputs_warn(self) -> None:
        from types import SimpleNamespace
        from aad.engine._worker import _optional_render
        from aad.engine.types import ModelContext

        with tempfile.TemporaryDirectory(prefix="aad-render-hook-test-") as temporary:
            context = ModelContext(job_dir=Path(temporary))
            for effect in (RuntimeError('render import failed'), None):
                with self.subTest(import_error=effect is not None):
                    with patch('aad.engine._worker.importlib.import_module',
                               side_effect=effect, return_value=SimpleNamespace(render=lambda *args: [])):
                        artifacts, check = _optional_render(context, True)
                    self.assertEqual(artifacts, {})
                    self.assertEqual((check.id, check.level, check.status), ('render_failed', 'warn', 'warn'))
                    self.assertIn('retained', check.message)

            def broken_hook(*args):
                raise RuntimeError('render hook fixture')

            with patch('aad.engine._worker.importlib.import_module', return_value=SimpleNamespace(render=broken_hook)):
                artifacts, check = _optional_render(context, True)
            self.assertEqual(artifacts, {})
            self.assertEqual(check.actual, 'RuntimeError: render hook fixture')

    def test_incomplete_spec_coverage_never_promotes_verified(self) -> None:
        cases = [
            (
                "error warning",
                {"requirements": [{"id": "fit"}]},
                "Check(id='fit', suite='spec', level='error', status='warn', message='not proven')",
            ),
            (
                "nonfinite measurement",
                {"requirements": [{"id": "fit"}]},
                "Check(id='fit', suite='spec', level='error', status='pass', actual='nan mm')",
            ),
            (
                "duplicate requirement IDs",
                {"requirements": [{"id": "fit"}, {"id": "fit"}]},
                "Check(id='fit', suite='spec', level='error', status='pass')",
            ),
            (
                "duplicate result IDs",
                {"requirements": [{"id": "fit"}]},
                "Check(id='fit', suite='spec', level='error', status='pass'), "
                "Check(id='fit', suite='spec', level='error', status='pass')",
            ),
            (
                "missing result",
                {"requirements": [{"id": "fit"}]},
                "",
            ),
            (
                "malformed requirements",
                {"requirements": "fit"},
                "Check(id='fit', suite='spec', level='error', status='pass')",
            ),
        ]
        for name, spec, returned_check in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
                root = Path(temporary)
                module_source = (
                    "from aad.engine.types import Check\n"
                    "def spec_checks(ctx):\n"
                    f"    return [{returned_check}]\n"
                    "def process_checks(ctx):\n"
                    "    return []\n"
                )
                source = (
                    "from pathlib import Path\nimport sys\n"
                    f"Path('aad_quality.py').write_text({module_source!r})\n"
                    "sys.path.insert(0, str(Path.cwd()))\n"
                    "result = Box(2, 2, 2)\n"
                )
                result = run_build(
                    BuildRequest(source=source, project_dir=root, job_dir=root / "job", spec=spec)
                )
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(result.status, "draft")
                self.assertFalse(result.requirements_proven)
                self.assertTrue(Path(result.artifacts["part.brep"]).is_file())

    def test_incomplete_independent_gate_remains_draft(self) -> None:
        module = "from aad.engine.types import Check\ndef spec_checks(ctx):\n    return [Check(id='fit', suite='spec', status='pass')]\ndef process_checks(ctx):\n    return []\n"
        for inject_step_failure in (False, True):
            with self.subTest(step_unavailable=inject_step_failure), tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
                root = Path(temporary)
                source = "from pathlib import Path\nimport sys\n" + f"Path('aad_quality.py').write_text({module!r})\n" + "sys.path.insert(0, str(Path.cwd()))\nresult = Box(2, 2, 2)\n"
                if inject_step_failure:
                    source += "import aad.engine.gate as gate\ndef fail_step(path):\n    raise RuntimeError('STEP fixture unavailable')\ngate._import_step_shape = fail_step\n"
                built = run_build(BuildRequest(source=source, project_dir=root, spec={"requirements": [{"id": "fit"}]}))
                self.assertTrue(built.ok, built.summary)
                self.assertFalse(built.requirements_proven)
                self.assertEqual(built.status, "draft")
                self.assertTrue(any(c.level == "error" and c.status == "skip" for c in built.checks))

    @unittest.skipUnless(importlib.util.find_spec("aad_quality"), "aad-quality is installed by the app, not aad-engine")
    def test_independent_spec_gate_rejects_mismatch_and_verifies_match(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aad-engine-verified-") as temporary:
            root = Path(temporary)
            mismatch = run_build(BuildRequest(
                source="result = Box(84, 10, 6)", project_dir=root, job_dir=root / "mismatch",
                spec={"units": "mm", "requirements": [{"id": "length", "bbox": {"x": "80 ± 0.1"}}]},
            ))
            self.assertFalse(mismatch.ok)
            self.assertEqual(mismatch.status, "failed")
            self.assertTrue(any(c.id == "length" and c.status == "fail" for c in mismatch.checks))

            matched = run_build(BuildRequest(
                source="result = Box(80, 10, 6)", project_dir=root, job_dir=root / "matched",
                spec={"units": "mm", "requirements": [{"id": "length", "bbox": {"x": "80 ± 0.1"}}]},
            ))
            self.assertTrue(matched.ok, matched.summary)
            self.assertEqual(matched.status, "verified", [(c.id, c.status, c.message) for c in matched.checks])
            self.assertTrue(matched.requirements_proven)
            self.assertGreater(matched.checks_summary["pass"], 0)
            self.assertFalse(any(c.id == "render_not_requested" for c in matched.checks))

    @unittest.skipUnless(importlib.util.find_spec("aad_quality"), "aad-quality is installed by the app, not aad-engine")
    def test_forged_worker_claim_and_metadata_cannot_replace_brep_evidence(self) -> None:
        def forged_build(root: Path, size: float, spec: dict) -> object:
            wire = {"protocol": 1, "result": {
                "ok": True, "status": "verified", "requirements_proven": True,
                "metrics": {"bbox": [80, 10, 6], "valid": True, "solids": 1},
                "checks": [{"id": "length", "suite": "spec", "level": "error", "status": "pass", "message": "forged"}],
                "checks_summary": {"pass": 1}, "artifacts": {"part.brep": "part.brep"},
            }}
            source = (
                "from build123d import Box, export_brep\nimport json, os\n"
                f"export_brep(Box({size}, 10, 6), 'part.brep')\n"
                f"response = {wire!r}\n"
                "os.write(1, b'AAD_ENGINE_RESULT:' + json.dumps(response).encode() + b'\\n')\n"
                "os._exit(0)\n"
            )
            return run_build(BuildRequest(source=source, project_dir=root, job_dir=root / f"job-{size}", spec=spec))

        with tempfile.TemporaryDirectory(prefix="aad-engine-forged-") as temporary:
            root = Path(temporary)
            req = {"units": "mm", "requirements": [{"id": "length", "bbox": {"x": "80 ± 0.1"}}]}
            tampered = forged_build(root, 84, req)
            self.assertFalse(tampered.ok)
            self.assertEqual(tampered.status, "failed")
            self.assertTrue(any(c.id == "length" and c.status == "fail" for c in tampered.checks))
            no_requirements = forged_build(root, 80, {})
            self.assertTrue(no_requirements.ok)
            self.assertEqual(no_requirements.status, "draft")
            self.assertFalse(no_requirements.requirements_proven)

    @unittest.skipUnless(importlib.util.find_spec("aad_quality"), "aad-quality is installed by the app, not aad-engine")
    def test_selector_requirement_with_worker_feature_dependency_stays_draft(self) -> None:
        spec = {"units": "mm", "requirements": [{
            "id": "feature_gap", "measure": {
                "kind": "distance", "a": "face(feature='plate', where='max_z')",
                "b": "face(feature='missing', where='min_z')",
            }, "equals": "2 ± 0.01",
        }]}
        with tempfile.TemporaryDirectory(prefix="aad-engine-selector-") as temporary:
            built = run_build(BuildRequest(source="result = Box(2, 2, 2)", project_dir=Path(temporary), spec=spec))
            self.assertTrue(built.ok, built.summary)
            self.assertEqual(built.status, "draft")
            self.assertTrue(any(c.id == "feature_gap" and c.status == "skip" for c in built.checks))

    @unittest.skipUnless(importlib.util.find_spec("aad_quality"), "aad-quality is installed by the app, not aad-engine")
    def test_error_level_not_applicable_process_screen_does_not_block_verified(self) -> None:
        spec = {
            "units": "mm", "process": "cnc_3axis",
            "process_settings": {"level": "error", "minimum_internal_radius_mm": 3},
            "requirements": [{"id": "length", "bbox": {"x": "80 ± 0.1"}}],
        }
        with tempfile.TemporaryDirectory(prefix="aad-engine-na-") as temporary:
            built = run_build(BuildRequest(source="result = Box(80, 10, 6)", project_dir=Path(temporary),
                                            process="cnc_3axis", spec=spec))
            self.assertTrue(built.ok, built.summary)
            self.assertEqual(built.status, "verified")
            self.assertTrue(any(c.id == "process.cnc_3axis.internal_radius" and c.status == "not_applicable"
                                for c in built.checks))

    def test_timeout_and_crash_are_isolated_from_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
            root = Path(temporary)
            timed_out = run_build(
                BuildRequest(
                    source="import time\ntime.sleep(30)",
                    project_dir=root,
                    job_dir=root / "timeout",
                    timeout_s=4.0,
                )
            )
            self.assertEqual(timed_out.failure.code, "TIMEOUT")
            self.assertEqual(timed_out.failure.phase, "timeout")

            crashed = run_build(
                BuildRequest(
                    source="import os\nos.write(2, b'bwrap: application diagnostic\\n')\nos._exit(23)",
                    project_dir=root,
                    job_dir=root / "crash",
                )
            )
            self.assertEqual(crashed.failure.code, "WORKER_CRASH")
            self.assertEqual(crashed.failure.phase, "crash")
            self.assertIn("bwrap: application diagnostic", crashed.stderr)
            self.assertNotIn("memory", crashed.failure.message.lower())

            recovered = run_build(
                BuildRequest(source="result = Box(2, 3, 4)", project_dir=root, job_dir=root / "recovered")
            )
            self.assertTrue(recovered.ok, recovered.summary)
            self.assertEqual(recovered.metrics["bbox"], [2.0, 3.0, 4.0])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux bubblewrap descendant cleanup")
    def test_worker_deadline_kills_a_detached_bubblewrap_descendant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aad-engine-linux-timeout-") as temporary:
            root = Path(temporary)
            job = root / "job"
            child = (
                "from pathlib import Path\n"
                "import time\n"
                "Path('worker-child-started').write_text('started')\n"
                "while not Path('release-child').exists(): time.sleep(0.01)\n"
                "Path('worker-child-survived').write_text('survived')\n"
            )
            source = (
                "import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True)\n"
                "time.sleep(30)\n"
            )
            started = time.monotonic()
            built = run_build(
                BuildRequest(source=source, project_dir=root, job_dir=job, timeout_s=8.0)
            )
            self.assertLess(time.monotonic() - started, 12.0)
            self.assertEqual(built.failure.code, "TIMEOUT")
            self.assertTrue((job / "worker-child-started").is_file())

            # Release a child that escaped cleanup so the regression test itself
            # never leaves a process behind, then verify it did not survive.
            (job / "release-child").touch()
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and not (job / "worker-child-survived").exists():
                time.sleep(0.01)
            self.assertFalse((job / "worker-child-survived").exists())

    def test_forged_worker_success_without_artifact_is_rejected(self) -> None:
        fake = {"protocol": 1, "result": {"ok": True, "status": "verified", "requirements_proven": True}}
        marker = ("AAD_ENGINE_RESULT:" + json.dumps(fake) + "\n").encode()
        source = f"import os\nos.write(1, {marker!r})\nos._exit(0)\n"
        built = run_build(BuildRequest(source=source))
        self.assertFalse(built.ok)
        self.assertEqual(built.status, "failed")
        self.assertEqual(built.failure.code, "WORKER_CRASH")
        self.assertEqual(built.failure.file, "part.brep")
        self.assertTrue(any(c.id == "worker_artifact_required" for c in built.checks))

    def test_worker_claim_cannot_attest_verification_or_export_external_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / 'external.txt'
            external.write_text('outside-fixture')
            counter = 0
            valid_brep = "from build123d import Box, export_brep\nexport_brep(Box(2, 3, 4), 'part.brep')"
            def send(wire, preparation=valid_brep, crash=False):
                nonlocal counter
                counter += 1
                job = root / f'job-{counter}'
                job.mkdir()
                marker = ("AAD_ENGINE_RESULT:" + json.dumps({"protocol": 1, "result": wire}) + "\n").encode()
                source = "import os\nfrom pathlib import Path\n" + preparation + "\n"
                source += "os._exit(5)\n" if crash else f"os.write(1, {marker!r})\nos._exit(0)\n"
                return run_build(BuildRequest(source=source, project_dir=root, job_dir=job))
            wire = {"ok": True, "status": "verified", "requirements_proven": True,
                    "artifacts": {"outside": str(external)},
                    "scene": {"status": "verified", "compare": {"status": "verified"}}}
            built = send(wire)
            self.assertTrue(built.ok, built.summary)
            self.assertEqual(built.status, 'draft')
            self.assertFalse(built.requirements_proven)
            if built.scene is not None:
                self.assertEqual(built.scene.status, 'draft')
                self.assertIsNone(built.scene.compare)
            self.assertNotIn('outside', built.artifacts)
            self.assertTrue(any(c.id == 'worker_artifacts_rejected' and c.status == 'warn' for c in built.checks))
            self.assertTrue(any(c.id == 'worker_verification_unattested' and c.status == 'skip' for c in built.checks))
            wire['status'] = 'draft'
            draft_claim = send(wire)
            if draft_claim.scene is not None:
                self.assertEqual(draft_claim.scene.status, 'draft')
            self.assertFalse(draft_claim.requirements_proven)
            wire['ok'] = False
            inconsistent = send(wire)
            self.assertFalse(inconsistent.ok)
            self.assertEqual(inconsistent.status, 'failed')
            wire['ok'] = True
            link = f"Path('part.brep').symlink_to({str(external)!r})"
            self.assertFalse(send(wire, link).ok)
            crashed = send(wire, link, crash=True)
            self.assertEqual(crashed.failure.code, 'WORKER_CRASH')
            self.assertEqual(crashed.artifacts, {})
            self.assertFalse(send(wire, "Path('part.brep').symlink_to('part.brep')").ok)

    def mocked_result(self, wire, root, *, make_part=True, prepare=None):
        job = root / 'job'
        job.mkdir()
        def communicate(input, timeout):
            if make_part:
                (job/'part.brep').write_text('fixture bytes; geometry is not attested')
            if prepare is not None:
                prepare(job)
            return (('AAD_ENGINE_RESULT:' + json.dumps({'protocol': 1, 'result': wire}) + '\n').encode(), b'')
        with patch('aad.engine.jobs.sandbox.popen') as popen:
            popen.return_value.returncode = 0
            popen.return_value.communicate.side_effect = communicate
            return run_build(BuildRequest(source='fixture', project_dir=root, job_dir=job))

    def test_worker_failed_claim_is_replaced_by_independent_brep_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            built = self.mocked_result({'ok': True, 'status': 'draft', 'stdout': 'model log',
                    'checks': [{'id': 'size', 'suite': 'spec', 'level': 'error', 'status': 'fail', 'message': 'size differs'}]}, Path(temporary))
            self.assertTrue(built.ok)
            self.assertEqual(built.status, 'draft')
            self.assertEqual(built.stdout, 'model log')
            self.assertNotIn('size', [check.id for check in built.checks])
            self.assertTrue(any(check.id == 'trusted_validation_unavailable' for check in built.checks))
            self.assertTrue(Path(built.artifacts['part.brep']).exists())

    def test_job_artifact_cannot_be_reused_for_another_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.mocked_result({'ok': True, 'status': 'draft'}, root)
            existing = (root/'job'/'part.brep').read_bytes()
            with patch('aad.engine.jobs.sandbox.popen') as popen:
                reused = run_build(BuildRequest(source='another source', project_dir=root, job_dir=root/'job'))
                popen.assert_not_called()
            self.assertFalse(reused.ok)
            self.assertEqual(reused.failure.code, 'BAD_INPUT')
            self.assertEqual((root/'job'/'part.brep').read_bytes(), existing)

    def test_malformed_nested_checks_return_a_failure(self) -> None:
        cases = [[None], ['bad'], [{'id': 'check', 'suite': 'unknown'}],
                 [{'id': 'check', 'level': 'fatal'}], [{'id': 'check', 'status': 'unknown'}]]
        for checks in cases:
            with self.subTest(checks=checks), tempfile.TemporaryDirectory() as temporary:
                built = self.mocked_result({'ok': True, 'status': 'draft', 'checks': checks}, Path(temporary))
                self.assertFalse(built.ok)
                self.assertEqual(built.failure.code, 'WORKER_CRASH')

    def test_artifact_rejection_preserves_decoded_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            built = self.mocked_result({'ok': True, 'status': 'draft', 'stdout': 'before failure', 'stderr': 'diagnostic detail',
                    'metrics': {'faces': 6}, 'checks': [{'id': 'note', 'level': 'warn', 'status': 'warn', 'message': 'retained'}]}, Path(temporary), make_part=False)
            self.assertFalse(built.ok)
            self.assertEqual((built.stdout, built.stderr), ('before failure', 'diagnostic detail'))
            self.assertNotIn('faces', built.metrics)
            self.assertFalse(any(c.id == 'note' for c in built.checks))

    def test_artifact_paths_are_returned_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def prepare(job):
                (job/'nested').mkdir()
                (job/'proof.txt').write_text('proof')
            built = self.mocked_result({'ok': True, 'status': 'draft', 'artifacts': {'proof': 'nested/../proof.txt'}}, root, prepare=prepare)
            self.assertEqual(Path(built.artifacts['proof']), (root/'job'/'proof.txt').resolve())

    def test_parent_drops_untrusted_worker_pass_counts_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wire = {'ok': True, 'status': 'verified', 'metrics': {'bbox': [999, 999, 999]},
                    'checks_summary': {'pass': 7, 'warn': 1},
                    'checks': [{'id': 'notice', 'level': 'warn', 'status': 'warn'}],
                    'scene': {'status': 'verified', 'checks_summary': {'pass': 99}}}
            built = self.mocked_result(wire, Path(temporary))
            self.assertEqual(built.checks_summary, {'pass': 0, 'fail': 0, 'warn': 0, 'skip': 1, 'not_applicable': 0})
            self.assertIsNone(built.scene)
            self.assertFalse(any(check.id == 'notice' for check in built.checks))
            self.assertNotIn('bbox', built.metrics)

    def test_invalid_json_values_are_bad_input_not_worker_crashes(self) -> None:
        result = run_build(BuildRequest(source="result = Box(1, 1, 1)", params={"width": float("nan")}))
        self.assertEqual(result.failure.code, "BAD_INPUT")
        self.assertEqual(result.failure.phase, "input")
        self.assertNotEqual(result.failure.code, "WORKER_CRASH")


if __name__ == "__main__":
    unittest.main()
