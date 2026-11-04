from __future__ import annotations

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

            skipped_ids = {check.id for check in result.checks if check.status == "skip"}
            self.assertIn("spec_checks_unavailable", skipped_ids)
            self.assertIn("scene_generation_unavailable", skipped_ids)

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

    def test_invalid_json_values_are_bad_input_not_worker_crashes(self) -> None:
        result = run_build(BuildRequest(source="result = Box(1, 1, 1)", params={"width": float("nan")}))
        self.assertEqual(result.failure.code, "BAD_INPUT")
        self.assertEqual(result.failure.phase, "input")
        self.assertNotEqual(result.failure.code, "WORKER_CRASH")


if __name__ == "__main__":
    unittest.main()
