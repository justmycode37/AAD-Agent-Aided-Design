"""Client MCP configuration paths and public writer API."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping


@dataclass(frozen=True)
class ServerEntry:
    command: str
    args: tuple[str, ...] = ("mcp",)
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClientStatus:
    id: str
    name: str
    detected: bool
    config_path: Path | None
    installed: bool
    restart_hint: str


@dataclass(frozen=True)
class WriteResult:
    id: str
    action: Literal[
        "installed",
        "updated",
        "unchanged",
        "removed",
        "restored",
        "not_installed",
        "skipped",
        "failed",
    ]
    config_path: Path | None
    backup_path: Path | None
    diff: str
    message: str


@dataclass(frozen=True)
class _ClientDefinition:
    id: str
    name: str
    format: Literal["json", "jsonc", "toml", "yaml"]
    root_key: str
    restart_hint: str
    mac_path: tuple[str, ...] | None
    linux_path: tuple[str, ...] | None
    windows_path: tuple[str, ...] | None
    linux_base: Literal["home", "xdg"] = "home"
    windows_base: Literal["home", "appdata"] = "home"
    detected_dir: tuple[str, ...] | None = None
    mac_app: str | None = None


CLIENT_IDS: tuple[str, ...] = (
    "claude-code",
    "claude-desktop",
    "codex",
    "cursor",
    "vscode",
    "windsurf",
    "goose",
    "gemini-cli",
    "zed",
    "cline",
)


# Verified 2026-11-04: user-scope MCP servers use ~/.claude.json and
# mcpServers. https://code.claude.com/docs/en/mcp
_CLIENTS: dict[str, _ClientDefinition] = {
    "claude-code": _ClientDefinition(
        "claude-code",
        "Claude Code",
        "json",
        "mcpServers",
        "Start a new Claude Code session, or refresh MCP servers with /mcp.",
        (".claude.json",),
        (".claude.json",),
        (".claude.json",),
        detected_dir=(".claude",),
    ),
    # Verified 2026-11-04 against the MCP project's Claude Desktop guide,
    # including macOS, Linux, Windows paths and mcpServers stdio entries.
    # https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server
    "claude-desktop": _ClientDefinition(
        "claude-desktop",
        "Claude Desktop",
        "json",
        "mcpServers",
        "Fully quit and reopen Claude Desktop.",
        ("Library", "Application Support", "Claude", "claude_desktop_config.json"),
        ("Claude", "claude_desktop_config.json"),
        ("Claude", "claude_desktop_config.json"),
        linux_base="xdg",
        windows_base="appdata",
        mac_app="Claude.app",
    ),
    # Verified 2026-11-04: ~/.codex/config.toml, mcp_servers.<id>, stdio
    # command/args/env, and startup_timeout_sec.
    # https://developers.openai.com/codex/mcp
    # https://learn.chatgpt.com/docs/config-file/config-reference
    "codex": _ClientDefinition(
        "codex",
        "Codex",
        "toml",
        "mcp_servers",
        "Restart Codex and T3 Code; T3 Code also reads ~/.codex/config.toml. "
        "With approval_policy=never, codex exec refuses MCP tools unless the server config sets "
        'default_tools_approval_mode = "approve"; AIAD does not set this for you.',
        (".codex", "config.toml"),
        (".codex", "config.toml"),
        (".codex", "config.toml"),
        detected_dir=(".codex",),
    ),
    # Verified 2026-11-04: ~/.cursor/mcp.json with mcpServers.
    # https://prod.cursor.com/help/customization/mcp
    "cursor": _ClientDefinition(
        "cursor",
        "Cursor",
        "json",
        "mcpServers",
        "Restart Cursor or reload its window.",
        (".cursor", "mcp.json"),
        (".cursor", "mcp.json"),
        (".cursor", "mcp.json"),
        detected_dir=(".cursor",),
        mac_app="Cursor.app",
    ),
    # Verified 2026-11-04: VS Code user-profile mcp.json uses `servers`,
    # and stdio entries require `type: stdio`.
    # https://code.visualstudio.com/docs/agents/reference/mcp-configuration
    "vscode": _ClientDefinition(
        "vscode",
        "VS Code",
        "json",
        "servers",
        "Reload the VS Code window or start a new Copilot session.",
        ("Library", "Application Support", "Code", "User", "mcp.json"),
        ("Code", "User", "mcp.json"),
        ("Code", "User", "mcp.json"),
        linux_base="xdg",
        windows_base="appdata",
        mac_app="Visual Studio Code.app",
    ),
    # Could not verify the current format or path: Windsurf's official MCP
    # documentation URL redirects to an inaccessible Devin page as of
    # 2026-11-04. This keeps the task-provided mcpServers/path assumption.
    # https://docs.windsurf.com/windsurf/cascade/mcp
    "windsurf": _ClientDefinition(
        "windsurf",
        "Windsurf",
        "json",
        "mcpServers",
        "Restart Windsurf or reload Cascade.",
        (".codeium", "windsurf", "mcp_config.json"),
        (".codeium", "windsurf", "mcp_config.json"),
        ("Codeium", "Windsurf", "mcp_config.json"),
        windows_base="appdata",
        detected_dir=(".codeium", "windsurf"),
        mac_app="Windsurf.app",
    ),
    # Verified 2026-11-04: Goose extensions are extensions.aiad in
    # ~/.config/goose/config.yaml, with stdio/cmd/args/envs/timeout fields.
    # Windows uses %APPDATA%\Block\goose\config\config.yaml.
    # https://goose.vcorp.ai/docs/getting-started/using-extensions/
    # https://goose.vcorp.ai/docs/guides/config-files/
    "goose": _ClientDefinition(
        "goose",
        "Goose",
        "yaml",
        "extensions",
        "Start a new Goose session or restart Goose Desktop.",
        (".config", "goose", "config.yaml"),
        ("goose", "config.yaml"),
        ("Block", "goose", "config", "config.yaml"),
        linux_base="xdg",
        windows_base="appdata",
        detected_dir=("goose",),
        mac_app="Goose.app",
    ),
    # Verified 2026-11-04: ~/.gemini/settings.json uses mcpServers and
    # command/args/env for stdio servers.
    # https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html
    "gemini-cli": _ClientDefinition(
        "gemini-cli",
        "Gemini CLI",
        "json",
        "mcpServers",
        "Start a new Gemini CLI session.",
        (".gemini", "settings.json"),
        (".gemini", "settings.json"),
        (".gemini", "settings.json"),
        detected_dir=(".gemini",),
    ),
    # Verified 2026-11-04: settings.json uses context_servers with
    # command/args/env; the official FAQ gives ~/.config/zed/settings.json.
    # https://zed.dev/docs/ai/mcp
    # https://zed.dev/faq
    "zed": _ClientDefinition(
        "zed",
        "Zed",
        "jsonc",
        "context_servers",
        "Restart Zed or open a new Agent thread.",
        (".config", "zed", "settings.json"),
        ("zed", "settings.json"),
        ("Zed", "settings.json"),
        linux_base="xdg",
        windows_base="appdata",
        detected_dir=("zed",),
        mac_app="Zed.app",
    ),
    # Official Cline docs verify the mcpServers stdio shape. They document
    # ~/.cline/data/settings/cline_mcp_settings.json for CLI and recent Cline
    # repository notes describe migration from VS Code globalStorage to this
    # shared settings path; an explicit IDE path is not documented.
    # https://github.com/cline/cline/blob/main/docs/mcp/mcp-overview.mdx
    # https://github.com/cline/cline/issues/13011
    "cline": _ClientDefinition(
        "cline",
        "Cline",
        "json",
        "mcpServers",
        "Reload the VS Code window or start a new Cline task.",
        (".cline", "data", "settings", "cline_mcp_settings.json"),
        (".cline", "data", "settings", "cline_mcp_settings.json"),
        (".cline", "data", "settings", "cline_mcp_settings.json"),
        detected_dir=(".cline",),
    ),
}


def _home() -> Path:
    return Path.home()


def _xdg_config_home(home: Path) -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate
    return home / ".config"


def _appdata(home: Path) -> Path:
    configured = os.environ.get("APPDATA")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate
    return home / "AppData" / "Roaming"


def _base_path(definition: _ClientDefinition, platform: str, home: Path) -> tuple[Path, tuple[str, ...] | None]:
    if platform == "darwin":
        return home, definition.mac_path
    if platform.startswith("linux"):
        base = _xdg_config_home(home) if definition.linux_base == "xdg" else home
        return base, definition.linux_path
    if platform in {"win32", "cygwin"}:
        base = _appdata(home) if definition.windows_base == "appdata" else home
        return base, definition.windows_path
    return home, None


def _client_config_path(client_id: str, *, platform: str | None = None) -> Path | None:
    definition = _CLIENTS.get(client_id)
    if definition is None:
        return None
    current_platform = sys.platform if platform is None else platform
    if client_id == "codex" and os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"]).expanduser().resolve() / "config.toml"
    if client_id == "cline":
        # https://docs.cline.bot/cli/cli-reference (configuration paths/overrides)
        if os.environ.get("CLINE_MCP_SETTINGS_PATH"):
            return Path(os.environ["CLINE_MCP_SETTINGS_PATH"]).expanduser().resolve()
        if os.environ.get("CLINE_DATA_DIR"):
            return Path(os.environ["CLINE_DATA_DIR"]).expanduser().resolve() / "settings" / "cline_mcp_settings.json"
        if os.environ.get("CLINE_DIR"):
            return Path(os.environ["CLINE_DIR"]).expanduser().resolve() / "data" / "settings" / "cline_mcp_settings.json"
    base, relative = _base_path(definition, current_platform, _home())
    return base.joinpath(*relative) if relative is not None else None


def _client_detection_paths(client_id: str, *, platform: str | None = None) -> tuple[Path, ...]:
    definition = _CLIENTS[client_id]
    current_platform = sys.platform if platform is None else platform
    home = _home()
    candidates: list[Path] = []
    config_path = _client_config_path(client_id, platform=current_platform)
    if config_path is not None:
        candidates.append(config_path)
        if config_path.parent != home:
            candidates.append(config_path.parent)
    if definition.detected_dir:
        use_config_parent = (
            current_platform == "darwin"
            and definition.linux_base == "xdg"
            and definition.mac_path is not None
            and definition.mac_path[0] == ".config"
        ) or (
            current_platform in {"win32", "cygwin"}
            and definition.windows_base == "appdata"
        )
        if not use_config_parent:
            if current_platform == "darwin":
                base = home
            elif current_platform.startswith("linux"):
                base = _xdg_config_home(home) if definition.linux_base == "xdg" else home
            elif current_platform in {"win32", "cygwin"}:
                base = _appdata(home) if definition.windows_base == "appdata" else home
            else:
                base = home
            candidates.append(base.joinpath(*definition.detected_dir))
    if current_platform == "darwin" and definition.mac_app:
        candidates.append(Path("/Applications") / definition.mac_app)
    # Preserve order while avoiding duplicate paths (for config files whose
    # parent is also the configured detection directory).
    return tuple(dict.fromkeys(candidates))


from ._writers import install as _install  # noqa: E402
from ._writers import statuses as _statuses  # noqa: E402
from ._writers import uninstall as _uninstall  # noqa: E402


def statuses() -> list[ClientStatus]:
    return _statuses()


def install(client_id: str, entry: ServerEntry, *, dry_run: bool = False) -> WriteResult:
    return _install(client_id, entry, dry_run=dry_run)


def uninstall(client_id: str, *, dry_run: bool = False) -> WriteResult:
    return _uninstall(client_id, dry_run=dry_run)


__all__ = [
    "CLIENT_IDS",
    "ClientStatus",
    "ServerEntry",
    "WriteResult",
    "install",
    "statuses",
    "uninstall",
]
