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
