import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from aad_platform.daemon import handlers
from aad_platform.daemon.handlers import Handlers, _project_directory, _publish_exports
from aad_platform.daemon.projects import create_project
from aad_platform.rpc import RpcError
from aad_platform.specs import parse_spec


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
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
        asyncio.run(Handlers().aad_file_read({"project": "p", "path": "secret"}))


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
    result = asyncio.run(Handlers().aad_import({"project": "p", "path": str(source)}))
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
    result = asyncio.run(Handlers().aad_library({"action": "search", "query": "bolt"}))
    assert result["structured"]["failure"]["code"] == "TIMEOUT"
    assert result["structured"]["failure"]["phase"] == "timeout"


def test_editable_guidance_is_discoverable_with_only_worker_read_mounts(tmp_path, monkeypatch):
    from aad_platform.daemon import ops_client
    from aad_quality import catalog

    root = tmp_path / "checkout"
    package = root / "packages" / "quality"
    origin = package / "src" / "aad_quality" / "__init__.py"
    origin.parent.mkdir(parents=True)
    origin.touch()
    (package / "pyproject.toml").write_text('[project]\nname = "aad-quality"\n')
    (package / "unrelated.txt").write_text("not needed by the worker")
    guidance = root / "skills"
    (guidance / "playbooks").mkdir(parents=True)
    monkeypatch.setattr(ops_client.importlib.util, "find_spec", lambda name: SimpleNamespace(origin=str(origin)) if name == "aad_quality" else None)
    monkeypatch.setattr(catalog, "__file__", str(origin))
    monkeypatch.setattr(catalog, "files", lambda name: origin.parent)
    reads = ops_client._package_roots()

    # Linux bubblewrap hides files outside the explicit read mounts. Unlike
    # Seatbelt's metadata permission, even is_file() cannot see an unmounted file.
    is_file, is_dir = Path.is_file, Path.is_dir

    def visible(path):
        return any(path == allowed or (is_dir(allowed) and path.is_relative_to(allowed)) for allowed in reads)

    monkeypatch.setattr(Path, "is_file", lambda path: visible(path) and is_file(path))
    monkeypatch.setattr(Path, "is_dir", lambda path: visible(path) and is_dir(path))
    assert catalog._guidance_root() == guidance
    assert not (package / "unrelated.txt").is_file()


def test_duplicate_yaml_cannot_create_or_advance_project(project):
    duplicate = "requirements:\n  - id: size\n    bbox: {x: 10}\n    bbox: {x: 5}\n"
    with pytest.raises(ValueError, match="duplicate YAML key"):
        parse_spec(duplicate)
    handler = Handlers()
    result = asyncio.run(handler.aad_new({"name": "bad", "spec": duplicate}))
    assert result["structured"]["failure"]["code"] == "BAD_INPUT"
    assert not (project.path.parent / "bad").exists()
    result = asyncio.run(handler.aad_build({"project": "p", "source": "result = None", "spec": duplicate}))
    assert result["structured"]["failure"]["code"] == "BAD_INPUT"
    assert project.head_rev is None
