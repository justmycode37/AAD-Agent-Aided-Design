from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aiad.engine.jobs import run_build
from aiad.engine.types import BuildRequest

ROOT = Path(__file__).resolve().parents[3]


class EngineBootstrapTests(unittest.TestCase):
    def test_types_import_does_not_import_kernel(self) -> None:
        code = (
            "import sys; from typing import get_type_hints; "
            "import aiad.engine.types as engine_types; "
            "get_type_hints(engine_types.ModelContext); "
            "assert 'build123d' not in sys.modules; "
            "assert 'OCP' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", code], check=True, timeout=10)

    def test_box_script_returns_metrics_and_loadable_brep(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiad-engine-test-") as temporary:
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


    def test_buildpart_fixture_produces_implicit_main_feature(self) -> None:
        source = (ROOT / "fixtures" / "models" / "fan_mount.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="aiad-engine-test-") as temporary:
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
            self.assertTrue(any(check.id == "process_checks_unavailable" for check in result.checks))

    def test_syntax_and_runtime_failures_report_source_location(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiad-engine-test-") as temporary:
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

    def test_optional_hook_failures_warn_without_discarding_brep(self) -> None:
        source = """from pathlib import Path
import sys
Path("aiad_quality.py").write_text(
    "def spec_checks(ctx):\\n    raise RuntimeError('quality hook fixture')\\n"
)
Path("aiad_render.py").write_text(
    "def make_scene(ctx, out):\\n    raise RuntimeError('scene hook fixture')\\n"
    "def render(ctx, req, out):\\n    raise RuntimeError('render hook fixture')\\n"
)
sys.path.insert(0, str(Path.cwd()))
result = Box(4, 5, 6)
"""
        with tempfile.TemporaryDirectory(prefix="aiad-engine-test-") as temporary:
            root = Path(temporary)
            result = run_build(
                BuildRequest(
                    source=source,
                    project_dir=root,
                    job_dir=root / "job",
                    spec={"requirements": [{"id": "fit"}]},
                    preview=True,
                )
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(result.status, "draft")
            self.assertTrue(Path(result.artifacts["part.brep"]).is_file())
            warnings = {check.id: check for check in result.checks if check.status == "warn"}
            self.assertIn("spec_checks_hook_failed", warnings)
            self.assertIn("scene_generation_failed", warnings)
            self.assertIn("render_failed", warnings)
            self.assertIn("retained", warnings["render_failed"].message)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group cleanup")
    def test_cleanup_kills_descendants_after_the_worker_leader_exits(self) -> None:
        from aiad.engine.jobs import _kill_worker
        program = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])"
        process = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        try:
            process.wait(timeout=3)
            _kill_worker(process)
            process.communicate(timeout=2)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=2)

    def test_optional_hook_import_errors_preserve_a_built_context(self) -> None:
        source = """from pathlib import Path
import sys
Path('aiad_quality.py').write_text("raise RuntimeError('quality import failed')")
Path('aiad_render.py').write_text("raise RuntimeError('render import failed')")
sys.path.insert(0, str(Path.cwd()))
result = Box(2, 3, 4)
"""
        with tempfile.TemporaryDirectory(prefix="aiad-engine-test-") as temporary:
            built = run_build(BuildRequest(source=source, project_dir=Path(temporary), preview=True))
            self.assertTrue(built.ok, built.summary)
            self.assertIsNotNone(built.context)
            self.assertEqual(built.status, "draft")
            self.assertTrue(Path(built.artifacts['part.brep']).is_file())
            warnings = {c.id for c in built.checks if c.status == "warn"}
            self.assertTrue({'spec_checks_hook_failed', 'process_checks_hook_failed',
                             'scene_generation_failed', 'render_failed'} <= warnings)

    def test_empty_optional_outputs_warn_instead_of_reporting_success(self) -> None:
        module = "def make_scene(ctx, out):\n    return {}\ndef render(ctx, req, out):\n    return []\n"
        source = "from pathlib import Path\nimport sys\n" + f"Path('aiad_render.py').write_text({module!r})\n" + "sys.path.insert(0, str(Path.cwd()))\nresult = Box(2, 2, 2)\n"
        with tempfile.TemporaryDirectory(prefix="aiad-engine-test-") as temporary:
            built = run_build(BuildRequest(source=source, project_dir=Path(temporary), preview=True))
            self.assertTrue(built.ok, built.summary)
            self.assertIsNone(built.scene)
            self.assertTrue(Path(built.artifacts['part.brep']).is_file())
            checks = {c.id: c.status for c in built.checks}
            self.assertEqual(checks['scene_generation_failed'], 'warn')
            self.assertEqual(checks['render_failed'], 'warn')

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
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix="aiad-engine-test-") as temporary:
                root = Path(temporary)
                module_source = (
                    "from aiad.engine.types import Check\n"
                    "def spec_checks(ctx):\n"
                    f"    return [{returned_check}]\n"
                    "def process_checks(ctx):\n"
                    "    return []\n"
                )
                source = (
                    "from pathlib import Path\nimport sys\n"
                    f"Path('aiad_quality.py').write_text({module_source!r})\n"
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

    def test_partial_hard_gate_cannot_verify_even_with_passing_spec(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiad-engine-test-") as temporary:
            root = Path(temporary)
            module = "from aiad.engine.types import Check\ndef spec_checks(ctx):\n    return [Check(id='fit', suite='spec', status='pass')]\ndef process_checks(ctx):\n    return []\n"
            source = "from pathlib import Path\nimport sys\n" + f"Path('aiad_quality.py').write_text({module!r})\n" + "sys.path.insert(0, str(Path.cwd()))\nresult = Box(2, 2, 2)\n"
            built = run_build(BuildRequest(source=source, project_dir=root, spec={"requirements": [{"id": "fit"}]}))
            self.assertTrue(built.ok, built.summary)
            self.assertFalse(built.requirements_proven)
            self.assertEqual(built.status, "draft")
            self.assertTrue(any(c.id == "hard_gate_incomplete" and c.status == "skip" for c in built.checks))

    def test_timeout_and_crash_are_isolated_from_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aiad-engine-test-") as temporary:
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
                    source="import os\nos._exit(23)",
                    project_dir=root,
                    job_dir=root / "crash",
                )
            )
            self.assertEqual(crashed.failure.code, "WORKER_CRASH")
            self.assertEqual(crashed.failure.phase, "crash")
            self.assertNotIn("memory", crashed.failure.message.lower())

            recovered = run_build(
                BuildRequest(source="result = Box(2, 3, 4)", project_dir=root, job_dir=root / "recovered")
            )
            self.assertTrue(recovered.ok, recovered.summary)
            self.assertEqual(recovered.metrics["bbox"], [2.0, 3.0, 4.0])

    def test_forged_worker_success_without_artifact_is_rejected(self) -> None:
        fake = {"protocol": 1, "result": {"ok": True, "status": "verified", "requirements_proven": True}}
        marker = ("AIAD_ENGINE_RESULT:" + json.dumps(fake) + "\n").encode()
        source = f"import os\nos.write(1, {marker!r})\nos._exit(0)\n"
        built = run_build(BuildRequest(source=source))
        self.assertFalse(built.ok)
        self.assertEqual(built.status, "failed")
        self.assertEqual(built.failure.code, "WORKER_CRASH")

    def test_worker_claim_cannot_attest_verification_or_export_external_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = root / 'job'
            job.mkdir()
            external = root / 'external.txt'
            external.write_text('outside-fixture')
            fake = {"protocol": 1, "result": {"ok": True, "status": "verified", "requirements_proven": True,
                    "artifacts": {"outside": str(external)},
                    "scene": {"status": "verified"}}}
            marker = ("AIAD_ENGINE_RESULT:" + json.dumps(fake) + "\n").encode()
            source = "import os\nfrom pathlib import Path\nPath('part.brep').write_text('not independently validated')\n" + f"os.write(1, {marker!r})\nos._exit(0)\n"
            built = run_build(BuildRequest(source=source, project_dir=root, job_dir=job))
            self.assertEqual(built.status, 'draft')
            self.assertFalse(built.requirements_proven)
            self.assertEqual(built.scene.status, 'draft')
            self.assertNotIn('outside', built.artifacts)
            self.assertTrue(any(c.id == 'worker_artifacts_rejected' and c.status == 'warn' for c in built.checks))
            self.assertTrue(any(c.id == 'worker_verification_unattested' and c.status == 'skip' for c in built.checks))
            fake['result']['status'] = 'draft'
            marker = ("AIAD_ENGINE_RESULT:" + json.dumps(fake) + "\n").encode()
            draft_claim = run_build(BuildRequest(source=f"import os\nos.write(1, {marker!r})\nos._exit(0)\n", project_dir=root, job_dir=job))
            self.assertEqual(draft_claim.scene.status, 'draft')
            self.assertFalse(draft_claim.requirements_proven)
            fake['result']['ok'] = False
            marker = ("AIAD_ENGINE_RESULT:" + json.dumps(fake) + "\n").encode()
            inconsistent = run_build(BuildRequest(source=f"import os\nos.write(1, {marker!r})\nos._exit(0)\n", project_dir=root, job_dir=job))
            self.assertFalse(inconsistent.ok)
            self.assertEqual(inconsistent.status, 'failed')
            fake['result']['ok'] = True
            marker = ("AIAD_ENGINE_RESULT:" + json.dumps(fake) + "\n").encode()
            (job/'part.brep').unlink()
            (job/'part.brep').symlink_to(external)
            linked = run_build(BuildRequest(source=f"import os\nos.write(1, {marker!r})\nos._exit(0)\n", project_dir=root, job_dir=job))
            self.assertFalse(linked.ok)
            crashed = run_build(BuildRequest(source="import os\nos._exit(5)\n", project_dir=root, job_dir=job))
            self.assertEqual(crashed.failure.code, 'WORKER_CRASH')
            self.assertEqual(crashed.artifacts, {})
            (job/'part.brep').unlink()
            (job/'part.brep').symlink_to('part.brep')
            looped = run_build(BuildRequest(source=f"import os\nos.write(1, {marker!r})\nos._exit(0)\n", project_dir=root, job_dir=job))
            self.assertFalse(looped.ok)

    def test_invalid_json_values_are_bad_input_not_worker_crashes(self) -> None:
        result = run_build(BuildRequest(source="result = Box(1, 1, 1)", params={"width": float("nan")}))
        self.assertEqual(result.failure.code, "BAD_INPUT")
        self.assertEqual(result.failure.phase, "input")
        self.assertNotEqual(result.failure.code, "WORKER_CRASH")


if __name__ == "__main__":
    unittest.main()
