"""Installs AAD's guide as a Claude Code skill. Other hosts get it through aad_library."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .clients import _home
from .clients._writers import _atomic_write

MARKER = "<!-- Managed by aad install. aad uninstall removes this file. -->"
def skill_path() -> Path:
    return _home() / ".claude" / "skills" / "aad" / "SKILL.md"


def skill_text() -> str:
    from aad_quality.catalog import _guidance_root

    front, body = _guidance_root().joinpath("aad/SKILL.md").read_text(encoding="utf-8").split("\n---\n", 1)
    return f"{front}\n---\n\n{MARKER}\n{body}"


def install_skill(*, dry_run: bool = False) -> dict[str, Any]:
    """Write the skill. A file this command did not write is the user's, so it stays."""
    path = skill_path()
    result: dict[str, Any] = {"path": str(path)}
    if path.is_symlink() or (path.exists() and (not path.is_file() or MARKER not in path.read_text(encoding="utf-8"))):
        return {**result, "action": "skipped", "reason": "An existing skill at this path was not written by AAD."}
    text = skill_text()
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return {**result, "action": "unchanged"}
    if dry_run:
        return {**result, "action": "would_write"}
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, text.encode("utf-8"), 0o644)
    return {**result, "action": "written"}


def uninstall_skill(*, dry_run: bool = False) -> dict[str, Any]:
    path = skill_path()
    result: dict[str, Any] = {"path": str(path)}
    if not path.exists() and not path.is_symlink():
        return {**result, "action": "absent"}
    if path.is_symlink() or not path.is_file() or MARKER not in path.read_text(encoding="utf-8"):
        return {**result, "action": "skipped", "reason": "This skill was not written by AAD."}
    if dry_run:
        return {**result, "action": "would_remove"}
    path.unlink()
    try:
        path.parent.rmdir()
    except OSError:  # The user keeps other files here.
        pass
    return {**result, "action": "removed"}
