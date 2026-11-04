import json

import pytest

from aiad_platform.daemon.projects import Project, ProjectError, create_project, open_project


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AIAD_HOME", str(tmp_path / ".aiad"))
    (tmp_path / ".aiad").mkdir()
    return tmp_path


def test_creates_the_contract_layout(home):
    project = create_project("bracket")
    assert project.path == (home / "AIAD" / "bracket").resolve()
    meta = json.loads((project.path / "project.json").read_text())
    assert meta["head_rev"] is None and meta["kind"] == "part" and meta["units"] == "mm"
    assert (project.path / ".gitignore").read_text().startswith(".aiad/")
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
