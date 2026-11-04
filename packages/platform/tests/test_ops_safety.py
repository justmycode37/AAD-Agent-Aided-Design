import asyncio
from pathlib import Path

import pytest

from aiad_platform.daemon import handlers
from aiad_platform.daemon.handlers import Handlers, _project_directory, _publish_exports
from aiad_platform.daemon.projects import create_project
from aiad_platform.rpc import RpcError
from aiad_platform.specs import parse_spec


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AIAD_HOME", str(tmp_path / ".aiad"))
    (tmp_path / ".aiad").mkdir()
    return create_project("p")


def test_rejects_symlinked_project_directories(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    for name in ("imports", "exports"):
        directory = project.path / name
        if directory.exists():
            directory.rmdir()
        directory.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="symlink"):
            _project_directory(project, name)
    (outside / "secret").write_text("keep")
    with pytest.raises(RpcError):
        asyncio.run(Handlers().aiad_file_read({"project": "p", "path": "secret"}))


def test_import_replaces_destination_symlink_without_following_it(project, tmp_path, monkeypatch):
    outside = tmp_path / "outside.step"
    outside.write_text("keep")
    source = tmp_path / "part.step"
    source.write_text("model")
    imports = _project_directory(project, "imports")
    (imports / source.name).symlink_to(outside)

    async def fake_op(*_, **__):
        return {"ok": True, "imported": {}}

    monkeypatch.setattr(handlers, "run_op", fake_op)
    result = asyncio.run(Handlers().aiad_import({"project": "p", "path": str(source)}))
    assert result["structured"]["ok"]
    assert outside.read_text() == "keep"
    assert not (imports / source.name).is_symlink()
    assert (imports / source.name).read_text() == "model"


def test_export_preflights_all_destinations(project, tmp_path):
    exports = _project_directory(project, "exports")
    out = tmp_path / "output"
    out.mkdir()
    (out / "p.step").write_text("step")
    (out / "p.stl").write_text("stl")
    (exports / "p.stl").mkdir()
    with pytest.raises(ValueError):
        _publish_exports(exports, out, {fmt: str(out / f"p.{fmt}") for fmt in ("step", "stl")}, "p")
    assert not (exports / "p.step").exists()


def test_export_rolls_back_published_files(project, tmp_path, monkeypatch):
    exports = _project_directory(project, "exports")
    out = tmp_path / "output"
    out.mkdir()
    for fmt in ("step", "stl"):
        (out / f"p.{fmt}").write_text("new")
        (exports / f"p.{fmt}").write_text("old")
    replace = handlers.os.replace

    def fail_second(source, target):
        if Path(source) == out / "p.stl":
            raise OSError("disk full")
        return replace(source, target)

    monkeypatch.setattr(handlers.os, "replace", fail_second)
    with pytest.raises(OSError, match="disk full"):
        _publish_exports(exports, out, {fmt: str(out / f"p.{fmt}") for fmt in ("step", "stl")}, "p")
    assert all((exports / f"p.{fmt}").read_text() == "old" for fmt in ("step", "stl"))


def test_library_preserves_worker_timeout(monkeypatch):
    async def timeout(*_, **__):
        return {"ok": False, "failure": {"code": "TIMEOUT", "phase": "timeout", "message": "timed out"}}

    monkeypatch.setattr(handlers, "run_op", timeout)
    result = asyncio.run(Handlers().aiad_library({"action": "search", "query": "bolt"}))
    assert result["structured"]["failure"]["code"] == "TIMEOUT"
    assert result["structured"]["failure"]["phase"] == "timeout"


def test_duplicate_yaml_cannot_create_or_advance_project(project):
    duplicate = "requirements:\n  - id: size\n    bbox: {x: 10}\n    bbox: {x: 5}\n"
    with pytest.raises(ValueError, match="duplicate YAML key"):
        parse_spec(duplicate)
    handler = Handlers()
    result = asyncio.run(handler.aiad_new({"name": "bad", "spec": duplicate}))
    assert result["structured"]["failure"]["code"] == "BAD_INPUT"
    assert not (project.path.parent / "bad").exists()
    result = asyncio.run(handler.aiad_build({"project": "p", "source": "result = None", "spec": duplicate}))
    assert result["structured"]["failure"]["code"] == "BAD_INPUT"
    assert project.head_rev is None
