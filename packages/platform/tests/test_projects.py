import asyncio
import gzip
import hashlib
import json
import stat
from pathlib import Path

import pytest

from aad_platform.daemon.projects import Project, ProjectError, create_project, open_project


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
    return tmp_path


def test_creates_the_contract_layout(home):
    project = create_project("bracket")
    assert project.path == (home / "AAD" / "bracket").resolve()
    meta = json.loads((project.path / "project.json").read_text())
    assert meta["head_rev"] is None and meta["kind"] == "part" and meta["units"] == "mm"
    assert (project.path / ".gitignore").read_text().startswith(".aad/")
    assert (project.path / "exports").is_dir()


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".hidden", "x" * 65])
def test_rejects_unsafe_names(home, name):
    with pytest.raises(ProjectError):
        create_project(name)
    with pytest.raises(ProjectError):
        open_project(name)


def test_alias_paths_resolve_to_one_project(home):
    real = home / "real"
    real.mkdir()
    (home / "alias").symlink_to(real)
    created = create_project("p", client={"root": str(home / "alias")})
    assert created.path == (real / "p").resolve()
    assert open_project("p", {"root": str(home / "alias")}).path == open_project("p", {"root": str(real)}).path


def test_commit_moves_head_and_keeps_spec(home):
    project: Project = create_project("p")
    _, job_dir = project.new_job_dir()
    assert project.commit_revision(job_dir, "source", "units: mm\n") == 1
    assert project.head_rev == 1
    assert (project.rev_dir(1) / "spec.yaml").read_text() == "units: mm\n"
    assert (project.path / "model.py").read_text() == "source"
    _, second = project.new_job_dir()
    assert project.commit_revision(second, "source 2", None) == 2
    assert project.head_rev == 2
    assert not (project.path / "spec.yaml").exists()


def test_first_commit_interns_unchanged_files_from_existing_revisions(home):
    project: Project = create_project("p")
    old_revision = project.rev_dir(1)
    old_revision.mkdir()
    (old_revision / "model.py").write_text("same model")
    (old_revision / "part.brep").write_bytes(b"same geometry")
    meta = project.meta()
    meta["head_rev"] = 1
    project.write_meta(meta)

    _, job_dir = project.new_job_dir()
    (job_dir / "part.brep").write_bytes(b"same geometry")
    assert project.commit_revision(job_dir, "same model", None) == 2

    for relative in ("model.py", "part.brep"):
        old = old_revision / relative
        new = project.rev_dir(2) / relative
        assert old.read_bytes() == new.read_bytes()
        assert (old.stat().st_dev, old.stat().st_ino) == (new.stat().st_dev, new.stat().st_ino)
        assert stat.S_IMODE(old.stat().st_mode) & 0o222 == 0
    assert json.loads(project.objects_index_path.read_text())["revisions"] == [1, 2]


def test_revision_meta_reads_legacy_inline_features(home):
    project: Project = create_project("p")
    rev_dir = project.rev_dir(1)
    rev_dir.mkdir()
    features = [{"name": "legacy", "faces": [{"kind": "face", "index": 0}]}]
    (rev_dir / "meta.json").write_text(json.dumps({"rev": 1, "features": features}))

    assert project.revision_meta(1) == {"rev": 1, "features": features}


def test_revisions_share_identical_files_and_keep_viewer_and_export_paths(home, tmp_path):
    from build123d import Box, export_brep

    from aad_platform.daemon.handlers import Handlers
    from aad_platform.ops import load_context, op_export

    project: Project = create_project("p", kind="assembly")
    source_brep = tmp_path / "box.brep"
    assert export_brep(Box(1, 1, 1), source_brep)
    brep_bytes = source_brep.read_bytes()
    scene_bytes = gzip.compress(b'{"instances":[],"shapes":{}}', mtime=0)
    scene_id = hashlib.sha256(scene_bytes).hexdigest()
    scene_manifest = {
        "scene_id": scene_id,
        "project": project.name,
        "format": "tcv-5",
        "encoding": "gzip",
        "kind": "3d",
        "units": "mm",
        "bbox": [0, 0, 0, 1, 1, 1],
        "total_bytes": len(scene_bytes),
        "triangles": 12,
        "parts": [{"path": "box", "name": "box"}],
    }
    features = [{"name": "main", "faces": [{"kind": "face", "index": 0, "feature": "main"}]}]

    for rev in (1, 2):
        _, job_dir = project.new_job_dir()
        (job_dir / "part.brep").write_bytes(brep_bytes)
        (job_dir / "features.json").write_text(json.dumps(features, sort_keys=True, separators=(",", ":")))
        (job_dir / "meta.json").write_text(
            json.dumps({"rev": rev, "status": "draft", "scene": {**scene_manifest, "rev": rev}})
        )
        scene_dir = job_dir / "scene"
        scene_dir.mkdir()
        (scene_dir / "scene.bin.gz").write_bytes(scene_bytes)
        (scene_dir / "scene.json").write_text(
            json.dumps({**scene_manifest, "rev": rev, "status": "draft"})
        )
        project.commit_revision(job_dir, "same model", "units: mm\n")

    shared_paths = ("model.py", "spec.yaml", "part.brep", "features.json", "scene/scene.bin.gz")
    for relative in shared_paths:
        first = project.rev_dir(1) / relative
        second = project.rev_dir(2) / relative
        assert first.read_bytes() == second.read_bytes()
        assert (first.stat().st_dev, first.stat().st_ino) == (second.stat().st_dev, second.stat().st_ino)
        assert stat.S_IMODE(first.stat().st_mode) & 0o222 == 0
    assert project.revision_meta(2)["features"] == features
    assert project.revision_meta(1)["rev"] == 1
    assert project.revision_meta(2)["rev"] == 2

    shown = asyncio.run(
        Handlers().aad_show({"project": project.name, "rev": 2, "client": {"supports_ui": True}})
    )["structured"]
    assert shown["ok"] and shown["scene"]["scene_id"] == scene_id

    out_dir = tmp_path / "exports"
    out_dir.mkdir()
    context = load_context(
        {
            "project_dir": str(project.path),
            "rev": 2,
            "kind": "assembly",
            "units": "mm",
            "out_dir": str(out_dir),
        }
    )
    assert context.shape is not None and context.features[0].name == "main"
    exported = op_export(context, {"formats": ["step", "viewer_html"], "options": {}, "stem": "p-r2"}, out_dir)
    assert Path(exported["files"]["step"]).is_file()
    assert Path(exported["files"]["viewer_html"]).is_file()

    orphan_data = b"orphan object"
    orphan_digest = hashlib.sha256(orphan_data).hexdigest()
    orphan = project.objects_dir / orphan_digest[:2] / orphan_digest
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(orphan_data)
    assert project.prune_objects() == 1
    assert not orphan.exists()
    assert (project.rev_dir(1) / "part.brep").read_bytes() == brep_bytes
    assert (project.rev_dir(2) / "scene" / "scene.bin.gz").read_bytes() == scene_bytes


def test_build_writes_features_to_a_reusable_file(home, tmp_path, monkeypatch):
    from aad_platform.daemon import handlers

    project = create_project("p", source="from aad import *\nresult = Box(1, 1, 1)")
    features = [{"name": "main", "faces": [{"kind": "face", "index": 0, "feature": "main"}]}]

    def fake_run_build(**request):
        job_dir = Path(request["job_dir"])
        (job_dir / "part.brep").write_bytes(b"placeholder geometry")
        return {
            "ok": True,
            "status": "draft",
            "summary": "Built a test revision.",
            "metrics": {"solids": 1},
            "checks": [],
            "checks_summary": {"pass": 1},
            "scene": {"scene_id": "a" * 64, "parts": []},
            "context": {"features": features},
        }

    monkeypatch.setattr(handlers.backend, "engine_available", lambda: True)
    monkeypatch.setattr(handlers.backend, "run_build", fake_run_build)
    result = asyncio.run(handlers.Handlers().aad_build({"project": "p", "wait_s": 10}))

    assert result["structured"]["ok"] is True
    meta_path = project.rev_dir(1) / "meta.json"
    assert "features" not in json.loads(meta_path.read_text())
    assert (project.rev_dir(1) / "features.json").is_file()
    assert project.revision_meta(1)["features"] == features


def test_checkout_removes_spec_absent_from_selected_revision(home):
    project = create_project("p")
    _, first = project.new_job_dir()
    project.commit_revision(first, "one", None)
    _, second = project.new_job_dir()
    project.commit_revision(second, "two", "requirements: []\n")
    project.checkout(1)
    assert project.head_rev == 1
    assert (project.path / "model.py").read_text() == "one"
    assert not (project.path / "spec.yaml").exists()


def test_checkout_missing_model_leaves_head_and_working_copy(home):
    project = create_project("p")
    _, job = project.new_job_dir()
    project.commit_revision(job, "good", "units: mm\n")
    project.rev_dir(2).mkdir()
    with pytest.raises(FileNotFoundError):
        project.checkout(2)
    assert project.head_rev == 1
    assert (project.path / "model.py").read_text() == "good"
    assert (project.path / "spec.yaml").read_text() == "units: mm\n"


def test_checkout_write_failure_restores_working_copy(home, monkeypatch):
    project = create_project("p")
    _, first = project.new_job_dir()
    project.commit_revision(first, "one", "units: mm\n")
    _, second = project.new_job_dir()
    project.commit_revision(second, "two", None)
    monkeypatch.setattr(project.__class__, "write_meta", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        project.checkout(1)
    assert project.head_rev == 2
    assert (project.path / "model.py").read_text() == "two"
    assert not (project.path / "spec.yaml").exists()
