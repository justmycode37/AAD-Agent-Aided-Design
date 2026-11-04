from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from aiad.engine import _preview
from aiad.engine.jobs import run_build
from aiad.engine.types import BuildRequest, BuildResult, Check, Failure, ModelContext, SceneManifest


class PreviewWorkerTests(unittest.TestCase):
    def test_public_preview_keeps_spec_failure_with_placeholder_id(self):
        source = '''import sys
from types import ModuleType
from aiad.engine.types import Check
quality = ModuleType('aiad_quality')
quality.spec_checks = lambda ctx: [Check(id='render_not_requested', suite='spec', level='error', status='fail', message='Required width is wrong', expected='5', actual='2')]
quality.process_checks = lambda ctx: []
sys.modules['aiad_quality'] = quality
result = Box(2, 3, 4)
'''
        with tempfile.TemporaryDirectory(prefix='aiad-preview-check-id-') as temporary:
            for preview in (False, True):
                with self.subTest(preview=preview), patch.object(_preview, '_BOOTSTRAP', 'import os; os._exit(23)'):
                    result = run_build(BuildRequest(source=source, preview=preview,
                                                    spec={'requirements': [{'id': 'render_not_requested', 'bbox': {'x': 5}}]},
                                                    project_dir=Path(temporary), job_dir=Path(temporary) / str(preview)))
                self.assertFalse(result.ok)
                self.assertEqual(result.status, 'failed')
                self.assertTrue(Path(result.artifacts['part.brep']).is_file())
                requirements = [c for c in result.checks if c.suite == 'spec' and c.id == 'render_not_requested']
                self.assertEqual(len(requirements), 1)
                self.assertEqual((requirements[0].level, requirements[0].status), ('error', 'fail'))
                self.assertEqual((requirements[0].expected, requirements[0].actual), ('5', '2'))
                self.assertEqual(result.checks_summary['fail'], 1)

    def test_only_internal_placeholder_is_replaced(self):
        cases = [('spec', 'error', 'fail'), ('process', 'error', 'fail'), ('spec', 'error', 'skip'),
                 ('process', 'warn', 'skip'), ('hard', 'error', 'skip'), ('hard', 'warn', 'fail')]
        with tempfile.TemporaryDirectory(prefix='aiad-preview-check-id-') as temporary:
            for suite, level, status in cases:
                with self.subTest(suite=suite, level=level, status=status):
                    result = self._result(Path(temporary))
                    original = Check(id='render_not_requested', suite=suite, level=level, status=status, message='preserve me')
                    result.checks.append(original)
                    result.checks_summary[status] += 1
                    process = Mock(returncode=23)
                    process.poll.return_value = 23
                    process.communicate.return_value = b'', b''
                    with patch.object(_preview.sandbox, 'popen', return_value=process):
                        _preview.add_preview(result, timeout_s=60)
                    self.assertIn(original, result.checks)
                    self.assertEqual(result.checks_summary['skip'], int(status == 'skip'))
                    self.assertEqual(result.checks_summary['fail'], int(status == 'fail'))
                    self.assertEqual(result.scene.checks_summary, result.checks_summary)

    def test_native_preview_does_not_execute_the_model_render_module(self):
        source = '''from pathlib import Path
import sys
Path('aiad_render.py').write_text("def make_scene(*args): return {}\\ndef render(*args):\\n    from pathlib import Path\\n    Path('model-render-was-run').write_text('untrusted')\\n    raise RuntimeError('model render executed')\\n")
sys.path.insert(0, str(Path.cwd()))
result = Box(2, 3, 4)
'''
        with tempfile.TemporaryDirectory(prefix='aiad-preview-native-') as temporary:
            result = run_build(BuildRequest(source=source, preview=True, project_dir=Path(temporary),
                                            job_dir=Path(temporary) / 'job'))
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(result.status, 'draft')
            self.assertAlmostEqual(result.metrics['volume'], 24)
            self.assertTrue(Path(result.artifacts['part.brep']).is_file())
            self.assertFalse((Path(result.job_dir) / 'model-render-was-run').exists())
            preview = [c for c in result.checks if c.id.startswith('render_')]
            self.assertEqual(len(preview), 1)
            self.assertNotEqual(preview[0].id, 'render_not_requested')
            self.assertNotIn('model render executed', preview[0].actual or '')

    def test_public_preview_crash_and_timeout_retain_built_geometry(self):
        cases = [
            ('import os; os.write(2, b"native fixture diagnostic"); os._exit(23)', 2.0, 'render_failed', 'status 23'),
            ('import time; time.sleep(30)', 0.2, 'render_timeout', '0.2 second budget'),
        ]
        with tempfile.TemporaryDirectory(prefix='aiad-preview-build-') as temporary:
            for index, (bootstrap, budget, identifier, message) in enumerate(cases):
                with self.subTest(identifier=identifier):
                    started = time.monotonic()
                    with patch.object(_preview, '_BOOTSTRAP', bootstrap), patch.object(_preview, '_TIMEOUT_S', budget):
                        result = run_build(BuildRequest(source='result = Box(2, 3, 4)', preview=True,
                                                        project_dir=Path(temporary), job_dir=Path(temporary) / str(index)))
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual(result.status, 'draft')
                    self.assertIsNone(result.failure)
                    self.assertFalse(result.requirements_proven)
                    self.assertAlmostEqual(result.metrics['volume'], 24)
                    self.assertTrue(Path(result.artifacts['part.brep']).is_file())
                    check = next(c for c in result.checks if c.id == identifier)
                    self.assertEqual((check.level, check.status), ('warn', 'warn'))
                    self.assertIn(message, check.message)
                    self.assertIn('retained', check.message)
                    self.assertNotIn('render_not_requested', {c.id for c in result.checks})
                    self.assertLess(time.monotonic() - started, 65)
                    if identifier == 'render_failed':
                        self.assertIn('native fixture diagnostic', result.stderr)

    def test_private_success_updates_summary_without_promoting_status(self):
        with tempfile.TemporaryDirectory(prefix='aiad-preview-protocol-') as temporary:
            root = Path(temporary).resolve()
            result = self._result(root)
            original_metrics = dict(result.metrics)

            def launch(argv, job, *, read_paths):
                self.assertEqual(Path(job).parent, root)
                self.assertNotEqual(Path(job), root)
                self.assertEqual(tuple(read_paths), (root / 'part.brep',))
                self.assertIn('-I', argv)
                process = Mock(returncode=0)
                process.poll.return_value = 0

                def communicate(*, input, timeout):
                    payload = json.loads(input)
                    self.assertNotIn('source', payload)
                    self.assertEqual(payload['context']['job_dir'], str(job))
                    path = Path(job) / 'sheet.png'
                    path.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
                    check = Check(id='render_generated', suite='hard', level='warn', status='pass', message='fixture')
                    return self._wire(check, {'render_sheet.png': str(path)}), b'preview diagnostic'

                process.communicate.side_effect = communicate
                return process

            with patch.object(_preview.sandbox, 'popen', side_effect=launch):
                _preview.add_preview(result, timeout_s=60)
            self.assertEqual(result.status, 'draft')
            self.assertFalse(result.requirements_proven)
            self.assertEqual(result.metrics, original_metrics)
            self.assertEqual(result.checks_summary, {'pass': 1, 'fail': 0, 'warn': 0, 'skip': 0}, result.checks[-1].actual)
            self.assertEqual(result.scene.checks_summary, result.checks_summary)
            self.assertEqual(len(result.artifacts), 2)
            self.assertIn('preview diagnostic', result.stderr)

    def test_invalid_or_outside_preview_artifacts_warn_and_preserve_failure(self):
        with tempfile.TemporaryDirectory(prefix='aiad-preview-protocol-') as temporary:
            root = Path(temporary).resolve()
            outside = root / 'outside.png'
            outside.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            cases = [b'not a protocol result', self._wire(Check(id='render_generated', level='warn', status='pass'), {}),
                     self._wire(Check(id='render_generated', level='warn', status='pass'), {'png': str(outside)})]
            for wire in cases:
                with self.subTest(wire=wire):
                    result = self._result(root)
                    result.ok, result.status = False, 'failed'
                    failure = Failure(code='INVALID_SOLID', message='original geometry failure')
                    result.failure = failure
                    original_check = Check(id='original', level='error', status='fail')
                    result.checks.append(original_check)
                    result.checks_summary['fail'] = 1
                    process = Mock(returncode=0)
                    process.poll.return_value = 0
                    process.communicate.return_value = wire, b''
                    with patch.object(_preview.sandbox, 'popen', return_value=process):
                        _preview.add_preview(result, timeout_s=60)
                    self.assertFalse(result.ok)
                    self.assertEqual(result.status, 'failed')
                    self.assertIs(result.failure, failure)
                    self.assertIn(original_check, result.checks)
                    self.assertEqual(result.checks_summary, {'pass': 0, 'fail': 1, 'warn': 1, 'skip': 0})
                    self.assertEqual(set(result.artifacts), {'part.brep'})

    def test_no_remaining_time_skips_launch_and_keeps_brep(self):
        with tempfile.TemporaryDirectory(prefix='aiad-preview-protocol-') as temporary:
            result = self._result(Path(temporary))
            with patch.object(_preview.sandbox, 'popen') as launch:
                _preview.add_preview(result, timeout_s=0)
            launch.assert_not_called()
            self.assertTrue(result.ok)
            self.assertEqual(result.checks[-1].id, 'render_timeout')
            self.assertEqual(set(result.artifacts), {'part.brep'})

    def test_pipe_exception_kills_a_running_preview(self):
        with tempfile.TemporaryDirectory(prefix='aiad-preview-protocol-') as temporary:
            result = self._result(Path(temporary))
            process = Mock(returncode=None)
            process.poll.return_value = None
            process.communicate.side_effect = [OSError('fixture pipe error'), (b'', b'drained diagnostic')]
            with patch.object(_preview.sandbox, 'popen', return_value=process), patch('aiad.engine.jobs._kill_worker') as kill:
                _preview.add_preview(result, timeout_s=60)
            kill.assert_called_once_with(process)
            self.assertEqual(process.communicate.call_count, 2)
            self.assertTrue(result.ok)
            self.assertEqual(result.checks[-1].status, 'warn')
            self.assertIn('fixture pipe error', result.checks[-1].actual)
            self.assertIn('drained diagnostic', result.stderr)

    def test_cleanup_exception_preserves_the_built_result(self):
        with tempfile.TemporaryDirectory(prefix='aiad-preview-protocol-') as temporary:
            result = self._result(Path(temporary))
            process = Mock(returncode=None)
            process.poll.return_value = None
            process.communicate.side_effect = OSError('fixture pipe error')
            with patch.object(_preview.sandbox, 'popen', return_value=process), patch('aiad.engine.jobs._kill_worker', side_effect=OSError('fixture cleanup error')):
                _preview.add_preview(result, timeout_s=60)
            self.assertTrue(result.ok)
            self.assertEqual(result.status, 'draft')
            self.assertEqual(set(result.artifacts), {'part.brep'})
            self.assertEqual(result.checks[-1].actual, 'OSError: fixture cleanup error')
            self.assertIn('cleanup failed', result.checks[-1].message)

    @staticmethod
    def _wire(check, artifacts):
        return _preview._MARKER + json.dumps({'check': asdict(check), 'artifacts': artifacts}).encode() + b'\n'

    @staticmethod
    def _result(root):
        brep = root / 'part.brep'
        brep.write_bytes(b'private geometry fixture')
        return BuildResult(ok=True, status='draft', job_dir=root, context=ModelContext(job_dir=root),
                           artifacts={'part.brep': str(brep)}, metrics={'volume': 24},
                           checks=[Check(id='render_not_requested', level='warn', status='skip')],
                           checks_summary={'pass': 0, 'fail': 0, 'warn': 0, 'skip': 1}, scene=SceneManifest())
