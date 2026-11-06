"""Run the actual S1 worker with installed S5 hooks; no fabricated gate result."""
from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest,Check
from aad_quality.spec import subsystem_review
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


@pytest.mark.parametrize("case,expected_ok,expected_status", [
    ("pass", True, "pass"),
    ("missing_part", False, "fail"),
    ("no_evidence", True, "skip"),
    ("skipped_evidence", True, "skip"),
    ("not_modeled", True, "skip"),
])
def test_worker_checks_functional_breakdown_labels_and_evidence(tmp_path, case, expected_ok, expected_status):
    source = (
        "from aad import assembly\n"
        "from build123d import Box, Pos\n"
        "asm = assembly('drive')\n"
        "asm.add('base', Box(10, 10, 2))\n"
        "asm.add('motor', Box(4, 4, 4), at=Pos(0, 0, 4))\n"
        "result = asm\n"
    )
    requirements = [{"id": "body_count", "solids": 2}]
    subsystem = {
        "id": "drive",
        "purpose": "Transmit torque",
        "parts": ["base", "motor"],
        "interfaces": [{
            "id": "mount",
            "kind": "mounting",
            "between": ["base", "motor"],
            "requirements": ["body_count"],
        }],
        "evidence": ["body_count"],
        "status": "modeled",
    }
    if case == "missing_part":
        subsystem["parts"] = ["missing_motor"]
    elif case == "no_evidence":
        subsystem["evidence"] = []
        subsystem["interfaces"][0]["requirements"] = []
    elif case == "skipped_evidence":
        requirements.append({"id": "future", "future_requirement": {"detail": "not implemented"}})
        subsystem["evidence"] = ["future"]
        subsystem["interfaces"][0]["requirements"] = ["future"]
    elif case == "not_modeled":
        subsystem.update({
            "parts": [],
            "interfaces": [],
            "evidence": [],
            "status": "not_modeled",
            "reason": "Motor controller geometry is unavailable",
        })
    spec = {"requirements": requirements, "subsystems": [subsystem]}

    result = run_build(BuildRequest(
        source=source,
        project_dir=tmp_path / case,
        job_dir=tmp_path / f"{case}-job",
        kind="assembly",
        spec=spec,
        timeout_s=60,
    ))

    assert result.ok is expected_ok, result.failure or result.checks
    checks = {check.id: check for check in result.checks}
    assert checks["subsystem:drive"].status == expected_status
    if case == "pass":
        assert checks["subsystem:drive:interface:mount"].status == "pass"
    elif case == "missing_part":
        assert "missing_motor" in checks["subsystem:drive"].actual
    elif case == "no_evidence":
        assert checks["subsystem:drive"].status == "skip"
        assert "no evidence" in checks["subsystem:drive"].actual
        assert checks["subsystem:drive:interface:mount"].status == "skip"
    elif case == "skipped_evidence":
        assert checks["future"].status == "skip"
        assert checks["subsystem:drive:interface:mount"].status == "skip"
    elif case == "not_modeled":
        review = subsystem_review(spec, result.checks, is_assembly=True)
        assert review["not_modeled"] == [{
            "id": "drive",
            "reason": "Motor controller geometry is unavailable",
        }]
