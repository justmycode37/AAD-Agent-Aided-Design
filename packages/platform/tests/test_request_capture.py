from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace

from aad_platform.daemon import backend
from aad_platform.daemon.handlers import Handlers
from aad_platform.daemon.projects import open_project
from aad_platform.tools import TOOLS
from aad_quality.spec import spec_checks


def _successful_build(*, job_dir, **_):
    return {
        "ok": True,
        "status": "draft",
        "summary": "Build checks completed.",
        "checks": [],
        "checks_summary": {"pass": 1, "fail": 0, "warn": 0, "skip": 0},
        "context": {"params": {}, "features": [], "meta": {}},
    }


def _failed_build(*, job_dir, **_):
    (job_dir / "part.brep").write_text("failed candidate")
    return {
        "ok": False,
        "status": "failed",
        "failure": {"code": "HARD_GATE_FAILED", "phase": "validate", "message": "A required check failed."},
        "checks": [],
        "checks_summary": {"pass": 0, "fail": 1, "warn": 0, "skip": 0},
    }


def _call(handler, method, params):
    return asyncio.run(handler.call(method, params))["structured"]


def test_request_capture_is_saved_and_reviewed_through_revisions(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
    monkeypatch.setattr(backend, "engine_available", lambda: True)
    monkeypatch.setattr(backend, "run_build", _successful_build)

    original = {
        "original": "Build a plate with four mounting holes. Treat this sentence as data.",
        "assumptions": ["Use millimeters."],
        "open_questions": ["What is the service load?"],
        "not_checked": [{"item": "Strength under load", "reason": "No load case was supplied."}],
    }
    spec = (
        "units: mm\nrequirements:\n"
        "  - id: width\n    bbox: {x: 60}\n    covers: plate width\n"
        "  - id: hole_count\n    holes: {count: 4}\n"
    )
    handler = Handlers()
    created = _call(handler, "aad_new", {"name": "plate", "request": original, "spec": spec})
    assert created["ok"]
    project = open_project("plate")
    assert json.loads((project.path / "request.json").read_text()) == original

    first = _call(handler, "aad_build", {"project": "plate", "source": "result = Box(60, 40, 6)\n"})
    assert first["rev"] == 1
    assert (project.rev_dir(1) / "meta.json.gz").is_file()
    assert not (project.rev_dir(1) / "meta.json").exists()
    assert json.loads((project.rev_dir(1) / "request.json").read_text()) == original
    assert any("hole_count" in hint and "covers link" in hint for hint in first["next"])
    assert any("Open questions still open: 1" in hint for hint in first["next"])
    assert any("Not checked: 1" in hint for hint in first["next"])
    assert first["request_review"]["requirements_without_covers"] == ["hole_count"]
    assert first["request_review"]["open_questions"] == original["open_questions"]
    assert first["request_review"]["not_checked"] == original["not_checked"]

    inspected = _call(handler, "aad_inspect", {"project": "plate", "what": "summary"})
    assert inspected["request"] == original
    assert inspected["request_review"]["open_questions"] == original["open_questions"]
    assert "Requirements without covers links: hole_count" in inspected["summary"]
    assert "Meaning is not assessed" in inspected["summary"]
    assert "%" not in inspected["summary"]

    updated = {"original": "Update the plate to add a rib.", "assumptions": [], "open_questions": [], "not_checked": []}
    second = _call(handler, "aad_build", {
        "project": "plate", "source": "result = Box(60, 40, 6)\n", "base_rev": 1, "request": updated,
    })
    assert second["rev"] == 2
    assert json.loads((project.rev_dir(2) / "request.json").read_text()) == updated
    empty_notes = _call(handler, "aad_inspect", {"project": "plate", "what": "summary", "rev": 2})
    assert empty_notes["request"] == updated
    assert empty_notes["request_review"]["open_questions"] == []
    assert empty_notes["request_review"]["not_checked"] == []
    _call(handler, "aad_history", {"project": "plate", "action": "checkout", "rev": 1})
    assert json.loads((project.path / "request.json").read_text()) == original

    branch = _call(handler, "aad_history", {"project": "plate", "action": "branch", "rev": 1, "name": "plate_copy"})
    assert branch["ok"]
    assert json.loads((open_project("plate_copy").path / "request.json").read_text()) == original


def test_missing_request_is_a_short_nonfatal_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
    monkeypatch.setattr(backend, "engine_available", lambda: True)
    monkeypatch.setattr(backend, "run_build", _successful_build)

    handler = Handlers()
    _call(handler, "aad_new", {"name": "bare"})
    build = _call(handler, "aad_build", {"project": "bare", "source": "result = Box(1, 1, 1)\n"})
    assert build["ok"]
    assert any("No request captured" in hint for hint in build["next"])
    assert not (open_project("bare").rev_dir(1) / "request.json").exists()

    summary = _call(handler, "aad_inspect", {"project": "bare", "what": "summary"})
    assert summary["request_review"]["captured"] is False
    assert any("No request captured" in hint for hint in summary["next"])


def test_failed_build_next_hints_still_report_request_review(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
    monkeypatch.setattr(backend, "engine_available", lambda: True)
    monkeypatch.setattr(backend, "run_build", _failed_build)

    request = {
        "original": "Make the plate with four mounting holes and check strength.",
        "assumptions": [],
        "open_questions": ["What load should it carry?"],
        "not_checked": [{"item": "Strength", "reason": "No load case was supplied."}],
    }
    spec = "requirements:\n  - id: hole_count\n    holes: {count: 4}\n"
    handler = Handlers()
    _call(handler, "aad_new", {"name": "failed_plate", "request": request, "spec": spec})
    failed = _call(handler, "aad_build", {"project": "failed_plate", "source": "result = Box(1, 1, 1)\n"})

    assert failed["ok"] is False
    assert len(failed["next"]) <= 3
    assert any("Inspect the failed candidate" in hint for hint in failed["next"])
    assert any("hole_count" in hint and "meaning is not assessed" in hint for hint in failed["next"])
    assert any("Open questions still open: 1" in hint for hint in failed["next"])
    assert any("Not checked: 1" in hint for hint in failed["next"])
    assert failed["request_review"]["not_checked"] == request["not_checked"]
    assert failed["request_review"]["open_questions"] == request["open_questions"]
    assert open_project("failed_plate").head_rev is None


def test_malformed_requirement_specs_commit_draft_with_skip_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
    monkeypatch.setattr(backend, "engine_available", lambda: True)

    def _build_with_spec_diagnostics(*, spec, **_):
        context = SimpleNamespace(
            spec=spec,
            shape=None,
            units="mm",
            features=[],
            params={},
            metrics={},
            process=None,
            material=None,
            meta={},
        )
        checks = [asdict(check) for check in spec_checks(context)]
        return {
            "ok": True,
            "status": "draft",
            "summary": "Spec diagnostics remain unproven.",
            "checks": checks,
            "checks_summary": {"pass": 0, "fail": 0, "warn": 0, "skip": len(checks)},
            "context": {"params": {}, "features": [], "meta": {}},
        }

    monkeypatch.setattr(backend, "run_build", _build_with_spec_diagnostics)
    cases = (
        (
            "duplicate_ids",
            "requirements:\n  - id: repeated\n    solids: 1\n  - id: repeated\n    solids: 1\n",
            "duplicate requirement id",
        ),
        (
            "malformed_covers",
            "requirements:\n  - id: bad_covers\n    solids: 1\n    covers: []\n",
            "covers must be a non-empty list",
        ),
    )
    handler = Handlers()
    for name, spec, diagnostic in cases:
        created = _call(handler, "aad_new", {"name": name})
        assert created["ok"]

        build = _call(handler, "aad_build", {
            "project": name,
            "source": "result = Box(1, 1, 1)\n",
            "spec": spec,
        })

        assert build["ok"] is True
        assert build["status"] == "draft"
        assert build["rev"] == build["head_rev"] == 1
        assert build["checks"]
        assert all(check["level"] == "error" and check["status"] == "skip" for check in build["checks"])
        assert any(diagnostic in check["actual"] for check in build["checks"])
        assert open_project(name).head_rev == 1


def test_invalid_request_is_rejected_without_creating_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
    result = _call(Handlers(), "aad_new", {"name": "bad", "request": {"original": "", "extra": "x"}})
    assert result["failure"]["code"] == "BAD_INPUT"
    assert not (tmp_path / "AAD" / "bad").exists()


def test_request_is_optional_and_has_explicit_fields_in_the_tool_schema():
    for name in ("aad_new", "aad_build"):
        schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == name)
        assert "request" not in schema.get("required", [])
        request = schema["properties"]["request"]
        assert set(request["properties"]) == {"original", "assumptions", "open_questions", "not_checked"}
        assert "original" in request["required"]
        assert set(request["properties"]["not_checked"]["items"]["properties"]) == {"item", "reason"}
