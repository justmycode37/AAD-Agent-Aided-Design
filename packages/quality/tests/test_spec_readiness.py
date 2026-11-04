from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiad_quality._checks import make_check
from aiad_quality.spec import spec_checks, verification_ready


def _check(check_id, suite, status, *, level="error", expected="measured value", actual="10 mm"):
    return make_check(
        id=check_id,
        suite=suite,
        level=level,
        status=status,
        message="test evidence",
        expected=expected,
        actual=actual,
        fix_hint="adjust the model",
    )


def _valid_checks(spec):
    from build123d import Box

    checks = spec_checks(SimpleNamespace(spec=spec, shape=Box(10, 8, 4), units="mm"))
    hard = [_check("hard:valid", "hard", "pass")]
    return checks, hard


def test_verification_ready_requires_passed_hard_gate_and_exact_coverage():
    spec = {
        "requirements": [
            {"id": "size", "bbox": {"x": "10 ± 0.01"}},
            {"id": "body", "solids": 1},
        ]
    }
    checks, hard = _valid_checks(spec)
    assert all(check.status == "pass" for check in checks)
    assert verification_ready(spec, checks, hard)
    assert not verification_ready(spec, checks[:1], hard)
    assert not verification_ready(spec, checks, [])
    assert not verification_ready(spec, checks, [_check("hard:valid", "hard", "fail")])


def test_verification_ready_rejects_unknown_or_skipped_error_requirement():
    spec = {
        "requirements": [
            {"id": "size", "bbox": {"x": 10}},
            {"id": "future", "hole_access": {"reach": 5}},
        ]
    }
    checks, hard = _valid_checks(spec)
    assert [check.status for check in checks] == ["pass", "skip"]
    assert not verification_ready(spec, checks, hard)


def test_verification_ready_rejects_duplicate_or_extra_spec_checks():
    spec = {"requirements": [{"id": "size", "bbox": {"x": 10}}]}
    checks, hard = _valid_checks(spec)
    assert not verification_ready(spec, checks + [checks[0]], hard)
    assert not verification_ready(spec, checks + [_check("other", "spec", "pass")], hard)


def test_verification_ready_rejects_error_evidence_from_any_suite():
    spec = {"requirements": [{"id": "size", "bbox": {"x": 10}}]}
    checks, hard = _valid_checks(spec)
    process_failure = _check("process:wall", "process", "skip", actual="unknown")
    assert not verification_ready(spec, checks + [process_failure], hard)


def test_verification_ready_rejects_invalid_empty_and_duplicate_id_specs():
    hard = [_check("hard:valid", "hard", "pass")]
    assert not verification_ready({"requirements": []}, [], hard)
    assert not verification_ready(
        {"requirements": [{"id": "same", "solids": 1}, {"id": "same", "solids": 1}]},
        [],
        hard,
    )
    assert not verification_ready("requirements: [", [], hard)


def test_verification_ready_requires_measured_spec_pass_with_actual_and_expected():
    spec = {"requirements": [{"id": "size", "bbox": {"x": 10}}]}
    hard = [_check("hard:valid", "hard", "pass")]
    no_actual = [_check("size", "spec", "pass", actual="unknown")]
    no_expected = [_check("size", "spec", "pass", expected="")]
    assert not verification_ready(spec, no_actual, hard)
    assert not verification_ready(spec, no_expected, hard)


def test_unproven_warning_requirement_still_withholds_readiness():
    spec = {
        "requirements": [
            {"id": "size", "bbox": {"x": 10}},
            {"id": "future", "level": "warn", "hole_access": {"reach": 5}},
        ]
    }
    checks, hard = _valid_checks(spec)
    assert [check.status for check in checks] == ["pass", "skip"]
    assert not verification_ready(spec, checks, hard)


@pytest.mark.parametrize('actual',[None,'','unknown','UNKNOWN wall thickness','query exception',1])
def test_passing_process_status_cannot_hide_unproven_measurement(actual):
    spec={'process':'fdm_pla','requirements':[{'id':'size','bbox':{'x':10}}]}
    checks,hard=_valid_checks(spec)
    process=_check('process.fdm_pla.wall','process','pass',actual=actual)
    assert not verification_ready(spec,checks+[process],hard)


def test_context_process_requires_matching_evidence_even_without_spec_process():
    spec={'requirements':[{'id':'size','bbox':{'x':10}}]}
    checks,hard=_valid_checks(spec)
    assert not verification_ready(spec,checks,hard,process='fdm_pla')
    process=_check('process.fdm_pla.wall','process','pass',actual='minimum sampled thickness 10 mm')
    assert verification_ready(spec,checks+[process],hard,process='fdm_pla')
    assert not verification_ready(spec,checks+[process],hard,process='sla')
    assert not verification_ready(spec,checks+[process],hard,process='unknown_profile')
    spec['process']='sla'
    assert not verification_ready(spec,checks+[process],hard,process='fdm_pla')
