"""Fail-closed assembly connectivity evidence and broad-phase performance."""
from __future__ import annotations

from types import SimpleNamespace
import time

import pytest

from build123d import Box, Compound, Pos
from aad.cad import assembly
from aad_quality._checks import record
from aad_quality.spec import load_spec, spec_checks


def check_for(asm, connected):
    ctx = SimpleNamespace(
        kind="assembly",
        units="mm",
        shape=asm.compound(),
        meta={"assembly": asm.record()},
        spec={"units": "mm", "requirements": [{"id": "structure", "connected": connected}]},
        features=[],
        params={},
        metrics={},
    )
    return record(spec_checks(ctx)[0])


def test_connected_fixture_has_measured_joint_and_contact_paths():
    asm = assembly("connected_fixture")
    asm.add("frame", Box(10, 10, 2))
    asm.add("bracket", Box(8, 8, 2), at=Pos(0, 0, 2))
    asm.connect("frame", "bracket", "bolted", tolerance=0.01, fasteners=["M3x8"])

    result = check_for(asm, {"ground": "frame", "allow_floating": [], "tolerance": 0.1})
    assert result["status"] == "pass", result
    assert "Declared joints: 1 of 1 passed" in result["actual"]
    assert "Declared-joint measurements: frame to bracket" in result["actual"]
    assert "Geometric contact: 1 component pairs" in result["actual"]
    assert "Measured touching pairs: frame to bracket" in result["actual"]
    assert "touching only; it does not prove fastening" in result["actual"]


def test_floating_block_fixture_reports_label_without_joint_path():
    asm = assembly("floating_fixture")
    asm.add("frame", Box(10, 10, 2))
    asm.add("bracket", Box(8, 8, 2), at=Pos(0, 0, 2))
    asm.add("floating_sensor", Box(4, 4, 4), at=Pos(30, 0, 0))
    asm.connect("frame", "bracket", "weld", tolerance=0.01)

    result = check_for(asm, {"ground": "frame", "tolerance": 0.1})
    assert result["status"] == "fail", result
    assert "floating_sensor" in result["actual"]
    assert "Components without a validated declared-joint path" in result["actual"]

    allowed = check_for(asm, {"ground": "frame", "allow_floating": ["floating_sensor"], "tolerance": 0.1})
    assert allowed["status"] == "pass", allowed


def test_contact_without_declared_joint_does_not_pass_connected_requirement():
    asm = assembly("touch_only_fixture")
    asm.add("frame", Box(2, 2, 2))
    asm.add("cover", Box(2, 2, 2), at=Pos(0, 0, 2))

    result = check_for(asm, {"ground": "frame", "tolerance": 0.1})
    assert result["status"] == "fail", result
    assert "Geometric contact: 1 component pairs" in result["actual"]
    assert "declared-joint path" in result["actual"]
    assert "touching only; it does not prove fastening" in result["actual"]


def test_joint_gap_fixture_fails_even_when_a_joint_was_declared():
    asm = assembly("gap_fixture")
    asm.add("frame", Box(2, 2, 2))
    asm.add("bracket", Box(2, 2, 2), at=Pos(5, 0, 0))
    asm.connect("frame", "bracket", "bolted", tolerance=0.1)

    result = check_for(asm, {"ground": "frame", "tolerance": 0.2})
    assert result["status"] == "fail", result
    assert "minimum distance" in result["actual"]
    assert "exceeds joint tolerance" in result["actual"]


def test_bad_ground_reference_fails_with_available_component_labels():
    asm = assembly("bad_reference_fixture")
    asm.add("frame", Box(2, 2, 2))

    result = check_for(asm, {"ground": "missing_frame", "tolerance": 0.1})
    assert result["status"] == "fail", result
    assert "ground 'missing_frame' is not an assembly component" in result["actual"]
    assert "frame" in result["actual"]


def test_connected_schema_requires_ground_and_one_nonnegative_tolerance():
    load_spec({"requirements": [{"id": "structure", "connected": {"ground": "frame", "tolerance": "0.1 mm"}}]})
    with pytest.raises(ValueError, match="tolerance"):
        load_spec({"requirements": [{"id": "structure", "connected": {"ground": "frame", "tolerance": -0.1}}]})
    with pytest.raises(ValueError, match="allow_floating"):
        load_spec({"requirements": [{"id": "structure", "connected": {"ground": "frame", "allow_floating": "cover", "tolerance": 0.1}}]})


def test_distance_failure_remains_unknown(monkeypatch):
    import aad_quality.connections as connections

    asm = assembly("unknown_distance_fixture")
    asm.add("frame", Box(2, 2, 2))
    asm.add("bracket", Box(2, 2, 2), at=Pos(0, 0, 2))
    asm.connect("frame", "bracket", "bolted", tolerance=0.1)
    monkeypatch.setattr(connections.spec_geometry, "distance_mm", lambda *_args: (_ for _ in ()).throw(RuntimeError("distance fixture failure")))

    result = check_for(asm, {"ground": "frame", "tolerance": 0.1})
    assert result["status"] == "skip", result
    assert "unknown geometric evidence" in result["actual"].lower()


def test_connection_budget_exhaustion_remains_unknown(monkeypatch):
    import aad_quality.connections as connections

    asm = assembly("timeout_fixture")
    asm.add("frame", Box(2, 2, 2))
    asm.add("bracket", Box(2, 2, 2), at=Pos(0, 0, 2))
    asm.connect("frame", "bracket", "bolted", tolerance=0.1)
    monkeypatch.setattr(connections, "CONNECTION_CHECK_BUDGET_S", 0.0)

    result = check_for(asm, {"ground": "frame", "tolerance": 0.1})
    assert result["status"] == "skip", result
    assert "unknown geometric evidence" in result["actual"].lower()
    assert "budget" in result["actual"].lower()


def test_large_separated_assembly_uses_bounding_box_broad_phase(monkeypatch):
    import aad_quality.connections as connections

    count = 1200
    parts = [Pos(index * 3, 0, 0) * Box(1, 1, 1) for index in range(count)]
    for index, part in enumerate(parts):
        part.label = f"block_{index:04d}"
    names = [f"block_{index:04d}" for index in range(count)]
    ctx = SimpleNamespace(
        kind="assembly",
        units="mm",
        shape=Compound(children=parts),
        meta={"assembly": {"name": "large_synthetic", "components": names, "bom": [], "joints": []}},
        spec={"units": "mm", "requirements": [{"id": "structure", "connected": {"ground": names[0], "tolerance": 0.05}}]},
        features=[],
        params={},
        metrics={},
    )
    calls = 0
    original = connections._bbox_gap_mm

    def counted_bbox_gap(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(connections, "_bbox_gap_mm", counted_bbox_gap)
    started = time.perf_counter()
    result = record(spec_checks(ctx)[0])
    elapsed = time.perf_counter() - started

    assert result["status"] == "fail"
    assert "block_0001" in result["actual"]
    assert "and 1099 more" in result["actual"]
    assert calls < count * 4
    assert elapsed < 15.0
