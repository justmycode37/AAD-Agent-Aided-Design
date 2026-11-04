"""Run the actual S1 worker with installed S5 hooks; no fabricated gate result."""
from aiad.engine.jobs import run_build
from aiad.engine.types import BuildRequest,Check
import pytest


@pytest.mark.parametrize('spec,proven',[
    ({},False),
    ({'requirements':[{'id':'size','bbox':{'x':10}},{'id':'body','solids':1}]},True),
    ({'requirements':[{'id':'size','bbox':{'x':10}},{'id':'future','unimplemented':1}]},False),
    ({'requirements':[{'id':'dup','solids':1},{'id':'dup','solids':1}]},False),
])
def test_worker_uses_real_quality_checks_and_keeps_unproven_builds_draft(tmp_path,spec,proven):
    result=run_build(BuildRequest(source='from build123d import Box\nresult=Box(10,8,4)\n',
        project_dir=tmp_path,job_dir=tmp_path/'job',spec=spec,timeout_s=60))
    assert result.ok,result.failure
    if not proven or any(check.suite=='hard' and check.level=='error' and check.status!='pass' for check in result.checks):
        assert result.status=='draft'
    else:
        assert result.status in {'draft','verified'}
    assert result.requirements_proven is proven,result.checks
    assert all(isinstance(check,Check) for check in result.checks)
    assert not any(check.id=='spec_checks_unavailable' for check in result.checks)
    assert result.artifacts
