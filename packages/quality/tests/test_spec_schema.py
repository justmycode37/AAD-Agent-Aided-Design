from __future__ import annotations

import json
from types import SimpleNamespace

from build123d import Box
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
    "requirements:\n  - id: x\n    interference: {volume_threshold_mm3: -1}\n",
    "requirements:\n  - id: x\n    interference: {unknown: true}\n",
    "requirements:\n  - id: ref\n    matches_reference: {path: assets/ref.step, f_score: {min: true}}\n",
    "requirements:\n  - id: ref\n    matches_reference: {path: assets/ref.step, f_score: {min: 1.2}}\n",
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


def test_interference_requirement_uses_the_optional_volume_threshold():
    ctx = _ctx({
        "requirements": [{
            "id": "clearance",
            "interference": {"volume_threshold_mm3": 0.025},
        }]
    })
    ctx.shape = Box(2, 2, 2)

    parsed = load_spec(ctx.spec)
    checks = spec_checks(SimpleNamespace(spec=parsed, shape=ctx.shape, units="mm"))

    assert len(checks) == 1
    assert checks[0].id == "clearance"
    assert checks[0].status == "pass"
    assert json.loads(checks[0].actual)["volume_threshold_mm3"] == 0.025


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


def test_matches_reference_spec_has_finite_unitless_threshold():
    parsed = load_spec(
        "requirements:\n"
        "  - id: reference_shape\n"
        "    matches_reference:\n"
        "      path: assets/ref.ply\n"
        "      f_score: {min: 0.99, tau_pct_of_diagonal: 0.1}\n"
    )
    assert parsed["requirements"][0]["matches_reference"]["f_score"]["min"] == 0.99


def test_angle_measure_requires_two_selectors_and_accepts_degree_ranges():
    parsed = load_spec(
        "requirements:\n"
        "  - id: corner\n"
        "    measure: {kind: angle, a: \"face(where='max_z')\", b: \"face(where='max_x')\"}\n"
        "    equals: '90 ± 0.5'\n"
    )
    assert parsed["requirements"][0]["measure"]["kind"] == "angle"
    with pytest.raises(SpecValidationError, match="angle requires a non-empty b selector"):
        load_spec({"requirements": [{"id": "corner", "measure": {"kind": "angle", "a": "face()"}, "equals": 90}]})


def test_position_schema_accepts_feature_and_hole_selectors_and_rejects_ambiguous_inputs():
    parsed = load_spec({"requirements": [
        {"id": "feature_origin", "position": {"selector": "vertex(feature='boss', nth=0)", "target": [0, 0, 0], "tolerance": "0.1 mm"}},
        {"id": "hole_center", "position": {"hole": "face(feature='holes', kind='cylinder')", "target": [0, 0, 0], "tolerance": 0.05}},
    ]})
    assert len(parsed["requirements"]) == 2
    for position in (
        {"selector": "face()", "hole": "face()", "target": [0, 0, 0], "tolerance": 0.1},
        {"selector": "face()", "target": [0, 0], "tolerance": 0.1},
        {"selector": "face()", "target": [0, 0, 0], "tolerance": -0.1},
    ):
        with pytest.raises(SpecValidationError):
            load_spec({"requirements": [{"id": "bad_position", "position": position}]})


def test_fit_schema_accepts_clearance_or_volume_range_and_rejects_both():
    parsed = load_spec({"requirements": [
        {"id": "gap", "fit": {"a": "shaft", "b": "bearing", "clearance": {"min": "0.02 mm", "max": "0.08 mm"}}},
        {"id": "press", "fit": {"a": "insert", "b": "housing", "interference": {"min": 1, "max": 3}}},
    ]})
    assert len(parsed["requirements"]) == 2
    with pytest.raises(SpecValidationError, match="exactly one of clearance or interference"):
        load_spec({"requirements": [{"id": "bad_fit", "fit": {
            "a": "shaft", "b": "bearing", "clearance": {"min": 0}, "interference": {"max": 1},
        }}]})
    with pytest.raises(SpecValidationError, match="wrong unit exponent"):
        load_spec({"requirements": [{"id": "bad_volume", "fit": {
            "a": "shaft", "b": "bearing", "interference": {"max": "1 mm^2"},
        }}]})
    with pytest.raises(SpecValidationError, match="bounds must be non-negative"):
        load_spec({"requirements": [{"id": "negative_gap", "fit": {
            "a": "shaft", "b": "bearing", "clearance": {"min": -0.1},
        }}]})


def test_covers_is_an_optional_link_and_is_validated():
    parsed = load_spec({"requirements": [
        {"id": "size", "bbox": {"x": 10}, "covers": "the requested width"},
        {"id": "holes", "holes": {"count": 4}, "covers": ["mounting pattern", "fasteners"]},
    ]})
    assert parsed["requirements"][0]["covers"] == "the requested width"
    with pytest.raises(SpecValidationError):
        load_spec({"requirements": [{"id": "size", "bbox": {"x": 10}, "covers": []}]})


def test_subsystems_accept_labeled_parts_interfaces_and_evidence_links():
    parsed = load_spec({"requirements": [{"id": "mount", "solids": 2}], "subsystems": [
        {
            "id": "drive",
            "purpose": "Transmit torque to the output",
            "parts": ["motor", "shaft"],
            "interfaces": [{
                "id": "coupling",
                "kind": "shaft_coupling",
                "between": ["motor", "shaft"],
                "requirements": ["mount"],
            }],
            "evidence": ["mount"],
            "status": "modeled",
        },
        {
            "id": "controller",
            "purpose": "Switch motor current",
            "parts": [],
            "interfaces": [],
            "evidence": [],
            "status": "not_modeled",
            "reason": "No geometry or electrical evidence is available",
        },
    ]})
    assert len(parsed["subsystems"]) == 2
    assert parsed["subsystems"][0]["interfaces"][0]["between"] == ["motor", "shaft"]


@pytest.mark.parametrize("subsystem", [
    {"id": "unmodeled", "purpose": "A system", "parts": [], "interfaces": [], "evidence": [], "status": "not_modeled"},
    {"id": "bad_status", "purpose": "A system", "parts": [], "interfaces": [], "evidence": [], "status": "estimated"},
    {"id": "bad_interface", "purpose": "A system", "parts": [], "interfaces": [{"id": "joint", "kind": "mount", "between": ["one"], "requirements": []}], "evidence": [], "status": "modeled"},
])
def test_subsystem_schema_rejects_missing_reason_or_malformed_interface(subsystem):
    with pytest.raises(SpecValidationError):
        load_spec({"requirements": [], "subsystems": [subsystem]})


def test_subsystem_text_and_encoded_payload_have_capture_style_limits():
    base = {"id": "x", "purpose": "ok", "parts": [], "interfaces": [], "evidence": [], "status": "modeled"}
    with pytest.raises(SpecValidationError, match="2000 characters"):
        load_spec({"requirements": [], "subsystems": [{**base, "purpose": "x" * 2001}]})
    many = [{**base, "id": f"subsystem_{index}", "purpose": "x" * 2000} for index in range(40)]
    with pytest.raises(SpecValidationError, match="64 KiB"):
        load_spec({"requirements": [], "subsystems": many})


def test_subsystem_evidence_ids_use_the_identifier_limit():
    base = {"id": "drive", "purpose": "turn", "parts": ["motor"], "interfaces": [], "status": "modeled"}
    with pytest.raises(SpecValidationError):
        load_spec({"requirements": [], "subsystems": [{**base, "evidence": ["x" * 201]}]})


def test_subsystem_review_withholds_oversized_text():
    from aad_quality.spec import subsystem_review

    subsystem = {
        "id": "drive", "purpose": "turn", "parts": [], "interfaces": [], "evidence": [],
        "status": "not_modeled", "reason": "x" * 70_000,
    }
    review = subsystem_review({"subsystems": [subsystem]}, [], is_assembly=True)
    assert "invalid" in review["hint"]
    assert "x" * 2_001 not in json.dumps(review)


@pytest.mark.parametrize("declared, expected", [("modeled", "fail"), ("not_modeled", "skip")])
def test_interface_with_an_unknown_endpoint_is_not_a_pass(declared, expected):
    from aad_quality.spec import subsystem_checks

    subsystem = {
        "id": "drive", "purpose": "turn", "parts": ["base"], "evidence": ["count"], "status": declared,
        "reason": "planned",
        "interfaces": [{"id": "mount", "kind": "bolted", "between": ["base", "ghost"], "requirements": ["count"]}],
    }
    ctx = SimpleNamespace(
        shape=None, features=[SimpleNamespace(name="base", intent=None)], assembly=None, meta={}, units="mm",
    )
    checks = subsystem_checks({"subsystems": [subsystem]}, [{"id": "count", "status": "pass"}], ctx)
    interface = next(check for check in checks if "mount" in (check["id"] if isinstance(check, dict) else check.id))
    status = interface["status"] if isinstance(interface, dict) else interface.status
    assert status == expected
