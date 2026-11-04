from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiad.engine import sandbox


class SandboxPolicyTests(unittest.TestCase):
    def test_environment_does_not_inherit_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"AIAD_TEST_SECRET": "private-fixture"}):
            env = sandbox.environment(Path(temporary))
            self.assertNotIn("AIAD_TEST_SECRET", env)
            self.assertEqual(Path(env["HOME"]), Path(temporary).resolve())
            self.assertTrue(Path(env["TMPDIR"]).is_dir())

    def test_missing_boundary_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(sandbox.shutil, "which", return_value=None):
            with self.assertRaises(sandbox.SandboxUnavailable):
                sandbox.command([sys.executable, "-c", "print('unsafe')"], Path(temporary))

    def test_unimplemented_windows_boundary_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(sandbox.sys, "platform", "win32"):
            with self.assertRaises(sandbox.SandboxUnavailable):
                sandbox.command([sys.executable], Path(temporary))


@unittest.skipUnless(sys.platform == "darwin", "macOS Seatbelt escape tests")
class MacSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aiad-sandbox-")
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
        with patch.dict(os.environ, {'AIAD_TEST_SECRET': 'private-fixture'}):
            result = self.run_script("import os; assert 'AIAD_TEST_SECRET' not in os.environ; print('clean')")
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
