from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aad_platform.daemon import backend
from aad_platform.daemon.handlers import Handlers
from aad_platform.daemon.projects import open_project


def _call(handler, method, params):
    return asyncio.run(handler.call(method, params))["structured"]


def test_provenance_survives_revision_inspection_diff_and_export(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
    monkeypatch.setattr(backend, "engine_available", lambda: True)

    def build(*, params, job_dir, **_):
        override = params.get("spacing")
        if override is None:
            value, basis, source, note = 89.0, "published", "https://example.org/spec", "shaft spacing"
        elif isinstance(override, dict):
            value = override["value"]
            basis = override.get("basis", "assumed")
            source = override.get("source")
            note = override.get("note")
        else:
            value, basis, source, note = override, "assumed", None, None
        return {
            "ok": True,
            "status": "draft",
            "summary": "Build completed.",
            "checks": [],
            "checks_summary": {"pass": 0, "fail": 0, "warn": 0, "skip": 0},
            "context": {
                "params": {"spacing": value, "clearance": 2.0},
                "param_definitions": {
                    "spacing": {
                        "value": value, "min": None, "max": None, "unit": "mm",
                        "basis": basis, "source": source, "note": note,
                    },
                    "clearance": {
                        "value": 2.0, "min": None, "max": None, "unit": "mm",
                        "basis": "unknown", "source": None, "note": None,
                    },
                },
                "features": [],
                "meta": {},
            },
        }

    monkeypatch.setattr(backend, "run_build", build)
    handler = Handlers()
    _call(handler, "aad_new", {"name": "gearbox"})
    first = _call(handler, "aad_build", {"project": "gearbox", "source": "result = Box(4, 4, 4)\n"})
    assert first["rev"] == 1

    project = open_project("gearbox")
    saved = json.loads((project.rev_dir(1) / "meta.json").read_text())
    assert saved["param_definitions"]["spacing"]["basis"] == "published"
    assert saved["param_definitions"]["spacing"]["source"] == "https://example.org/spec"

    inspected = _call(handler, "aad_inspect", {"project": "gearbox", "what": "params"})
    assert inspected["params"] == {"spacing": 89.0, "clearance": 2.0}
    assert inspected["param_definitions"]["spacing"]["note"] == "shaft spacing"
    assert inspected["param_basis_counts"] == {
        "published": 1, "measured": 0, "fitted": 0, "assumed": 0, "derived": 0, "unknown": 1,
    }
    assert inspected["params_without_source"] == ["clearance"]
    assert "does not verify" in inspected["provenance_notice"]

    summary = _call(handler, "aad_inspect", {"project": "gearbox", "what": "summary"})
    assert "published=1" in summary["summary"]
    assert "parameters lacking a source: clearance" in summary["summary"]

    second = _call(handler, "aad_build", {"project": "gearbox", "base_rev": 1, "params": {"spacing": 90.0}})
    assert second["rev"] == 2
    latest = json.loads((project.rev_dir(2) / "meta.json").read_text())
    assert latest["param_definitions"]["spacing"]["basis"] == "assumed"
    assert latest["param_definitions"]["spacing"]["source"] is None

    diff = _call(handler, "aad_history", {"project": "gearbox", "action": "diff", "rev": 2, "against": 1})
    assert diff["diff"]["params"]["spacing"] == {"from": 89.0, "to": 90.0}
    provenance_change = diff["diff"]["param_provenance"]["spacing"]
    assert provenance_change["from"]["basis"] == "published"
    assert provenance_change["to"]["basis"] == "assumed"
    assert provenance_change["to"]["source"] is None

    output = project.path / ".aad" / "cache" / "test-export"
    output.mkdir(parents=True)
    step = output / "gearbox-r2-UNVERIFIED.step"
    step.write_text("step")

    async def fake_op(_project, _rev, op, _args, *, out_dir=None, timeout_s=120.0):
        assert op == "export"
        return {"ok": True, "files": {"step": str(step)}}, output

    handler._op = fake_op
    exported = _call(handler, "aad_export", {
        "project": "gearbox", "rev": 2, "formats": ["step"], "allow_unverified": True,
    })
    assert exported["metadata"]["param_basis_counts"]["assumed"] == 1
    sidecar = exported["artifacts"]["metadata"]
    report = json.loads(Path(sidecar).read_text(encoding="utf-8"))
    assert report["params_without_source"] == ["clearance", "spacing"]
    assert "does not verify" in report["provenance_notice"]
