from __future__ import annotations

from pathlib import Path

import pytest

from aad_platform import skill


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(skill, "_home", lambda: tmp_path)
    return tmp_path


def test_install_writes_a_valid_skill_and_is_idempotent(home):
    assert skill.install_skill(dry_run=True)["action"] == "would_write"
    assert not skill.skill_path().exists()
    assert skill.install_skill()["action"] == "written"
    text = skill.skill_path().read_text(encoding="utf-8")
    assert text.startswith("---\nname: aad\ndescription: ")
    assert "aad_measure" in text and "sheet_metal" in text
    assert skill.install_skill()["action"] == "unchanged"


def test_a_skill_the_user_owns_survives_install_and_uninstall(home):
    path = skill.skill_path()
    path.parent.mkdir(parents=True)
    path.write_text("mine", encoding="utf-8")
    assert skill.install_skill()["action"] == "skipped"
    assert skill.uninstall_skill()["action"] == "skipped"
    assert path.read_text(encoding="utf-8") == "mine"


def test_uninstall_removes_what_install_wrote(home):
    skill.install_skill()
    assert skill.uninstall_skill(dry_run=True)["action"] == "would_remove"
    assert skill.skill_path().exists()
    assert skill.uninstall_skill()["action"] == "removed"
    assert not skill.skill_path().parent.exists()
    assert skill.uninstall_skill()["action"] == "absent"
