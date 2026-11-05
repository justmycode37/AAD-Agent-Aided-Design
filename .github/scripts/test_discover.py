"""CI must keep integration coverage while avoiding unrelated macOS jobs."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location("ci_discover", Path(__file__).with_name("discover.py"))
discover = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(discover)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for package in ("engine", "render", "drafting", "platform", "future"):
            pkg = self.root / "packages" / package
            (pkg / "tests").mkdir(parents=True)
            (pkg / "tests" / "test_example.py").touch()
            (pkg / "pyproject.toml").write_text(f'[project]\nname = "aad-{package}"\n')
        (self.root / "tests/e2e").mkdir(parents=True)
        (self.root / "tests/e2e/test_smoke.py").touch()
        (self.root / "viewer").mkdir()
        (self.root / "viewer/package.json").write_text('{}')

    def outputs(self, changed):
        captured = io.StringIO()
        with patch.object(discover, "__file__", str(self.root / ".github/scripts/discover.py")), \
             patch.object(discover, "changed_files", return_value=changed), \
             patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(captured):
            self.assertEqual(discover.main(), 0)
        return dict(line.split("=", 1) for line in captured.getvalue().splitlines())

    def assert_selection(self, changed, packages, viewer=False):
        result = self.outputs(changed)
        self.assertEqual({p["dir"] for p in json.loads(result["packages"])}, set(packages))
        self.assertEqual(result["e2e"], "true", "Every PR must keep the integration smoke test")
        self.assertEqual(result["viewer"], str(viewer).lower())

    def test_docs_and_empty_diff_keep_smoke(self):
        for changed in ([], ["README.md", "docs/host-matrix.md", "ARCHITECTURE.md", "CONTRACTS.md"]):
            with self.subTest(changed=changed):
                self.assert_selection(changed, [])

    def test_missing_smoke_tests_fail_discovery(self):
        (self.root / "tests/e2e/test_smoke.py").unlink()
        captured = io.StringIO()
        with patch.object(discover, "__file__", str(self.root / ".github/scripts/discover.py")), \
             patch.object(discover, "changed_files", return_value=["README.md"]), \
             contextlib.redirect_stderr(captured):
            self.assertEqual(discover.main(), 1)
        self.assertIn("Required smoke tests are missing", captured.getvalue())

    def test_platform_is_a_leaf(self):
        self.assert_selection(["packages/platform/src/aad_platform/cli.py"], ["platform"])

    def test_viewer_keeps_its_build_and_smoke(self):
        self.assert_selection(["viewer/src/main.ts"], [], viewer=True)

    def test_platform_and_viewer_both_run(self):
        self.assert_selection(["viewer/src/main.ts", "packages/platform/README.md"], ["platform"], viewer=True)

    def test_shared_cad_and_future_packages_run_all(self):
        for package in ("engine", "render", "drafting", "future"):
            with self.subTest(package=package):
                self.assert_selection([f"packages/{package}/src/code.py"],
                                      ["engine", "render", "drafting", "platform", "future"], viewer=True)

    def test_main_unknown_paths_and_packaged_markdown_run_all(self):
        for changed in (None, ["uv.lock"], [".github/workflows/ci.yml"],
                        ["skills/playbook.md"], ["packages/render/preset.md"]):
            with self.subTest(changed=changed):
                self.assert_selection(changed, ["engine", "render", "drafting", "platform", "future"], viewer=True)

    def test_no_test_directory_is_not_a_matrix_leg(self):
        (self.root / "packages/future/tests/test_example.py").unlink()
        self.assert_selection(None, ["engine", "render", "drafting", "platform"], viewer=True)

    def test_failed_git_diff_falls_back_to_full_coverage(self):
        failure = subprocess.CompletedProcess([], 128, stdout="", stderr="unknown base")
        with patch.dict(os.environ, {"GITHUB_BASE_REF": "main"}, clear=True), \
             patch.object(discover.subprocess, "run", return_value=failure), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(discover.changed_files(self.root))

    def test_real_git_diff_keeps_both_sides_of_rename(self):
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True)
        git("init", "-b", "main")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "CI test")
        source = self.root / "packages/engine/README.md"
        source.write_text("packaged runtime data\n")
        git("add", ".")
        git("commit", "-m", "base")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        source.rename(self.root / "docs-renamed.md")
        git("add", "-A")
        git("commit", "-m", "move")
        with patch.dict(os.environ, {"GITHUB_BASE_REF": "main"}, clear=True):
            self.assertEqual(set(discover.changed_files(self.root)),
                             {"packages/engine/README.md", "docs-renamed.md"})


if __name__ == "__main__":
    unittest.main()
