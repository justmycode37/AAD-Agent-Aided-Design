from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest

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
            self.assertTrue(any(check.suite == "process" for check in result.checks))

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
            warnings = {check.id: check for check in result.checks if check.status == "warn"}
            self.assertIn("spec_checks_hook_failed", warnings)
            self.assertIn("scene_generation_failed", warnings)

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
            warnings = {c.id for c in built.checks if c.status == "warn"}
            self.assertTrue({'spec_checks_hook_failed', 'process_checks_hook_failed',
                             'scene_generation_failed'} <= warnings)

    def test_empty_optional_outputs_warn_instead_of_reporting_success(self) -> None:
        module = "def make_scene(ctx, out):\n    return {}\ndef render(ctx, req, out):\n    return []\n"
        source = "from pathlib import Path\nimport sys\n" + f"Path('aad_render.py').write_text({module!r})\n" + "sys.path.insert(0, str(Path.cwd()))\nresult = Box(2, 2, 2)\n"
        with tempfile.TemporaryDirectory(prefix="aad-engine-test-") as temporary:
            built = run_build(BuildRequest(source=source, project_dir=Path(temporary)))
            self.assertTrue(built.ok, built.summary)
            self.assertIsNone(built.scene)
            self.assertTrue(Path(built.artifacts['part.brep']).is_file())
            checks = {c.id: c.status for c in built.checks}
            self.assertEqual(checks['scene_generation_failed'], 'warn')
            self.assertEqual(checks['render_not_requested'], 'skip')

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

    def test_complete_gate_remains_draft_until_independent_validation(self) -> None:
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
                hard = {c.id: c for c in built.checks if c.suite == "hard" and c.level == "error" and c.id != "worker_verification_unattested"}
                self.assertNotIn("hard_gate_incomplete", hard)
                if inject_step_failure:
                    self.assertEqual(hard["step_volume_drift"].status, "skip")
                    self.assertIn("STEP fixture unavailable", hard["step_volume_drift"].actual)
                else:
                    self.assertTrue(all(check.status == "pass" for check in hard.values()))
                    self.assertTrue(any(c.id == "worker_verification_unattested" and c.status == "skip" for c in built.checks))

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
            def send(wire, preparation="Path('part.brep').write_text('not independently validated')", crash=False):
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
            self.assertEqual(built.scene.status, 'draft')
            self.assertIsNone(built.scene.compare)
            self.assertNotIn('outside', built.artifacts)
            self.assertTrue(any(c.id == 'worker_artifacts_rejected' and c.status == 'warn' for c in built.checks))
            self.assertTrue(any(c.id == 'worker_verification_unattested' and c.status == 'skip' for c in built.checks))
            wire['status'] = 'draft'
            draft_claim = send(wire)
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

    def test_success_with_failed_error_check_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            built = self.mocked_result({'ok': True, 'status': 'draft', 'stdout': 'model log',
                    'checks': [{'id': 'size', 'suite': 'spec', 'level': 'error', 'status': 'fail', 'message': 'size differs'}]}, Path(temporary))
            self.assertFalse(built.ok)
            self.assertEqual(built.status, 'failed')
            self.assertEqual(built.failure.code, 'NOT_VERIFIED')
            self.assertEqual(built.stdout, 'model log')
            self.assertEqual(built.checks[0].id, 'size')
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
            self.assertEqual(built.metrics['faces'], 6)
            self.assertTrue(any(c.id == 'note' for c in built.checks))

    def test_artifact_paths_are_returned_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def prepare(job):
                (job/'nested').mkdir()
                (job/'proof.txt').write_text('proof')
            built = self.mocked_result({'ok': True, 'status': 'draft', 'artifacts': {'proof': 'nested/../proof.txt'}}, root, prepare=prepare)
            self.assertEqual(Path(built.artifacts['proof']), (root/'job'/'proof.txt').resolve())

    def test_aggregate_counts_and_scene_summary_survive_parent_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wire = {'ok': True, 'status': 'verified', 'checks_summary': {'pass': 7, 'warn': 1},
                    'checks': [{'id': 'notice', 'level': 'warn', 'status': 'warn'}],
                    'scene': {'status': 'verified', 'checks_summary': {'pass': 99}}}
            built = self.mocked_result(wire, Path(temporary))
            self.assertEqual(built.checks_summary, {'pass': 7, 'fail': 0, 'warn': 1, 'skip': 1})
            self.assertEqual(built.scene.checks_summary, built.checks_summary)
            self.assertEqual(built.scene.status, 'draft')

    def test_invalid_json_values_are_bad_input_not_worker_crashes(self) -> None:
        result = run_build(BuildRequest(source="result = Box(1, 1, 1)", params={"width": float("nan")}))
        self.assertEqual(result.failure.code, "BAD_INPUT")
        self.assertEqual(result.failure.phase, "input")
        self.assertNotEqual(result.failure.code, "WORKER_CRASH")


if __name__ == "__main__":
    unittest.main()
