"""Check that the artifact oracle rejects plausible valid-but-wrong geometry."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

from build123d import BuildPart, Box, GridLocations, Hole, export_step

module_spec=importlib.util.spec_from_file_location('audit',Path(__file__).with_name('audit.py'))
module=importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(module)

class ArtifactOracleTests(unittest.TestCase):
    def setUp(self):
        (Path(__file__).parent/'.cache').mkdir(exist_ok=True)

    def test_valid_but_wrong_plates_are_rejected(self):
        for width, spacing, diameter, expected in [(60,46,4.4,True),(61,46,4.4,False),(60,44,4.4,False),(60,46,5,False)]:
            with self.subTest(width=width,spacing=spacing,diameter=diameter):
                with BuildPart() as bp:
                    Box(width,40,6)
                    with GridLocations(spacing,26,2,2):
                        Hole(diameter/2)
                with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'.cache') as temp:
                    target=Path(temp)/'plate.step'
                    export_step(bp.part,str(target))
                    report=module.audit(target,'plate')
                    self.assertTrue(report['checks']['brep_valid'])
                    self.assertEqual(report['requirements_pass'],expected)

    def test_blocked_counterbore_and_missing_counterbore_mutant(self):
        source=(Path(__file__).parent/'tasks/blocked_counterbore.py').read_text()
        for missing in (False,True):
            with self.subTest(missing_counterbore=missing):
                code=source.replace('CounterBoreHole(2.2, 4.5, 3, depth=6)', 'Hole(2.2, depth=6)') if missing else source
                namespace={}
                exec(compile(code,'blocked_fixture','exec'),namespace)
                with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'.cache') as temp:
                    target=Path(temp)/'blocked.step'
                    export_step(namespace['result'],str(target))
                    checks=module.audit(target,'blocked_counterbore')['checks']
                    self.assertTrue(checks['brep_valid'])
                    self.assertFalse(checks['coaxial_access'])
                    self.assertEqual(checks['counterbore_9_0'],not missing)
                    self.assertEqual(checks['counterbore_depth_3_0'],not missing)
                    self.assertEqual(all(v for k,v in checks.items() if k!='coaxial_access'),not missing)

    def test_incomplete_or_stale_manifests_are_rejected(self):
        for reason in ('incomplete','artifact','source'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory(dir=Path(__file__).parent/'.cache') as temp:
                base=Path(temp)
                artifacts=base/'outputs/step'
                artifacts.mkdir(parents=True)
                target=artifacts/'plate.step'
                target.write_bytes(b'not a STEP; validation must stop before geometry import')
                source=Path(__file__).parent/'tasks/plate.py'
                manifest={'complete':reason!='incomplete','run_id':'test','trials':[{
                    'task':'plate','repeat':1,'step':'outputs/step/plate.step',
                    'step_sha256':'0'*64 if reason=='artifact' else hashlib.sha256(target.read_bytes()).hexdigest(),
                    'source_sha256':'0'*64 if reason=='source' else hashlib.sha256(source.read_bytes()).hexdigest()}]}
                (base/'results.json').write_text(json.dumps(manifest))
                result=subprocess.run([sys.executable,str(Path(__file__).with_name('audit.py')),str(artifacts),'--out',str(base/'audit.json')],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                expected={'incomplete':'Run is incomplete','artifact':'STEP hash differs','source':'Task source differs'}[reason]
                self.assertIn(expected,result.stderr)
                self.assertFalse((base/'audit.json').exists())

if __name__=='__main__':unittest.main()
