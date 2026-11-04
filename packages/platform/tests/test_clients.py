from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from aiad_platform.clients import CLIENT_IDS, ServerEntry, install, statuses, uninstall
from aiad_platform.clients import _CLIENTS, _client_config_path, _client_detection_paths
from aiad_platform.clients._formats import ABSENT, empty_text, get_entry, parse_document, set_entry
from aiad_platform.clients._writers import _entry_for_client
from aiad_platform.paths import aiad_home


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("AIAD_HOME", str(home / ".aiad"))
    return home


def _entry(home: Path, version: int = 1) -> ServerEntry:
    return ServerEntry(
        command=str(home / "bin" / f"aiad-{version}"),
        args=("mcp", "--stdio"),
        env={"AIAD_TEST_MODE": str(version)},
    )


def _config_path(client_id: str) -> Path:
    path = _client_config_path(client_id)
    assert path is not None
    return path


def _seed_text(client_id: str) -> str:
    definition = _CLIENTS[client_id]
    if definition.format == "json":
        document = {
            definition.root_key: {
                "other": {"command": "/usr/bin/other", "args": ["keep"]},
            },
            "workspace": {"theme": "dark"},
        }
        return json.dumps(document, indent=2) + "\n"
    if definition.format == "jsonc":
        return (
            "{\n"
            "  // keep root comment\n"
            f'  "{definition.root_key}": {{\n'
            "    // keep server comment\n"
            '    "other": { "command": "/usr/bin/other", "args": ["keep"] },\n'
            "  },\n"
            '  "workspace": "keep",\n'
            "}\n"
        )
    if definition.format == "toml":
        return (
            "# keep preamble comment\n"
            'profile = "default"\n'
            "\n"
            "# keep other server comment\n"
            "[mcp_servers.other]\n"
            'command = "/usr/bin/other"\n'
            'args = ["keep"]\n'
            'env = { KEEP = "yes" }\n'
            "\n"
            "[ui]\n"
            'theme = "dark"\n'
        )
    return (
        "# keep preamble comment\n"
        "app_name: demo\n"
        "extensions:\n"
        "  # keep other server comment\n"
        "  other:\n"
        "    cmd: other\n"
        "    args: []\n"
        "    envs:\n"
        "      KEEP: value\n"
        "    enabled: true\n"
    )


def _write_seed(client_id: str, text: str | None = None, mode: int = 0o640) -> tuple[Path, bytes]:
    path = _config_path(client_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (text if text is not None else _seed_text(client_id)).encode("utf-8")
    path.write_bytes(content)
    path.chmod(mode)
    return path, content


def _document(client_id: str, path: Path) -> dict:
    return parse_document(_CLIENTS[client_id], path.read_text(encoding="utf-8"))


def _manifest_path(client_id: str) -> Path:
    return aiad_home() / "backups" / client_id / "manifest.json"


def _assert_unrelated_server(client_id: str, document: dict) -> None:
    definition = _CLIENTS[client_id]
    if definition.format == "toml":
        assert document["profile"] == "default"
        assert document["mcp_servers"]["other"]["command"] == "/usr/bin/other"
        assert document["mcp_servers"]["other"]["env"]["KEEP"] == "yes"
        assert document["ui"]["theme"] == "dark"
    elif definition.format == "yaml":
        assert document["app_name"] == "demo"
        assert document["extensions"]["other"]["cmd"] == "other"
        assert document["extensions"]["other"]["envs"]["KEEP"] == "value"
    else:
        assert document["workspace"] == {"theme": "dark"} if definition.format == "json" else document["workspace"] == "keep"
        assert document[definition.root_key]["other"]["command"] == "/usr/bin/other"


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_fresh_install_creates_config_and_manifest(client_id: str, isolated_home: Path) -> None:
    path = _config_path(client_id)
    assert not path.exists()

    entry = _entry(isolated_home)
    result = install(client_id, entry)

    assert result.action == "installed"
    assert result.config_path == path
    assert result.backup_path is None
    assert path.is_file()
    assert get_entry(_CLIENTS[client_id], _document(client_id, path)) == _entry_for_client(client_id, entry)

    manifest_path = _manifest_path(client_id)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["original_bytes_hash"] is None
    assert manifest["existed_before"] is False
    assert manifest["written_bytes_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert all(Path(raw).is_relative_to(isolated_home) for raw in manifest["created_directories"])
    assert manifest["backup_directories_created"]


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_install_preserves_existing_unrelated_server(client_id: str, isolated_home: Path) -> None:
    path, _ = _write_seed(client_id)
    entry = _entry(isolated_home)

    result = install(client_id, entry)

    assert result.action == "installed", result.message
    document = _document(client_id, path)
    assert get_entry(_CLIENTS[client_id], document) == _entry_for_client(client_id, entry)
    _assert_unrelated_server(client_id, document)


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_reinstall_is_idempotent_and_does_not_create_another_backup(client_id: str, isolated_home: Path) -> None:
    path, _ = _write_seed(client_id)
    entry = _entry(isolated_home)
    assert install(client_id, entry).action == "installed"
    config_before = path.read_bytes()
    manifest_before = _manifest_path(client_id).read_bytes()
    backup_dir = _manifest_path(client_id).parent
    backup_files_before = sorted(item.name for item in backup_dir.iterdir())

    result = install(client_id, entry)

    assert result.action == "unchanged"
    assert path.read_bytes() == config_before
    assert _manifest_path(client_id).read_bytes() == manifest_before
    assert sorted(item.name for item in backup_dir.iterdir()) == backup_files_before


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_changed_entry_updates_config_but_keeps_first_original(client_id: str, isolated_home: Path) -> None:
    path, original = _write_seed(client_id)
    first_entry = _entry(isolated_home, 1)
    second_entry = _entry(isolated_home, 2)
    assert install(client_id, first_entry).action == "installed"
    manifest_before = json.loads(_manifest_path(client_id).read_text(encoding="utf-8"))
    backup_path = _manifest_path(client_id).parent / manifest_before["original_backup"]
    backup_files_before = sorted(item.name for item in backup_path.parent.iterdir())

    result = install(client_id, second_entry)

    assert result.action == "updated"
    assert get_entry(_CLIENTS[client_id], _document(client_id, path)) == _entry_for_client(client_id, second_entry)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    manifest_after = json.loads(_manifest_path(client_id).read_text(encoding="utf-8"))
    assert manifest_after["original_bytes_hash"] == hashlib.sha256(original).hexdigest()
    assert manifest_after["existed_before"] is True
    assert manifest_after["written_bytes_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert backup_path.read_bytes() == original
    assert sorted(item.name for item in backup_path.parent.iterdir()) == backup_files_before


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_dry_run_creates_no_file_backup_or_directory(client_id: str, isolated_home: Path) -> None:
    path = _config_path(client_id)
    before = sorted((item.relative_to(isolated_home).as_posix(), item.is_dir()) for item in isolated_home.rglob("*"))

    result = install(client_id, _entry(isolated_home), dry_run=True)

    after = sorted((item.relative_to(isolated_home).as_posix(), item.is_dir()) for item in isolated_home.rglob("*"))
    assert result.action == "installed"
    assert before == after
    assert not path.exists()
    assert not aiad_home().exists()


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_uninstall_restores_existing_config_byte_for_byte(client_id: str, isolated_home: Path) -> None:
    path, original = _write_seed(client_id)
    original_mode = stat.S_IMODE(path.stat().st_mode)
    result = install(client_id, _entry(isolated_home))
    assert result.action == "installed"
    backup_path = result.backup_path
    assert backup_path is not None and backup_path.read_bytes() == original

    removed = uninstall(client_id)

    assert removed.action == "restored"
    assert path.read_bytes() == original
    assert stat.S_IMODE(path.stat().st_mode) == original_mode
    assert not _manifest_path(client_id).exists()
    assert backup_path.exists()


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_uninstall_of_new_config_removes_created_file_and_directories(client_id: str, isolated_home: Path) -> None:
    path = _config_path(client_id)
    assert install(client_id, _entry(isolated_home)).action == "installed"
    manifest = json.loads(_manifest_path(client_id).read_text(encoding="utf-8"))
    created_dirs = [Path(raw) for raw in manifest["created_directories"] + manifest["backup_directories_created"]]

    result = uninstall(client_id)

    assert result.action == "restored"
    assert not path.exists()
    assert not _manifest_path(client_id).exists()
    assert all(not directory.exists() for directory in created_dirs)


def _add_unrelated_edit(client_id: str, path: Path) -> None:
    definition = _CLIENTS[client_id]
    text = path.read_text(encoding="utf-8")
    if definition.format == "jsonc":
        # Simulate a user editing one property without stripping all comments.
        opening = text.index("{")
        path.write_text(text[:opening + 1] + '\n  "user_edit": "keep me",' + text[opening + 1:], encoding="utf-8")
    elif definition.format == "json":
        document = parse_document(definition, text)
        document["user_edit"] = "keep me"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    elif definition.format == "toml":
        path.write_text(text.replace('profile = "default"\n', 'profile = "default"\nuser_edit = "keep me"\n', 1), encoding="utf-8")
    else:
        path.write_text("user_edit: keep me\n" + text, encoding="utf-8")


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_uninstall_after_unrelated_edit_keeps_edit_and_removes_only_aiad(client_id: str, isolated_home: Path) -> None:
    path, _ = _write_seed(client_id)
    assert install(client_id, _entry(isolated_home)).action == "installed"
    _add_unrelated_edit(client_id, path)

    result = uninstall(client_id)

    assert result.action == "removed"
    document = _document(client_id, path)
    assert document["user_edit"] == "keep me"
    assert get_entry(_CLIENTS[client_id], document) is ABSENT
    _assert_unrelated_server(client_id, document)
    assert not _manifest_path(client_id).exists()


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_unparseable_config_is_left_untouched(client_id: str, isolated_home: Path) -> None:
    path = _config_path(client_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    bad_bytes = (
        b'{"broken": ]\n'
        if _CLIENTS[client_id].format in {"json", "jsonc"}
        else b"[broken\nkey = [\n"
        if _CLIENTS[client_id].format == "toml"
        else b"extensions: [\n"
    )
    path.write_bytes(bad_bytes)
    path.chmod(0o640)

    result = install(client_id, _entry(isolated_home))

    assert result.action == "failed"
    assert path.read_bytes() == bad_bytes
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not aiad_home().exists()


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_foreign_aiad_entry_is_skipped(client_id: str, isolated_home: Path) -> None:
    definition = _CLIENTS[client_id]
    foreign = _entry_for_client(client_id, ServerEntry(str(isolated_home / "bin" / "foreign"), ("serve",), {"OWNER": "other"}))
    text = set_entry(definition, empty_text(definition), foreign)
    path, original = _write_seed(client_id, text=text)

    result = install(client_id, _entry(isolated_home))

    assert result.action == "skipped"
    assert path.read_bytes() == original
    assert not aiad_home().exists()


@pytest.mark.parametrize("client_id", ("codex", "goose", "zed"))
def test_text_format_comments_survive_install_update_and_uninstall(client_id: str, isolated_home: Path) -> None:
    path, _ = _write_seed(client_id)
    if client_id == "zed":
        comments = ("// keep root comment", "// keep server comment")
    else:
        comments = ("# keep preamble comment", "# keep other server comment")
    first = _entry(isolated_home, 1)
    second = _entry(isolated_home, 2)

    assert install(client_id, first).action == "installed"
    assert all(comment in path.read_text(encoding="utf-8") for comment in comments)
    assert install(client_id, second).action == "updated"
    assert all(comment in path.read_text(encoding="utf-8") for comment in comments)
    _add_unrelated_edit(client_id, path)
    assert uninstall(client_id).action == "removed"
    assert all(comment in path.read_text(encoding="utf-8") for comment in comments)


@pytest.mark.parametrize("client_id", CLIENT_IDS)
def test_config_directory_detection_and_installed_status(client_id: str, isolated_home: Path) -> None:
    path = _config_path(client_id)
    if path.parent != isolated_home:
        detected_dir = path.parent
    else:
        relative = _CLIENTS[client_id].detected_dir
        assert relative is not None
        detected_dir = isolated_home.joinpath(*relative)
    detected_dir.mkdir(parents=True, exist_ok=True)

    before = {status.id: status for status in statuses()}[client_id]
    assert before.detected is True
    assert before.installed is False
    assert before.config_path == path

    assert install(client_id, _entry(isolated_home)).action == "installed"
    after = {status.id: status for status in statuses()}[client_id]
    assert after.detected is True
    assert after.installed is True


def test_codex_restart_hint_explains_exec_approval_and_t3_code(isolated_home: Path) -> None:
    hint = {status.id: status.restart_hint for status in statuses()}["codex"]
    assert "codex exec" in hint
    assert "approval_policy=never" in hint
    assert 'default_tools_approval_mode = "approve"' in hint
    assert "AIAD does not set this for you" in hint
    assert "T3 Code" in hint
    assert "~/.codex/config.toml" in hint


@pytest.mark.parametrize("client_id", ("claude-desktop", "goose", "zed"))
def test_macos_detection_uses_the_config_directory(client_id: str, isolated_home: Path) -> None:
    path = _client_config_path(client_id, platform="darwin")
    assert path is not None
    assert path.parent in _client_detection_paths(client_id, platform="darwin")


def test_no_writer_operation_touches_outside_temp_home(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aiad_platform.clients import _writers

    assert aiad_home().is_relative_to(isolated_home)
    touched: list[Path] = []
    for name in ("_atomic_write", "_mkdir_one", "_unlink", "_rmdir"):
        original = getattr(_writers, name)

        def guarded(path: Path, *args, _original=original, **kwargs):
            target = Path(path).absolute()
            assert target.is_relative_to(isolated_home), f"writer attempted {target}"
            touched.append(target)
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(_writers, name, guarded)

    for client_id in CLIENT_IDS:
        path = _config_path(client_id)
        assert path.absolute().is_relative_to(isolated_home), path
        assert install(client_id, _entry(isolated_home)).action == "installed"
        assert uninstall(client_id).action == "restored"

    assert touched
    assert all(path.is_relative_to(isolated_home) for path in touched)


def test_zed_handles_escaped_quotes_and_nested_trailing_commas(isolated_home: Path) -> None:
    path = _config_path("zed")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = (
        "{\n"
        '  "theme": "a \\"quoted\\" } brace",\n'
        '  "languages": { "Python": { "tab_size": 4, }, },\n'
        '  "context_servers": {\n'
        '    "other": { "command": "x", "args": [], },\n'
        "  },\n"
        "}\n"
    )
    path.write_text(original)

    assert install("zed", _entry(isolated_home)).action == "installed"
    document = parse_document(_CLIENTS["zed"], path.read_text())
    assert "aiad" in document["context_servers"] and "other" in document["context_servers"]
    assert document["theme"] == 'a "quoted" } brace'

    assert uninstall("zed").action == "restored"
    assert path.read_text() == original
