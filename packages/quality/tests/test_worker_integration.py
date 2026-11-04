"""Run the actual S1 worker with installed S5 hooks; no fabricated gate result."""
from aiad.engine.jobs import run_build
from aiad.engine.types import BuildRequest,Check
import pytest


@pytest.mark.parametrize('spec,measured',[
    ({},False),
    ({'requirements':[{'id':'size','bbox':{'x':10}},{'id':'body','solids':1}]},True),
    ({'requirements':[{'id':'size','bbox':{'x':10}},{'id':'future','unimplemented':1}]},False),
    ({'requirements':[{'id':'dup','solids':1},{'id':'dup','solids':1}]},False),
])
def test_worker_uses_real_quality_checks_without_attesting_their_results(tmp_path,spec,measured):
    result=run_build(BuildRequest(source='from build123d import Box\nresult=Box(10,8,4)\n',
        project_dir=tmp_path,job_dir=tmp_path/'job',spec=spec,timeout_s=60))
    assert result.ok,result.failure
    # S1's parent clears model-process proof claims until independent
    # validation exists. Passing worker measurements are still useful, but
    # cannot promote the revision or become a trusted proof bit.
    assert result.status=='draft'
    assert result.requirements_proven is False,result.checks
    spec_checks=[check for check in result.checks if check.suite=='spec']
    assert spec_checks
    if measured:
        assert {check.id for check in spec_checks}=={req['id'] for req in spec['requirements']}
        assert all(check.status=='pass' and check.actual and check.expected for check in spec_checks)
    else:
        assert any(check.status=='skip' for check in spec_checks)
    assert all(isinstance(check,Check) for check in result.checks)
    assert not any(check.id=='spec_checks_unavailable' for check in result.checks)
    assert result.artifacts
