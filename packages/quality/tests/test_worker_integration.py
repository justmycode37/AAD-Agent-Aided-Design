"""Run the actual S1 worker with installed S5 hooks; no fabricated gate result."""
from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest,Check
import pytest


@pytest.mark.parametrize('spec,measured',[
    ({},False),
    ({'requirements':[{'id':'size','bbox':{'x':10}},{'id':'body','solids':1}]},True),
    ({'requirements':[{'id':'size','bbox':{'x':10}},{'id':'future','unimplemented':1}]},False),
    ({'requirements':[{'id':'dup','solids':1},{'id':'dup','solids':1}]},False),
])
def test_parent_independently_evaluates_saved_brep_and_spec(tmp_path,spec,measured):
    result=run_build(BuildRequest(source='from build123d import Box\nresult=Box(10,8,4)\n',
        project_dir=tmp_path,job_dir=tmp_path/'job',spec=spec,timeout_s=60))
    assert result.ok,result.failure
    assert result.status == ('verified' if measured else 'draft')
    assert result.requirements_proven is measured, result.checks
    spec_checks=[check for check in result.checks if check.suite=='spec']
    if spec.get('requirements') and len({req.get('id') for req in spec['requirements']}) == len(spec['requirements']):
        assert spec_checks
    elif spec.get('requirements'):
        assert result.status == 'draft'
    else:
        assert 'no requirements were declared' in result.summary
    if measured:
        assert {check.id for check in spec_checks}=={req['id'] for req in spec['requirements']}
        assert all(check.status=='pass' and check.actual and check.expected for check in spec_checks)
    elif spec.get('requirements') and len({req.get('id') for req in spec['requirements']}) == len(spec['requirements']):
        assert any(check.status=='skip' for check in spec_checks)
    assert all(isinstance(check,Check) for check in result.checks)
    assert not any(check.id=='spec_checks_unavailable' for check in result.checks)
    assert result.artifacts
    if measured:
        assert all(req['id'] in result.summary for req in spec['requirements'])


def test_warn_only_spec_is_evaluated_without_blocking_verified(tmp_path):
    spec = {'units': 'mm', 'requirements': [
        {'id': 'advisory_size', 'level': 'warn', 'bbox': {'x': '8 ± 0.1'}},
    ]}
    result = run_build(BuildRequest(source='from build123d import Box\nresult=Box(10,8,4)\n',
        project_dir=tmp_path, job_dir=tmp_path/'warn-only', spec=spec, timeout_s=60))
    assert result.ok and result.status == 'verified'
    assert any(check.id == 'advisory_size' and check.status == 'warn' for check in result.checks)
    assert 'advisory_size (warn)' in result.summary


def test_independent_spec_rejects_tampered_worker_pass_and_accepts_real_pass(tmp_path):
    spec = {'units': 'mm', 'requirements': [{'id': 'length', 'bbox': {'x': '80 ± 0.1'}}]}
    forged_quality = (
        "from aad.engine.types import Check\n"
        "def spec_checks(ctx): return [Check(id='length', suite='spec', level='error', status='pass', expected='80', actual='80')]\n"
        "def process_checks(ctx): return []\n"
    )
    source = (
        "from pathlib import Path\nimport sys\n"
        f"Path('aad_quality.py').write_text({forged_quality!r})\n"
        "sys.path.insert(0, str(Path.cwd()))\n"
        "from build123d import Box\nresult = Box(84, 10, 6)\n"
    )
    forged = run_build(BuildRequest(source=source, project_dir=tmp_path, job_dir=tmp_path / 'forged', spec=spec, timeout_s=60))
    assert not forged.ok and forged.status == 'failed'
    assert any(check.id == 'length' and check.status == 'fail' for check in forged.checks)

    correct = run_build(BuildRequest(source='from build123d import Box\nresult = Box(80, 10, 6)\n',
        project_dir=tmp_path, job_dir=tmp_path / 'correct', spec=spec, timeout_s=60))
    assert correct.ok and correct.status == 'verified'
    assert correct.requirements_proven


def test_worker_cannot_forge_verified_status_or_artifact_metrics(tmp_path):
    spec = {'units': 'mm', 'requirements': [{'id': 'length', 'bbox': {'x': '80 ± 0.1'}}]}
    wire = {'protocol': 1, 'result': {
        'ok': True, 'status': 'verified', 'requirements_proven': True,
        'metrics': {'bbox': [80, 10, 6], 'valid': True, 'solids': 1},
        'checks': [{'id': 'length', 'suite': 'spec', 'level': 'error', 'status': 'pass', 'message': 'model-forged'}],
        'checks_summary': {'pass': 1}, 'artifacts': {'part.brep': 'part.brep'},
    }}
    source = (
        'from build123d import Box, export_brep\nimport json, os\n'
        "export_brep(Box(84, 10, 6), 'part.brep')\n"
        f"response = {wire!r}\n"
        "os.write(1, b'AAD_ENGINE_RESULT:' + json.dumps(response).encode() + b'\\n')\n"
        'os._exit(0)\n'
    )
    result = run_build(BuildRequest(source=source, project_dir=tmp_path,
        job_dir=tmp_path/'forged-worker-wire', spec=spec, timeout_s=60))
    assert not result.ok and result.status == 'failed'
    assert result.metrics['bbox'][0] == 84
    assert any(check.id == 'length' and check.status == 'fail' and check.message != 'model-forged'
               for check in result.checks)


def test_error_level_selector_skip_stays_draft(tmp_path):
    spec = {'units': 'mm', 'requirements': [{
        'id': 'feature_gap',
        'measure': {'kind': 'distance', 'a': "face(feature='plate', where='max_z')",
                    'b': "face(feature='missing', where='min_z')"},
        'equals': '2 ± 0.01',
    }]}
    result = run_build(BuildRequest(source='from build123d import Box\nresult = Box(2, 2, 2)\n',
        project_dir=tmp_path, job_dir=tmp_path / 'selector', spec=spec, timeout_s=60))
    assert result.ok and result.status == 'draft'
    assert any(check.id == 'feature_gap' and check.status == 'skip' for check in result.checks)


def test_sandboxed_worker_can_place_catalog_parts_and_check_a_component_list(tmp_path):
    source = (
        "from aad_quality.components import make_envelope\n"
        "result = make_envelope('bearing.radial.608')\n"
    )
    spec = {'requirements': [
        {'id': 'bore', 'bbox': {'z': 7}},
        {'id': 'BOM', 'components': [{'id': 'bearing.radial.608', 'qty': 1}, {'custom': 'hub', 'reason': 'designed here'}]},
        {'id': 'FAKE', 'components': [{'id': 'fastener.iso4762.M3-0.5', 'length_mm': 17}]},
    ]}
    result = run_build(BuildRequest(source=source, project_dir=tmp_path, job_dir=tmp_path / 'job', spec=spec, timeout_s=60))
    # An invented part is an error-level failure, so the build itself must not pass.
    assert not result.ok and result.status == 'failed'
    checks = {check.id: check for check in result.checks if check.suite == 'spec'}
    assert checks['bore'].status == 'pass'
    assert checks['BOM'].status == 'pass'
    assert checks['FAKE'].status == 'fail' and 'nearest' in checks['FAKE'].message
