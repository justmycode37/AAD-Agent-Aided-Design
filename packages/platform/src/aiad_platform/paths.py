"""Filesystem locations. Nothing here touches the disk unless asked to."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

MAX_SOCKET_PATH = 100  # sun_path is 104 bytes on macOS and 108 on Linux.


def aiad_home() -> Path:
    """Global state directory, ~/.aiad unless AIAD_HOME overrides it."""
    override = os.environ.get("AIAD_HOME")
    return Path(override).expanduser() if override else Path.home() / ".aiad"


def run_dir() -> Path:
    return aiad_home() / "run"


def logs_dir() -> Path:
    return aiad_home() / "logs"


def cache_dir() -> Path:
    return aiad_home() / "cache"


def daemon_info_path() -> Path:
    return run_dir() / "aiadd.json"


def daemon_lock_path() -> Path:
    return run_dir() / "aiadd.lock"


def socket_path() -> Path:
    """The daemon socket. Falls back to the per-user temp dir if the path is too long."""
    path = run_dir() / "aiadd.sock"
    if len(os.fsencode(path)) <= MAX_SOCKET_PATH:
        return path
    digest = hashlib.sha256(os.fsencode(aiad_home())).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"aiadd-{digest}.sock"


def default_projects_root() -> Path:
    """Where projects live when the host supplies no MCP roots."""
    return Path.home() / "AIAD"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path
