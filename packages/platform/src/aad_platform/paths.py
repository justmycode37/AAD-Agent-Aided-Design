"""Filesystem locations. Nothing here touches the disk unless asked to."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

MAX_SOCKET_PATH = 100  # sun_path is 104 bytes on macOS and 108 on Linux.


def aad_home() -> Path:
    """Global state directory, ~/.aad unless AAD_HOME overrides it."""
    override = os.environ.get("AAD_HOME")
    return Path(override).expanduser() if override else Path.home() / ".aad"


def run_dir() -> Path:
    return aad_home() / "run"


def logs_dir() -> Path:
    return aad_home() / "logs"


def cache_dir() -> Path:
    return aad_home() / "cache"


def daemon_info_path() -> Path:
    return run_dir() / "aadd.json"


def daemon_lock_path() -> Path:
    return run_dir() / "aadd.lock"


def socket_path(home: Path | None = None) -> Path:
    """The daemon socket. Falls back to the per-user temp dir if the path is too long."""
    home = home or aad_home()
    path = home / "run" / "aadd.sock"
    if len(os.fsencode(path)) <= MAX_SOCKET_PATH:
        return path
    digest = hashlib.sha256(os.fsencode(home)).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"aadd-{digest}.sock"


def default_projects_root() -> Path:
    """Where projects live when the host supplies no MCP roots."""
    return Path.home() / "AAD"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path
