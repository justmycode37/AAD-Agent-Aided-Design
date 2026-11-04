from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

from aiad_platform import __version__, daemon_ctl
from aiad_platform import cli


@pytest.fixture
def temp_home(monkeypatch):
    home = Path(tempfile.mkdtemp(prefix="aiad-", dir="/tmp"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AIAD_HOME", str(home))
    monkeypatch.setenv("AIAD_NO_BROWSER", "1")
    try:
        yield home
    finally:
        # This path is the test's short-lived AIAD_HOME, never a shared user daemon.
        try:
            daemon_ctl.stop(timeout_s=0.2)
        except Exception:
            pass
        shutil.rmtree(home, ignore_errors=True)


@dataclasses.dataclass(frozen=True)
class FakeServerEntry:
    command: str
    args: tuple[str, ...] = ("mcp",)
    env: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class FakeClientStatus:
    id: str
    name: str
    detected: bool
    config_path: Path | None
    installed: bool
    restart_hint: str


@dataclasses.dataclass(frozen=True)
class FakeWriteResult:
    id: str
    action: str
    config_path: Path | None
    backup_path: Path | None
    diff: str
    message: str


def install_fake_clients(monkeypatch, home: Path):
    module = types.ModuleType("aiad_platform.clients")
    module.CLIENT_IDS = ("codex", "claude-code")
    module.ServerEntry = FakeServerEntry
    module.calls = []

    def statuses():
        return [
            FakeClientStatus("codex", "Codex", True, home / "codex.json", True, "Start a new Codex session."),
            FakeClientStatus("claude-code", "Claude Code", False, home / "claude.json", False, "Start a new Claude Code session."),
        ]

    def install(client_id, entry, *, dry_run=False):
        path = home / f"{client_id}.json"
        module.calls.append(("install", client_id, entry, dry_run))
        if not dry_run:
            path.write_text(json.dumps({"command": entry.command, "args": entry.args}))
        return FakeWriteResult(client_id, "would_install" if dry_run else "installed", path, None, "+ MCP server entry", "Ready to use.")

    def uninstall(client_id, *, dry_run=False):
        path = home / f"{client_id}.json"
        module.calls.append(("uninstall", client_id, dry_run))
        if not dry_run:
            path.unlink(missing_ok=True)
        return FakeWriteResult(client_id, "would_uninstall" if dry_run else "uninstalled", path, None, "- MCP server entry", "Removed.")

    module.statuses = statuses
    module.install = install
    module.uninstall = uninstall
    monkeypatch.setitem(sys.modules, "aiad_platform.clients", module)
    return module


def output_object(capsys):
    out = capsys.readouterr().out
    assert out.strip()
    assert "\n" not in out.strip(), out
    return json.loads(out)


def test_version_json(capsys, temp_home):
    assert cli.main(["version", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"version": __version__}


def test_cli_import_and_version_do_not_load_heavy_modules_or_mcp(temp_home):
    env = os.environ.copy()
    import_probe = subprocess.run(
        [sys.executable, "-c", "import sys; import aiad_platform.cli; bad={'build123d','OCP','vtk'} & set(sys.modules); assert not bad, bad"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert import_probe.returncode == 0, import_probe.stderr

    version_probe = subprocess.run(
        [sys.executable, "-c", "import sys; from aiad_platform.cli import main; main(['version']); assert 'mcp' not in sys.modules"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert version_probe.returncode == 0, version_probe.stderr
    assert version_probe.stdout.strip() == __version__


def test_subprocess_cli_runs_in_temp_home(temp_home):
    result = subprocess.run(
        [sys.executable, "-m", "aiad_platform.cli", "version", "--json"],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"version": __version__}


def test_script_entry_falls_back_to_python_module(monkeypatch, temp_home):
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    command, args = cli._script_entry()
    assert command == sys.executable
    assert Path(command).is_absolute()
    assert args == ("-m", "aiad_platform.cli", "mcp")


def test_script_entry_does_not_select_another_installation_on_path(monkeypatch, temp_home):
    other = temp_home / "other" / "aiad"
    other.parent.mkdir()
    other.write_text("other installation")
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(cli.shutil, "which", lambda _: str(other))
    assert cli._script_entry() == (sys.executable, ("-m", "aiad_platform.cli", "mcp"))


def test_install_dry_run_is_json_and_writes_nothing(monkeypatch, capsys, temp_home):
    clients = install_fake_clients(monkeypatch, temp_home)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    assert cli.main(["install", "--client", "codex", "--dry-run", "--json"]) == 0
    result = output_object(capsys)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["clients"][0]["action"] == "would_install"
    assert not (temp_home / "codex.json").exists()
    assert not (temp_home / "run" / "aiadd.sock").exists()
    assert clients.calls[0][3] is True
    assert result["entry"]["command"] == sys.executable
    assert result["entry"]["args"] == ["-m", "aiad_platform.cli", "mcp"]


def test_install_live_runs_requested_steps_and_reports_restart_hint(monkeypatch, capsys, temp_home):
    clients = install_fake_clients(monkeypatch, temp_home)
    monkeypatch.setattr(cli, "_script_entry", lambda: ("/tmp/aiad", ("mcp",)))
    monkeypatch.setattr("aiad_platform.daemon_ctl.ensure_running", lambda: {"running": True, "compatible": True, "pid": 42, "version": __version__})
    monkeypatch.setattr("aiad_platform.doctor.run_doctor", lambda: {"ok": True, "checks": []})
    assert cli.main(["install", "--client", "codex", "--no-prewarm", "--json"]) == 0
    result = output_object(capsys)
    assert result["ok"] is True
    assert clients.calls[0][3] is False
    assert result["restart_hints"] == [{"id": "codex", "hint": "Start a new Codex session."}]
    assert clients.calls[0][2].command == "/tmp/aiad"


def test_uninstall_dry_run_preserves_client_config(monkeypatch, capsys, temp_home):
    install_fake_clients(monkeypatch, temp_home)
    config_path = temp_home / "codex.json"
    config_path.write_text('{"existing":true}')
    monkeypatch.setattr("aiad_platform.daemon_ctl.stop", lambda: pytest.fail("dry run must not stop daemon"))
    assert cli.main(["uninstall", "--client", "codex", "--dry-run", "--json"]) == 0
    result = output_object(capsys)
    assert result["ok"] is True
    assert result["clients"][0]["action"] == "would_uninstall"
    assert config_path.read_text() == '{"existing":true}'


def test_uninstall_purge_removes_only_aiad_home(monkeypatch, capsys, temp_home):
    install_fake_clients(monkeypatch, temp_home)
    state = temp_home / "state"
    state.mkdir()
    monkeypatch.setenv("AIAD_HOME", str(state))
    project_file = temp_home / "AIAD" / "part" / "keep.txt"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("keep")
    monkeypatch.setattr("aiad_platform.daemon_ctl.stop", lambda: True)
    assert cli.main(["uninstall", "--client", "codex", "--purge", "--json"]) == 0
    result = output_object(capsys)
    assert result["ok"] is True
    assert result["purged"] is True
    assert not state.exists()
    assert project_file.read_text() == "keep"


@pytest.mark.parametrize("action", ["failed", "skipped"])
def test_uninstall_failure_keeps_backups_and_returns_failure(monkeypatch, capsys, temp_home, action):
    clients = install_fake_clients(monkeypatch, temp_home)
    backup = temp_home / "backups" / "codex" / "original.bin"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"original config")
    clients.uninstall = lambda *_args, **_kw: FakeWriteResult("codex", action, None, backup, "", "Could not uninstall")
    monkeypatch.setattr("aiad_platform.daemon_ctl.stop", lambda: True)
    assert cli.main(["uninstall", "--client", "codex", "--purge", "--json"]) == 1
    assert output_object(capsys)["purged"] is False
    assert backup.read_bytes() == b"original config"


def test_purge_refuses_remaining_client_manifest(monkeypatch, temp_home):
    state = temp_home / "state"
    manifest = state / "backups" / "codex" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    monkeypatch.setenv("AIAD_HOME", str(state))
    assert cli._purge_aiad_home()[0] is False
    assert manifest.exists()


def test_config_json_read_set_and_all(monkeypatch, capsys, temp_home):
    assert cli.main(["config", "viewer=inline", "--json"]) == 0
    assert output_object(capsys)["value"] == "inline"
    assert cli.main(["config", "viewer", "--json"]) == 0
    assert output_object(capsys)["value"] == "inline"
    assert cli.main(["config", "--json"]) == 0
    assert output_object(capsys)["values"] == {"viewer": "inline"}


def test_config_invalid_value_is_json_failure(capsys, temp_home):
    assert cli.main(["config", "viewer=browser", "--json"]) == 1
    result = output_object(capsys)
    assert result["ok"] is False
    assert "viewer must be one of" in result["error"]


def test_daemon_start_status_and_stop_json(capsys, temp_home):
    try:
        assert cli.main(["daemon", "start", "--json"]) == 0
        started = output_object(capsys)
        assert started["ok"] is True
        assert started["running"] is True
        assert started["version"] == __version__

        assert cli.main(["daemon", "status", "--json"]) == 0
        status = output_object(capsys)
        assert status["ok"] is True
        assert status["socket"]

        assert cli.main(["daemon", "stop", "--json"]) == 0
        stopped = output_object(capsys)
        assert stopped["ok"] is True
        assert stopped["stopped"] is True
    finally:
        daemon_ctl.stop(timeout_s=0.2)


def test_invalid_uninstall_does_not_stop_daemon(monkeypatch, capsys, temp_home):
    install_fake_clients(monkeypatch, temp_home)
    monkeypatch.setattr(daemon_ctl, "stop", lambda: pytest.fail("invalid request must not stop a daemon"))
    assert cli.main(["uninstall", "--client", "typo", "--json"]) == 1
    assert "Unknown client" in output_object(capsys)["client_error"]


@pytest.mark.parametrize("metadata", [None, "not json"])
def test_stop_probes_daemon_when_metadata_is_missing_or_invalid(metadata, temp_home):
    from aiad_platform import paths

    daemon_ctl.ensure_running()
    if metadata is None:
        paths.daemon_info_path().unlink()
    else:
        paths.daemon_info_path().write_text(metadata)
    assert daemon_ctl.stop() is True
    assert daemon_ctl.status()["running"] is False


def test_purge_protects_case_aliases_of_projects(monkeypatch, temp_home):
    root = temp_home / "AIAD"
    root.mkdir()
    protected = root / "part.txt"
    protected.write_text("keep")
    alias = temp_home / "aiad"
    if not alias.exists() or not alias.samefile(root):
        pytest.skip("requires a case-insensitive filesystem")
    monkeypatch.setenv("AIAD_HOME", str(alias))
    assert cli._purge_aiad_home()[0] is False
    assert protected.read_text() == "keep"


def test_remote_commands_are_json_and_forward_contract(monkeypatch, capsys, temp_home, tmp_path):
    source = tmp_path / "model.py"
    source.write_text("from build123d import Box\nresult = Box(4, 5, 6)\n")
    calls = []

    def fake_remote(requests):
        calls.extend(requests)
        replies = []
        for method, _params in requests:
            structured = {"ok": True, "summary": f"{method} ok"}
            if method == "aiad_show":
                structured["viewer"] = {"url": "http://127.0.0.1:1234/view"}
            replies.append({"text": f"{method} ok", "structured": structured})
        return replies, None

    monkeypatch.setattr(cli, "_remote_calls", fake_remote)
    assert cli.main(["build", "sample", str(source), "--param", "width=4.5", "--preview", "--create", "--json"]) == 0
    build = output_object(capsys)
    assert build["ok"] is True
    assert [method for method, _params in calls] == ["aiad_new", "aiad_build"]
    assert calls[0][1]["source"].startswith("from build123d")
    assert calls[1][1]["params"] == {"width": 4.5}
    assert calls[1][1]["preview"] is True
    assert calls[1][1]["client"] == {"name": "aiad-cli", "supports_ui": False}

    calls.clear()
    assert cli.main(["show", "sample", "--rev", "2", "--json"]) == 0
    show = output_object(capsys)
    assert show["ok"] is True
    assert calls == [("aiad_show", {"project": "sample", "client": {"name": "aiad-cli", "supports_ui": False}, "rev": 2})]

    calls.clear()
    assert cli.main(["export", "sample", "--format", "stl", "step", "--rev", "2", "--allow-unverified", "--json"]) == 0
    export = output_object(capsys)
    assert export["ok"] is True
    assert calls[0][0] == "aiad_export"
    assert calls[0][1]["formats"] == ["stl", "step"]
    assert calls[0][1]["allow_unverified"] is True


def test_show_prints_viewer_url(monkeypatch, capsys, temp_home):
    monkeypatch.setattr(
        cli,
        "_remote_calls",
        lambda calls: ([{"text": "Showing sample.", "structured": {"ok": True, "viewer": {"url": "http://127.0.0.1:4567/p/sample"}}}], None),
    )
    assert cli.main(["show", "sample"]) == 0
    assert "http://127.0.0.1:4567/p/sample" in capsys.readouterr().out


def test_update_json_prints_without_running_upgrade(monkeypatch, capsys, temp_home):
    monkeypatch.setattr(cli.subprocess, "run", lambda *_a, **_k: pytest.fail("update must not run without --run"))
    assert cli.main(["update", "--json"]) == 0
    result = output_object(capsys)
    assert result["command"] == "uv tool upgrade aiad"
    assert result["ran"] is False


def test_serve_json_reports_not_implemented(capsys, temp_home):
    assert cli.main(["serve", "--json"]) == 1
    result = output_object(capsys)
    assert result["ok"] is False
    assert "not implemented" in result["error"]


def test_doctor_command_json(monkeypatch, capsys, temp_home):
    monkeypatch.setattr("aiad_platform.doctor.run_doctor", lambda: {"ok": False, "checks": [{"id": "engine", "status": "fail", "message": "missing"}]})
    assert cli.main(["doctor", "--json"]) == 1
    result = output_object(capsys)
    assert result["ok"] is False
    assert result["checks"][0]["id"] == "engine"
