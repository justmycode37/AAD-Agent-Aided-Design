from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aad.engine import memory, sandbox
from aad.engine.jobs import _reference_read_paths, run_build
from aad.engine.types import BuildRequest


class SandboxPolicyTests(unittest.TestCase):
    def test_default_popen_preserves_command_environment_and_process_options(self) -> None:
        from aad.engine import memory

        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary).resolve()
            args = [sys.executable, '-I', '-B', '-c', 'pass']
            expected = sandbox.command(args, job)
            env = sandbox.environment(job)
            with patch.object(sandbox.subprocess, 'Popen') as launch, \
                    patch.object(memory, '_bootstrap_argv', side_effect=AssertionError('unexpected wrapper')), \
                    patch.object(memory, '_address_resource', side_effect=AssertionError('unexpected resource API')), \
                    patch.object(memory, '_mac_taskinfo', side_effect=AssertionError('unexpected libproc load')):
                for options in ({}, {'address_space': None}):
                    sandbox.popen(args, job, text=True, **options)
                    launch.assert_called_with(
                        expected, cwd=job, env=env, close_fds=True,
                        start_new_session=True, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                self.assertEqual(launch.call_count, 2)

    def test_configured_hook_rejects_unknown_policies_and_invalid_original_argv_before_launch(self) -> None:
        from aad.engine.memory import AddressSpaceBudget

        class ResidentBudget:
            bytes = 1024

        with tempfile.TemporaryDirectory() as temporary, patch.object(sandbox.subprocess, 'Popen') as launch:
            job = Path(temporary)
            for policy in (True, 1024, object(), ResidentBudget()):
                with self.subTest(policy=type(policy).__name__), self.assertRaises(TypeError):
                    sandbox.popen([sys.executable], job, address_space=policy)
            for args in ([], [sys.executable, None], [sys.executable, 'bad\0arg']):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    sandbox.popen(args, job, address_space=AddressSpaceBudget(1024))
            launch.assert_not_called()

    def test_environment_does_not_inherit_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"AAD_TEST_SECRET": "private-fixture"}):
            env = sandbox.environment(Path(temporary))
            self.assertNotIn("AAD_TEST_SECRET", env)
            self.assertEqual(Path(env["HOME"]), Path(temporary).resolve())
            self.assertTrue(Path(env["TMPDIR"]).is_dir())

    def test_missing_boundary_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(sandbox.shutil, "which", return_value=None):
            with self.assertRaises(sandbox.SandboxUnavailable):
                sandbox.command([sys.executable, "-c", "print('unsafe')"], Path(temporary))

    def test_runtime_reads_include_overlay_packages_without_arbitrary_search_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packages = root / "overlay" / "lib" / "python3.12" / "site-packages"
            arbitrary = root / "unrelated"
            packages.mkdir(parents=True)
            arbitrary.mkdir()
            with patch.object(sandbox.sys, "path", [str(packages), str(arbitrary), ""]):
                reads = sandbox.runtime_reads()
            self.assertIn(packages.resolve(), reads)
            self.assertNotIn(arbitrary.resolve(), reads)
            self.assertNotIn(root.resolve(), reads)

    def test_unimplemented_windows_boundary_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(sandbox.sys, "platform", "win32"):
            with self.assertRaises(sandbox.SandboxUnavailable):
                sandbox.command([sys.executable], Path(temporary))

    def test_linux_read_mounts_preserve_asset_alias_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            job = root / "job"
            job.mkdir()
            target = root / "actual.step"
            target.write_text("asset fixture")
            alias = root / "asset.step"
            alias.symlink_to(target)
            with patch.object(sandbox.sys, "platform", "linux"), patch.object(sandbox.shutil, "which", return_value="/usr/bin/bwrap"):
                argv = sandbox.command([sys.executable, "-c", "pass"], job, read_paths=(alias,))
            mounts = {argv[i + 2] for i, arg in enumerate(argv) if arg == "--ro-bind"}
            self.assertIn(str(alias), mounts)
            self.assertIn(str(target), mounts)

    def test_read_alias_to_filesystem_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alias = root / "root-alias"
            alias.symlink_to("/", target_is_directory=True)
            with self.assertRaises(ValueError):
                sandbox.command([sys.executable], root, read_paths=(alias,))


class PublicBuildSandboxFailureTests(unittest.TestCase):
    def test_build_grants_only_safe_declared_reference_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            assets = root / "assets"
            assets.mkdir()
            reference = assets / "reference.step"
            reference.write_text("mesh data")
            outside = root / "outside-reference.step"
            outside.write_text("mesh data")
            (assets / "escape.step").symlink_to(outside)
            spec = {
                "requirements": [
                    {"id": "safe", "matches_reference": {"path": "assets/reference.step", "f_score": {"min": 0.99}}},
                    {"id": "traversal", "matches_reference": {"path": "../outside-reference.step", "f_score": {"min": 0.99}}},
                    {"id": "symlink", "matches_reference": {"path": "assets/escape.step", "f_score": {"min": 0.99}}},
                ]
            }
            self.assertEqual(_reference_read_paths(root, spec), (reference,))

    def test_public_build_passes_only_valid_reference_to_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            reference = root / "reference.obj"
            reference.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
            job = root / "job"
            job.mkdir()
            request = BuildRequest(
                source="result = Box(1, 1, 1)",
                project_dir=root,
                job_dir=job,
                spec={"requirements": [{"id": "ref", "matches_reference": {"path": "reference.obj", "f_score": {"min": 0.99}}}]},
            )
            with patch.object(sandbox, "popen", side_effect=sandbox.SandboxUnavailable("test boundary")) as launch:
                result = run_build(request)
            self.assertEqual(result.failure.code, "SANDBOX_DENIED")
            self.assertEqual(launch.call_args.kwargs["read_paths"], (reference,))
            self.assertEqual(launch.call_args.kwargs["address_space"].bytes,
                             memory.bootstrap_address_space_budget(memory.default_memory_budget_bytes()).bytes)

    def test_missing_or_unsupported_boundary_returns_failure_without_popen(self) -> None:
        cases = [
            ("darwin", "macOS sandbox-exec is unavailable"),
            ("linux", "Linux bubblewrap (bwrap) is unavailable"),
            ("win32", "No supported filesystem/network sandbox is implemented for win32"),
        ]
        for platform, reason in cases:
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                with patch.object(sandbox.sys, "platform", platform), \
                     patch.object(sandbox.shutil, "which", return_value=None), \
                     patch.object(sandbox.subprocess, "Popen") as popen:
                    built = run_build(BuildRequest(source="result = Box(2, 3, 4)",
                                                   project_dir=Path(temporary)))
                    popen.assert_not_called()
                self.assertFalse(built.ok)
                self.assertEqual(built.status, "failed")
                self.assertEqual(built.failure.code, "SANDBOX_DENIED")
                self.assertEqual(built.failure.phase, "sandbox")
                self.assertIn(reason, built.failure.message)
                self.assertIn(reason, built.failure.kernel_message)
                self.assertEqual(built.artifacts, {})

    def test_os_launch_error_returns_observed_reason_without_fallback(self) -> None:
        error = PermissionError(1, "fixture sandbox launch rejected")
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(sandbox, "popen", side_effect=error) as launcher, \
             patch.object(subprocess, "Popen") as direct_popen:
            built = run_build(BuildRequest(source="result = Box(2, 3, 4)", project_dir=Path(temporary)))
            launcher.assert_called_once()
            direct_popen.assert_not_called()
        self.assertEqual(built.failure.code, "SANDBOX_DENIED")
        self.assertEqual(built.failure.phase, "sandbox")
        self.assertEqual(built.failure.kernel_message, f"PermissionError: {error}")

    def test_native_launcher_diagnostics_are_retained_without_inferring_a_cause(self) -> None:
        diagnostics = [
            b"sandbox-exec: sandbox_apply: Operation not permitted\n",
            b"bwrap: Creating new namespace failed: Operation not permitted\n",
        ]
        for diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic), tempfile.TemporaryDirectory() as temporary, \
                 patch.object(sandbox, "popen") as launcher:
                launcher.return_value.returncode = 1
                launcher.return_value.communicate.return_value = (b"launch log\n", diagnostic)
                built = run_build(BuildRequest(source="fixture", project_dir=Path(temporary)))
                launcher.assert_called_once()
            self.assertFalse(built.ok)
            self.assertEqual(built.failure.code, "SANDBOX_DENIED")
            self.assertEqual(built.failure.phase, "sandbox")
            self.assertEqual(built.failure.kernel_message, diagnostic.decode())
            self.assertEqual(built.stderr, diagnostic.decode())
            self.assertEqual(built.stdout, "launch log\n")

    def test_unexplained_worker_death_is_not_classified_as_sandbox_or_oom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(sandbox, "popen") as launcher:
            launcher.return_value.returncode = -9
            launcher.return_value.communicate.return_value = (b"before exit\n", b"")
            built = run_build(BuildRequest(source="fixture", project_dir=Path(temporary)))
            launcher.assert_called_once()
        self.assertEqual(built.failure.code, "WORKER_CRASH")
        self.assertEqual(built.failure.phase, "crash")
        self.assertIn("-9", built.failure.message)
        self.assertIn("no exit cause was inferred", built.failure.message)
        self.assertEqual(built.stdout, "before exit\n")

    def test_memory_bootstrap_setup_failure_is_not_reported_as_oom(self) -> None:
        from aad.engine import memory

        with tempfile.TemporaryDirectory() as temporary, patch.object(sandbox, "popen") as launcher:
            launcher.return_value.returncode = 1
            launcher.return_value.communicate.return_value = (
                memory.SETUP_FAILURE_MARKER + b"ValueError: current limit exceeds maximum limit\n",
                b"ValueError: current limit exceeds maximum limit\n",
            )
            built = run_build(BuildRequest(source="raise AssertionError('must not execute')",
                                           project_dir=Path(temporary)))
        self.assertEqual(built.failure.code, "SANDBOX_DENIED")
        self.assertIn("model code was not run", built.failure.message)

    def test_startup_memory_error_is_reported_as_out_of_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(sandbox, "popen") as launcher:
            launcher.return_value.returncode = 1
            launcher.return_value.communicate.return_value = (b"", b"MemoryError: startup allocation\n")
            built = run_build(BuildRequest(source="result = Box(1, 1, 1)", project_dir=Path(temporary)))
        self.assertEqual(built.failure.code, "OUT_OF_MEMORY")
        self.assertEqual(built.failure.phase, "oom")


@unittest.skipUnless(sys.platform == "darwin", "macOS Seatbelt escape tests")
class MacSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aad-sandbox-")
        self.root = Path(self.temporary.name).resolve()
        self.job = self.root / 'job'
        self.job.mkdir()
        self.secret = self.root / 'outside.txt'
        self.secret.write_text('outside-fixture')

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(self, source: str, *, reads: tuple[Path, ...] = ()) -> subprocess.CompletedProcess[str]:
        process = sandbox.popen([sys.executable, '-I', '-B', '-c', source], self.job, read_paths=reads, text=True)
        try:
            stdout, stderr = process.communicate(timeout=60)
        except BaseException:
            process.kill()
            process.communicate(timeout=2)
            raise
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def test_python_and_job_writes_work(self) -> None:
        result = self.run_script("from pathlib import Path; Path('result.txt').write_text('built'); print('ready')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ready')
        self.assertEqual((self.job/'result.txt').read_text(), 'built')

    def test_child_does_not_receive_inherited_environment_secrets(self) -> None:
        with patch.dict(os.environ, {'AAD_TEST_SECRET': 'private-fixture'}):
            result = self.run_script("import os; assert 'AAD_TEST_SECRET' not in os.environ; print('clean')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'clean')

    def test_outside_read_and_write_are_denied(self) -> None:
        source = f'''from pathlib import Path
for op in [lambda: Path({str(self.secret)!r}).read_text(), lambda: Path({str(self.secret)!r}).write_text('changed')]:
    try: op()
    except PermissionError: print('denied')
    else: raise RuntimeError('sandbox escaped')
'''
        result = self.run_script(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['denied', 'denied'])
        self.assertEqual(self.secret.read_text(), 'outside-fixture')

    def test_host_home_directory_is_not_readable(self) -> None:
        home = Path.home().resolve()
        if home == self.job:
            self.skipTest("the sandbox fixture is also the host home directory")
        source = f'''from pathlib import Path
try: list(Path({str(home)!r}).iterdir())
except PermissionError: print('denied')
else: raise RuntimeError('host home directory was readable')
'''
        result = self.run_script(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'denied')

    def test_symlink_and_hardlink_escapes_are_denied(self) -> None:
        (self.job/'link').symlink_to(self.secret)
        source = f'''from pathlib import Path
import os
try: Path('link').read_text()
except PermissionError: pass
else: raise RuntimeError('symlink escaped')
try:
    os.link({str(self.secret)!r}, 'hardlink')
    Path('hardlink').read_text()
except PermissionError: pass
else: raise RuntimeError('hardlink escaped')
'''
        result = self.run_script(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_network_and_subprocess_creation_are_denied(self) -> None:
        source = '''import socket, subprocess
try: socket.create_connection(('127.0.0.1', 9), timeout=1)
except PermissionError: pass
else: raise RuntimeError('network was not denied')
try: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'test', ('127.0.0.1', 9))
except PermissionError: pass
else: raise RuntimeError('UDP was not denied')
try: socket.socket().bind(('127.0.0.1', 0))
except PermissionError: pass
else: raise RuntimeError('listen address was not denied')
try: subprocess.run(['/usr/bin/true'], check=True)
except PermissionError: pass
else: raise RuntimeError('subprocess was not denied')
'''
        result = self.run_script(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_asset_read_is_read_only(self) -> None:
        source = f'''from pathlib import Path
p=Path({str(self.secret)!r})
assert p.read_text() == 'outside-fixture'
try: p.write_text('changed')
except PermissionError: pass
else: raise RuntimeError('asset was writable')
'''
        result = self.run_script(source, reads=(self.secret,))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_package_import_can_list_its_search_directory_without_reading_siblings(self) -> None:
        search = self.root / "editable-src"
        package = search / "fixture_package"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("value = 42\n")
        sibling = search / "private.txt"
        sibling.write_text("not a runtime dependency")
        source = f'''import sys
sys.path.insert(0, {str(search)!r})
import fixture_package
assert fixture_package.value == 42
try: open({str(sibling)!r}).read()
except PermissionError: print('sibling denied')
else: raise RuntimeError('sibling content was readable')
'''
        result = self.run_script(source, reads=(package,))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "sibling denied")

    def test_native_open_and_relative_traversal_are_denied(self) -> None:
        source = f'''import ctypes, os
libc=ctypes.CDLL(None, use_errno=True)
fd=libc.open({os.fsencode(self.secret)!r}, os.O_RDONLY)
assert fd == -1, 'native open escaped'
try: open('../outside.txt').read()
except PermissionError: pass
else: raise RuntimeError('relative path escaped')
'''
        result = self.run_script(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exec_preserves_the_boundary(self) -> None:
        source = f'''import os
os.execv('/bin/sh', ['sh', '-c', 'printf changed > "$1"', 'sh', {str(self.secret)!r}])
'''
        result = self.run_script(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.secret.read_text(), 'outside-fixture')

    def test_profile_path_cannot_inject_policy(self) -> None:
        self.job = self.job / 'quoted ") (allow default) ;'
        self.job.mkdir()
        source = f'''from pathlib import Path
try: Path({str(self.secret)!r}).read_text()
except PermissionError: print('denied')
else: raise RuntimeError('profile injected')
'''
        result = self.run_script(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'denied')

    def test_kernel_import_and_real_geometry_work(self) -> None:
        result = self.run_script('from build123d import Box, export_brep; shape=Box(2,3,4); assert export_brep(shape, "part.brep"); print(shape.is_valid, round(shape.volume, 6))')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'True 24.0')
        self.assertGreater((self.job/'part.brep').stat().st_size, 0)

    def test_public_run_build_denies_outside_io_and_completes_real_brep(self) -> None:
        outside_output = self.root / "escaped.txt"
        source = f'''import os, subprocess
from pathlib import Path
assert "AAD_TEST_SECRET" not in os.environ
assert Path.cwd() == Path({str(self.job)!r})
assert Path(os.environ["HOME"]) == Path.cwd()
assert Path(os.environ["TMPDIR"]).is_relative_to(Path.cwd())
for name, operation in [
    ("read", lambda: Path({str(self.secret)!r}).read_text()),
    ("overwrite", lambda: Path({str(self.secret)!r}).write_text("escaped")),
    ("create", lambda: Path({str(outside_output)!r}).write_text("escaped")),
    ("traversal", lambda: Path("../outside.txt").read_text()),
    ("subprocess", lambda: subprocess.run(["/usr/bin/true"], check=True)),
]:
    try: operation()
    except PermissionError: print(name + " denied")
    else: raise AssertionError(name + " escaped the public build sandbox")
result = Box(2, 3, 4)
'''
        with patch.dict(os.environ, {"AAD_TEST_SECRET": "private-fixture"}):
            built = run_build(BuildRequest(source=source, project_dir=self.root, job_dir=self.job))
        self.assertTrue(built.ok, f"{built.summary}\n{built.failure}\n{built.stderr}")
        self.assertEqual(built.status, "draft")
        self.assertFalse(built.requirements_proven)
        self.assertEqual(built.stdout.splitlines(), ["read denied", "overwrite denied", "create denied",
                                                    "traversal denied", "subprocess denied"])
        self.assertEqual(self.secret.read_text(), "outside-fixture")
        self.assertFalse(outside_output.exists())
        self.assertEqual(built.metrics["bbox"], [2.0, 3.0, 4.0])
        self.assertEqual(Path(built.artifacts["part.brep"]), self.job / "part.brep")
        from build123d import import_brep
        shape = import_brep(built.artifacts["part.brep"])
        self.assertTrue(shape.is_valid)
        self.assertEqual(len(shape.solids()), 1)
        self.assertAlmostEqual(shape.volume, 24.0, places=5)
