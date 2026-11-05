import asyncio

import pytest

from aad_platform.daemon import backend
from aad_platform.daemon.handlers import Handlers
from aad_platform.daemon.projects import MAX_RETAINED_FAILED_JOBS, open_project

FAILED_CHECK = {"id": "closed_shell", "suite": "hard", "level": "error", "status": "fail", "message": "open shell"}


def _failing_build(*, brep: bool):
    def run_build(*, job_dir, **_):
        if brep:
            (job_dir / "part.brep").write_text("BREP")
        return {
            "ok": False,
            "failure": {"code": "INVALID_SOLID", "phase": "validate", "message": "The hard gate failed."},
            "checks": [FAILED_CHECK],
            "checks_summary": {"pass": 0, "fail": 1, "warn": 0, "skip": 0},
        }

    return run_build


@pytest.fixture
def handlers(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
    monkeypatch.setattr(backend, "engine_available", lambda: True)
    instance = Handlers()
    asyncio.run(instance.call("aad_new", {"name": "p"}))
    return instance


def _build(handlers, **params):
    return asyncio.run(handlers.call("aad_build", {"project": "p", "source": "x = 1\n", "wait_s": 30, **params}))


def test_failed_candidate_is_retained_without_moving_head(handlers, monkeypatch):
    monkeypatch.setattr(backend, "run_build", _failing_build(brep=True))
    project = open_project("p")
    before = project.meta()

    envelope = _build(handlers)["structured"]

    assert envelope["status"] == "failed" and envelope["failure"]["code"] == "INVALID_SOLID"
    assert project.meta() == before and project.revisions() == []
    assert not (project.path / "model.py").exists()
    job = envelope["job"]
    assert (project.jobs_dir / job / "part.brep").read_text() == "BREP"

    # A new daemon has no in-memory job, and the evidence must still be readable.
    fresh = Handlers()
    again = asyncio.run(fresh.call("aad_history", {"project": "p", "action": "job", "job": job}))["structured"]
    assert again["status"] == "failed" and again["job"] == job
    assert again["checks"][0]["id"] == "closed_shell"
    assert again["artifacts"]["part.brep"].endswith("part.brep")


def test_failure_without_a_brep_is_not_retained(handlers, monkeypatch):
    monkeypatch.setattr(backend, "run_build", _failing_build(brep=False))
    envelope = _build(handlers)["structured"]
    assert "job" not in envelope
    assert list(open_project("p").jobs_dir.iterdir()) == []


def test_retained_candidates_are_bounded(handlers, monkeypatch):
    monkeypatch.setattr(backend, "run_build", _failing_build(brep=True))
    for _ in range(MAX_RETAINED_FAILED_JOBS + 2):
        _build(handlers)
    assert len(list(open_project("p").jobs_dir.iterdir())) == MAX_RETAINED_FAILED_JOBS


@pytest.mark.parametrize("job", ["../../etc", "", "ZZZ"])
def test_job_id_cannot_escape(handlers, job):
    result = asyncio.run(handlers.call("aad_history", {"project": "p", "action": "job", "job": job}))
    assert result["structured"]["failure"]["code"] == "BAD_INPUT"
