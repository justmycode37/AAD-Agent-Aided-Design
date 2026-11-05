from __future__ import annotations

from types import SimpleNamespace

import pytest

from aad_quality.schema import SpecValidationError, load_spec
from aad_quality.spec import spec_checks


def _ctx(spec):
    return SimpleNamespace(spec=spec, shape=None, units="mm")


@pytest.mark.parametrize(
    "source",
    [
        "units: mm\nunits: in\nrequirements: []\n",
        "requirements:\n  - id: dup\n    solids: 1\n    solids: 2\n",
    "requirements: !!python/object/apply:os.system ['true']\n",
    "requirements: [\n",
    "units: cm\nrequirements: []\n",
    "requirements:\n  - id: x\n    solids: true\n",
    "requirements:\n  - id: x\n    bbox: {x: .nan}\n",
    "requirements:\n  - id: x\n    bbox: {x: .inf}\n",
    "requirements:\n  - id: x\n    level: critical\n    solids: 1\n",
    "requirements:\n  - id: x\n    holes: {count: 2, diameter: 3, unknown: true}\n",
])
def test_load_spec_rejects_unsafe_or_invalid_documents(source):
    with pytest.raises(SpecValidationError):
        load_spec(source)


def test_invalid_yaml_returns_an_error_check():
    checks = spec_checks(_ctx("units: mm\nunits: in\nrequirements: []\n"))
    assert len(checks) == 1
    assert checks[0].suite == "spec"
    assert checks[0].level == "error"
    assert checks[0].status == "skip"
    assert "duplicate key" in checks[0].actual
    assert checks[0].expected and checks[0].fix_hint


def test_duplicate_requirement_ids_make_each_requirement_invalid():
    spec = {
        "requirements": [
            {"id": "same", "solids": 1},
            {"id": "same", "solids": 1},
        ]
    }
    checks = spec_checks(_ctx(spec))
    assert len(checks) == 2
    assert [check.id for check in checks] == ["same", "same"]
    assert all(check.status == "skip" for check in checks)
    assert all("duplicate" in check.actual for check in checks)


def test_empty_or_non_mapping_requirement_gets_diagnostic():
    checks = spec_checks(_ctx({"requirements": [None]}))
    assert len(checks) == 1
    assert checks[0].id == "requirement_1"
    assert checks[0].status == "skip"


def test_unknown_type_is_an_explicit_skip():
    checks = spec_checks(_ctx({"requirements": [{"id": "future", "hole_access": {"reach": 5}}]}))
    assert len(checks) == 1
    assert checks[0].id == "future"
    assert checks[0].status == "skip"
    assert "unknown" in checks[0].actual
    assert checks[0].expected and checks[0].fix_hint


def test_mixed_known_and_unknown_keys_cannot_pass():
    checks = spec_checks(_ctx({"requirements": [{"id": "mixed", "solids": 1, "unexpected": True}]}))
    assert len(checks) == 1
    assert checks[0].status == "skip"
    assert "unsupported field" in checks[0].actual


def test_nonfinite_and_boolean_values_are_invalid_at_requirement_level():
    for bad_value in (True, float("nan"), float("inf")):
        checks = spec_checks(_ctx({"requirements": [{"id": "bad", "bbox": {"x": bad_value}}]}))
        assert len(checks) == 1
        assert checks[0].status == "skip"
        assert checks[0].level == "error"


def test_plus_minus_and_unit_suffixes_are_accepted():
    parsed = load_spec(
        "units: mm\nrequirements:\n"
        "  - id: inch_dimension\n"
        "    bbox: {x: '1 ± 0.01 in'}\n"
    )
    assert parsed["units"] == "mm"
    assert parsed["requirements"][0]["bbox"]["x"] == "1 ± 0.01 in"


def test_min_max_ranges_and_exact_counts_are_accepted():
    parsed = load_spec(
        "requirements:\n"
        "  - id: bounded\n"
        "    bbox: {x: {min: 10, max: 12}}\n"
        "  - id: body_count\n"
        "    solids: 2\n"
    )
    assert len(parsed["requirements"]) == 2


def test_load_spec_accepts_bytes_and_dict():
    assert load_spec(b"requirements: []\n")["units"] == "mm"
    assert load_spec({"requirements": []})["units"] == "mm"
