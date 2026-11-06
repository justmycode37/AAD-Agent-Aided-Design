from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from aad.engine import memory, sandbox, validation
from aad.engine.types import Check
from aad.engine.validation import inspect_brep


class GeometryInspectionTests(unittest.TestCase):
    @staticmethod
    def _brep(root, shape=None):
        import build123d as bd
        root = Path(root).resolve()
        path = root / 'part.brep'
        bd.export_brep(bd.Box(2, 3, 4) if shape is None else shape, path)
        return path

    def test_public_box_has_independent_metrics_and_bound_snapshot(self):
        with tempfile.TemporaryDirectory(prefix='aad-inspect-box-') as temporary:
            path = self._brep(temporary)
            evidence = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm', spec={'requirements': [{'id': 'fit'}]})
            self.assertTrue(evidence.ok, evidence.failure)
            self.assertIsNone(evidence.failure)
            self.assertEqual(evidence.metrics['bbox'], [2, 3, 4])
            self.assertAlmostEqual(evidence.metrics['volume'], 24)
            self.assertEqual(evidence.metrics['solids'], 1)
            self.assertEqual((evidence.metrics['faces'], evidence.metrics['edges'], evidence.metrics['vertices']), (6, 12, 8))
            self.assertTrue(evidence.metrics['valid'])
            self.assertIn('brep_valid', {c.id for c in evidence.checks})
            self.assertFalse(hasattr(evidence, 'requirements_proven'))
            self.assertFalse(hasattr(evidence, 'status'))
            snapshot = evidence.inspection_dir / 'part.brep'
            self.assertEqual(path.read_bytes(), snapshot.read_bytes())
            self.assertEqual(evidence.snapshot_sha256, hashlib.sha256(snapshot.read_bytes()).hexdigest())
            record = {'protocol': 1, 'snapshot_sha256': evidence.snapshot_sha256,
                      'spec_sha256': evidence.spec_sha256, 'kernel_versions': evidence.kernel_versions,
                      'inputs': {'kind': 'part', 'units': 'mm', 'spec': {'requirements': [{'id': 'fit'}]}}}
            self.assertEqual(evidence.input_sha256, hashlib.sha256(validation._canonical_json(record)).hexdigest())
            self.assertEqual(evidence.spec_sha256, hashlib.sha256(validation._canonical_json({'requirements': [{'id': 'fit'}]})).hexdigest())
            self.assertIn('build123d', evidence.kernel_versions)

    def test_validator_generates_clean_scene_from_its_loaded_brep(self):
        with tempfile.TemporaryDirectory(prefix='aad-inspect-scene-') as temporary:
            path = self._brep(temporary)
            evidence = inspect_brep(
                path, candidate_dir=temporary, kind='part', units='mm',
                render_scene=True, project='inspection-test', rev=3,
            )
            self.assertTrue(evidence.ok, evidence.failure)
            self.assertIsNotNone(evidence.scene)
            self.assertEqual((evidence.scene.project, evidence.scene.rev, evidence.scene.status),
                             ('inspection-test', 3, 'draft'))
            scene_bytes = (evidence.scene_dir / 'scene.bin.gz').read_bytes()
            self.assertEqual(hashlib.sha256(scene_bytes).hexdigest(), evidence.scene.scene_id)
            self.assertGreater(evidence.scene_elapsed_ms, 0)

    def test_viewer_scene_serialization_does_not_load_the_vtk_image_renderer(self):
        program = """import sys
from pathlib import Path
import build123d as bd
from aad.engine.types import ModelContext
from aad_render import make_scene
ctx = ModelContext(kind='part', units='mm', spec={}, shape=bd.Box(1, 2, 3), metrics={})
ctx.project, ctx.rev, ctx.status = 'scene-test', 1, 'draft'
make_scene(ctx, Path(sys.argv[1]))
assert not any(name == 'vtk' or name.startswith('vtk.') for name in sys.modules)
"""
        with tempfile.TemporaryDirectory(prefix='aad-scene-vtk-') as temporary:
            subprocess.run(
                [sys.executable, '-c', program, str(Path(temporary) / 'scene')],
                check=True,
                timeout=30,
            )

    def test_nested_assembly_uses_all_solids_and_explicit_coordinate_units(self):
        import build123d as bd
        shape = bd.Compound(children=[bd.Compound(children=[bd.Box(2, 3, 4)])])
        shape.move(bd.Location((3, 5, 7)))
        with tempfile.TemporaryDirectory(prefix='aad-inspect-nested-') as temporary:
            path = self._brep(temporary, shape)
            hashes = []
            for units in ('mm', 'in'):
                with self.subTest(units=units):
                    evidence = inspect_brep(path, candidate_dir=temporary, kind='assembly', units=units)
                    self.assertTrue(evidence.ok, evidence.failure)
                    self.assertEqual(evidence.metrics['units'], units)
                    self.assertAlmostEqual(evidence.metrics['volume'], 24)
                    for actual, expected in zip(evidence.metrics['center_of_mass'], (3, 5, 7), strict=True):
                        self.assertAlmostEqual(actual, expected)
                    self.assertEqual(evidence.metrics['bbox'], [2, 3, 4])
                    hashes.append(evidence.input_sha256)
            self.assertNotEqual(*hashes)

    def test_multiple_bodies_cannot_pass_as_one_part(self):
        import build123d as bd
        shape = bd.Compound(children=[bd.Box(1, 2, 3), bd.Box(1, 2, 3).moved(bd.Location((10, 0, 0)))])
        with tempfile.TemporaryDirectory(prefix='aad-inspect-bodies-') as temporary:
            path = self._brep(temporary, shape)
            result = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm')
            self.assertFalse(result.ok)
            self.assertEqual(result.failure.code, 'INVALID_SOLID')
            self.assertEqual(result.metrics['solids'], 2)
            self.assertAlmostEqual(result.metrics['volume'], 12)

    def test_positive_body_cannot_mask_a_reversed_solid(self):
        import build123d as bd
        from copy import deepcopy
        positive = bd.Box(2, 3, 4).solid()
        small = bd.Box(1, 2, 3).solid().moved(bd.Location((10, 0, 0)))
        reversed_solid = deepcopy(small)
        reversed_solid.wrapped.Reverse()
        self.assertAlmostEqual(reversed_solid.volume, -6)
        cases = [('forward', bd.Compound(children=[positive, small]), True),
                 ('reversed', bd.Compound(children=[positive, reversed_solid]), False),
                 ('reversed-alone', reversed_solid, False)]
        with tempfile.TemporaryDirectory(prefix='aad-inspect-oriented-solids-') as temporary:
            for name, shape, expected_ok in cases:
                for units in ('mm', 'in'):
                    with self.subTest(case=name, units=units):
                        root = Path(temporary) / (name + '-' + units)
                        root.mkdir()
                        path = self._brep(root, shape)
                        result = inspect_brep(path, candidate_dir=root, kind='assembly', units=units)
                        self.assertEqual(result.ok, expected_ok, result.failure)
                        if expected_ok:
                            self.assertAlmostEqual(result.metrics['volume'], 30)
                            self.assertAlmostEqual(result.metrics['center_of_mass'][0], 2)
                        else:
                            self.assertEqual((result.failure.code, result.failure.phase), ('INVALID_SOLID', 'validate'))
                            self.assertIn('solid volume', result.failure.kernel_message)
                            self.assertEqual(result.failure.feature, 'unknown')
                            self.assertNotIn('center_of_mass', result.metrics)
                            self.assertTrue((result.inspection_dir / 'part.brep').is_file())

    def test_wrapper_metrics_cannot_change_native_inspection(self):
        import build123d as bd
        from types import SimpleNamespace
        shape = bd.Box(10, 8, 4)
        shape.bounding_box = lambda: SimpleNamespace(size=SimpleNamespace(X=123, Y=8, Z=4))
        self.assertEqual(shape.bounding_box().size.X, 123)
        with tempfile.TemporaryDirectory(prefix='aad-inspect-wrapper-') as temporary:
            path = self._brep(temporary, shape)
            result = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm')
            self.assertTrue(result.ok, result.failure)
            self.assertEqual(result.metrics['bbox'], [10, 8, 4])
            self.assertAlmostEqual(result.metrics['volume'], 320)

    def test_garbage_brep_returns_observed_failure_and_keeps_original(self):
        with tempfile.TemporaryDirectory(prefix='aad-inspect-garbage-') as temporary:
            path = Path(temporary) / 'part.brep'
            path.write_bytes(b'not a BREP fixture')
            evidence = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm')
            self.assertFalse(evidence.ok)
            self.assertEqual((evidence.failure.code, evidence.failure.phase), ('INVALID_SOLID', 'validate'))
            self.assertEqual(evidence.failure.feature, 'unknown')
            self.assertTrue(evidence.failure.kernel_message)
            self.assertEqual(path.read_bytes(), b'not a BREP fixture')
            self.assertTrue((evidence.inspection_dir / 'part.brep').is_file())

    def test_outside_link_empty_and_directory_paths_never_launch(self):
        with tempfile.TemporaryDirectory(prefix='aad-inspect-paths-') as temporary:
            root = Path(temporary).resolve()
            candidate = root / 'candidate'
            candidate.mkdir()
            outside = root / 'outside.brep'
            outside.write_bytes(b'outside fixture')
            alias = candidate / 'alias.brep'
            alias.symlink_to(outside)
            original = candidate / 'original.brep'
            original.write_bytes(b'original fixture')
            hardlink = candidate / 'hardlink.brep'
            os.link(original, hardlink)
            empty = candidate / 'empty.brep'
            empty.touch()
            for path in (outside, alias, hardlink, original, empty, candidate):
                with self.subTest(path=path), patch.object(sandbox, 'popen') as launch:
                    evidence = inspect_brep(path, candidate_dir=candidate, kind='part', units='mm')
                    launch.assert_not_called()
                self.assertFalse(evidence.ok)
                self.assertEqual(evidence.failure.code, 'BAD_INPUT')

    def test_malformed_input_never_launches(self):
        with tempfile.TemporaryDirectory(prefix='aad-inspect-input-') as temporary:
            path = Path(temporary) / 'part.brep'
            path.write_bytes(b'fixture')
            for overrides in ({'timeout_s': 0}, {'timeout_s': float('nan')}, {'timeout_s': True},
                              {'kind': '2d'}, {'units': 'unknown'}, {'spec': {'a': float('inf')}},
                              {'spec': {'x': (1, 2)}}, {'spec': []}):
                with self.subTest(overrides=overrides), patch.object(sandbox, 'popen') as launch:
                    args = {'candidate_dir': temporary, 'kind': 'part', 'units': 'mm'} | overrides
                    evidence = inspect_brep(path, **args)
                    launch.assert_not_called()
                    self.assertEqual(evidence.failure.code, 'BAD_INPUT')

    def test_missing_boundary_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix='aad-inspect-launch-') as temporary:
            path = Path(temporary) / 'part.brep'
            path.write_bytes(b'fixture')
            with patch.object(sandbox, 'popen', side_effect=sandbox.SandboxUnavailable('fixture missing boundary')) as launch:
                evidence = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm')
                launch.assert_called_once()
            self.assertEqual((evidence.failure.code, evidence.failure.phase), ('SANDBOX_DENIED', 'sandbox'))
            self.assertIn('fixture missing boundary', evidence.failure.kernel_message)

    @unittest.skipUnless(sys.platform == 'darwin' or sys.platform.startswith('linux'), 'RLIMIT_AS policy is unsupported')
    def test_clean_brep_worker_receives_the_configured_memory_budget(self):
        memory_limit = 64 * 1024 ** 2
        with tempfile.TemporaryDirectory(prefix='aad-inspect-memory-') as temporary:
            path = Path(temporary) / 'part.brep'
            path.write_bytes(b'fixture')
            with patch.object(sandbox, 'popen', side_effect=sandbox.SandboxUnavailable('fixture missing boundary')) as launch:
                evidence = inspect_brep(
                    path,
                    candidate_dir=temporary,
                    kind='part',
                    units='mm',
                    memory_limit_bytes=memory_limit,
                )
            self.assertEqual((evidence.failure.code, evidence.failure.phase), ('SANDBOX_DENIED', 'sandbox'))
            self.assertEqual(
                launch.call_args.kwargs['address_space'],
                memory.bootstrap_address_space_budget(memory_limit),
            )
            argv = launch.call_args.args[0]
            self.assertEqual(argv[-1], str(memory_limit))
            bootstrap = argv[4]
            self.assertLess(
                bootstrap.index('install_worker_memory_budget'),
                bootstrap.index('from aad.engine._validation_worker import main'),
            )

    @unittest.skipUnless(sys.platform == 'darwin' or sys.platform.startswith('linux'), 'RLIMIT_AS policy is unsupported')
    def test_clean_sheet_worker_receives_the_configured_memory_budget(self):
        memory_limit = 96 * 1024 ** 2
        with tempfile.TemporaryDirectory(prefix='aad-sheet-memory-') as temporary:
            path = Path(temporary) / 'sheet.json'
            path.write_text('{}')
            with patch.object(sandbox, 'popen', side_effect=sandbox.SandboxUnavailable('fixture missing boundary')) as launch:
                evidence = validation.inspect_sheet(
                    path,
                    candidate_dir=temporary,
                    units='mm',
                    memory_limit_bytes=memory_limit,
                )
            self.assertEqual((evidence.failure.code, evidence.failure.phase), ('SANDBOX_DENIED', 'sandbox'))
            self.assertEqual(
                launch.call_args.kwargs['address_space'],
                memory.bootstrap_address_space_budget(memory_limit),
            )
            argv = launch.call_args.args[0]
            self.assertEqual(argv[-1], str(memory_limit))
            bootstrap = argv[4]
            self.assertLess(
                bootstrap.index('install_worker_memory_budget'),
                bootstrap.index('from aad.engine._sheet_validation_worker import main'),
            )

    def test_native_exit_and_hang_are_bounded_without_invented_causes(self):
        cases = [('import os; os.write(2,b"native inspection fixture"); os._exit(23)', 5, 'WORKER_CRASH'),
                 ('import time; time.sleep(30)', 0.2, 'TIMEOUT')]
        with tempfile.TemporaryDirectory(prefix='aad-inspect-lifecycle-') as temporary:
            path = Path(temporary) / 'part.brep'
            path.write_bytes(b'fixture')
            for bootstrap, budget, code in cases:
                with self.subTest(code=code), patch.object(validation, '_BOOTSTRAP', bootstrap):
                    started = time.monotonic()
                    evidence = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm', timeout_s=budget)
                    self.assertLess(time.monotonic() - started, budget + 3)
                    self.assertEqual(evidence.failure.code, code)
                    self.assertTrue((evidence.inspection_dir / 'part.brep').is_file())
                    if code == 'WORKER_CRASH':
                        self.assertIn('status 23', evidence.failure.message)
                        self.assertIn('native inspection fixture', evidence.stderr)

    def test_candidate_python_is_neither_importable_nor_readable(self):
        bootstrap = '''import sys
sys.path.insert(0, sys.argv.pop(1))
from pathlib import Path
import importlib.util
assert importlib.util.find_spec('aad_inspection_poison') is None
try:
    Path.cwd().parent.joinpath('aad_inspection_poison.py').read_text()
except OSError:
    pass
else:
    raise AssertionError('candidate Python content was readable')
from aad.engine._validation_worker import main
main()
'''
        with tempfile.TemporaryDirectory(prefix='aad-inspect-module-') as temporary:
            path = self._brep(temporary)
            (Path(temporary) / 'aad_inspection_poison.py').write_text("raise RuntimeError('must not execute')")
            (Path(temporary) / 'model.py').write_text("raise RuntimeError('model must not execute')")
            with patch.object(validation, '_BOOTSTRAP', bootstrap):
                evidence = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm')
            self.assertTrue(evidence.ok, (evidence.failure, evidence.stderr))

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group lifecycle fixture')
    def test_timeout_cleans_pipes_after_inspector_leader_exits(self):
        # Trusted lifecycle fixture, outside the production sandbox launcher.
        # The Mac policy deliberately denies child creation in production.
        program = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print('inspector leader exited',flush=True)"
        with tempfile.TemporaryDirectory(prefix='aad-inspect-orphan-') as temporary:
            path = Path(temporary) / 'part.brep'
            path.write_bytes(b'fixture')
            process = subprocess.Popen([sys.executable, '-c', program], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                process.wait(timeout=3)
                with patch.object(sandbox, 'popen', return_value=process):
                    started = time.monotonic()
                    result = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm', timeout_s=0.2)
                self.assertLess(time.monotonic() - started, 3)
                self.assertEqual(result.failure.code, 'TIMEOUT')
                self.assertIn('inspector leader exited', result.stdout)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    # macOS reports EPERM for a group that holds only zombies.
                    pass
                process.communicate(timeout=2)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux bubblewrap descendant cleanup')
    def test_validator_deadline_kills_a_detached_bubblewrap_descendant(self):
        with tempfile.TemporaryDirectory(prefix='aad-inspect-linux-timeout-') as temporary:
            root = Path(temporary).resolve()
            path = root / 'part.brep'
            path.write_bytes(b'validator deadline fixture')
            # Bound the child so a communicate() regression cannot leave the
            # inspector's pipes open indefinitely.
            child = (
                "from pathlib import Path\n"
                "import time\n"
                "Path('validator-child-started').write_text('started')\n"
                "deadline = time.monotonic() + 20.0\n"
                "while not Path('release-child').exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
                "if Path('release-child').exists(): Path('validator-child-survived').write_text('survived')\n"
            )
            bootstrap = (
                "import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True)\n"
                "time.sleep(30)\n"
            )
            started = time.monotonic()
            with patch.object(validation, '_BOOTSTRAP', bootstrap):
                evidence = inspect_brep(
                    path,
                    candidate_dir=root,
                    kind='part',
                    units='mm',
                    timeout_s=8.0,
                )
            self.assertLess(time.monotonic() - started, 12.0)
            self.assertEqual(evidence.failure.code, 'TIMEOUT')
            inspection_dir = evidence.inspection_dir
            self.assertTrue((inspection_dir / 'validator-child-started').is_file())

            # Release a child that escaped cleanup so the regression test itself
            # never leaves a process behind, then verify it did not survive.
            (inspection_dir / 'release-child').touch()
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and not (inspection_dir / 'validator-child-survived').exists():
                time.sleep(0.01)
            self.assertFalse((inspection_dir / 'validator-child-survived').exists())

    def test_response_binding_and_schema_are_validated(self):
        with tempfile.TemporaryDirectory(prefix='aad-inspect-response-') as temporary:
            path = Path(temporary) / 'part.brep'
            path.write_bytes(b'fixture')
            mutations = [lambda w: w.update(input_sha256='wrong'), lambda w: w.update(nonce='wrong'),
                         lambda w: w.update(snapshot_sha256='wrong'), lambda w: w['metrics'].update(volume=float('nan')),
                         lambda w: w['metrics'].update(solids=True), lambda w: w['metrics'].update(solids=2),
                         lambda w: w['metrics'].update(bbox=[2, -3, 4]), lambda w: w['checks'].append('malformed'),
                         lambda w: w.update(ok='yes'), lambda w: w.update(failure={'code': 'arbitrary'}),
                         lambda w: w['checks'].append(asdict(Check(id='failed', status='fail')))]
            for mutate in mutations:
                with self.subTest(mutation=mutate):
                    def launch(argv, job, mutate=mutate, **kwargs):
                        process = Mock(returncode=0)
                        process.poll.return_value = 0
                        def communicate(*, input, timeout):
                            p = json.loads(input)
                            wire = {'ok': True, 'nonce': p['nonce'], 'input_sha256': p['input_sha256'],
                                    'snapshot_sha256': p['record']['snapshot_sha256'], 'failure': None,
                                    'metrics': {'units': 'mm', 'bbox': [2, 3, 4], 'volume': 24, 'area': 52,
                                                'center_of_mass': [0, 0, 0], 'valid': True, 'solids': 1, 'faces': 6, 'edges': 12, 'vertices': 8},
                                    'checks': [asdict(Check(id='valid', status='pass'))]}
                            mutate(wire)
                            return validation._MARKER + json.dumps(wire).encode() + b'\n', b'diagnostic retained'
                        process.communicate.side_effect = communicate
                        return process
                    with patch.object(sandbox, 'popen', side_effect=launch):
                        result = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm')
                    self.assertFalse(result.ok)
                    self.assertEqual(result.failure.code, 'WORKER_CRASH')
                    self.assertIn('diagnostic retained', result.stderr)

    def test_snapshot_mutation_during_child_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='aad-inspect-mutation-') as temporary:
            path = Path(temporary) / 'part.brep'
            path.write_bytes(b'fixture')
            bootstrap = '''import sys,json
from pathlib import Path
payload=json.loads(sys.stdin.buffer.readline())
Path('part.brep').write_text('changed')
print('AAD_GEOMETRY_EVIDENCE:'+json.dumps({'nonce':payload['nonce'],'input_sha256':payload['input_sha256'],'snapshot_sha256':payload['record']['snapshot_sha256']}))
'''
            with patch.object(validation, '_BOOTSTRAP', bootstrap):
                result = inspect_brep(path, candidate_dir=temporary, kind='part', units='mm')
            self.assertFalse(result.ok)
            self.assertIn('snapshot changed', result.failure.kernel_message)
            self.assertEqual(path.read_bytes(), b'fixture')

    def test_parent_import_is_kernel_free(self):
        program = "import sys; import aad.engine.validation; assert not any(m == 'build123d' or m == 'OCP' or m == 'vtk' for m in sys.modules)"
        subprocess.run([sys.executable, '-c', program], check=True, timeout=10)
