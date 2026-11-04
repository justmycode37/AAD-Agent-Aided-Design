"""Atomic, reversible MCP config writes for supported clients."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from aiad_platform.paths import aiad_home

from . import (
    CLIENT_IDS,
    ClientStatus,
    ServerEntry,
    WriteResult,
    _CLIENTS,
    _client_config_path,
    _client_detection_paths,
)
from ._formats import ABSENT, ConfigError, empty_text, get_entry, parse_document, remove_entry, set_entry


_MANIFEST_VERSION = 1
_UNCONDITIONAL = object()


def _entry_for_client(client_id: str, entry: ServerEntry) -> dict[str, Any]:
    base: dict[str, Any] = {
        "command": entry.command,
        "args": list(entry.args),
        "env": dict(entry.env),
    }
    if client_id == "vscode":
        return {"type": "stdio", **base}
    if client_id == "codex":
        return {"command": entry.command, "args": list(entry.args), "startup_timeout_sec": 30, "env": dict(entry.env)}
    if client_id == "goose":
        return {
            "enabled": True,
            "type": "stdio",
            "name": "aiad",
            "cmd": entry.command,
            "args": list(entry.args),
            "envs": dict(entry.env),
            "timeout": 30,
        }
    return base


def _validate_entry(entry: ServerEntry) -> str | None:
    if not isinstance(entry.command, str) or not entry.command.strip():
        return "command must be a non-empty absolute path"
    if not Path(entry.command).is_absolute():
        return "command must be an absolute path"
    if not isinstance(entry.args, tuple) or any(not isinstance(arg, str) for arg in entry.args):
        return "args must be a tuple of strings"
    try:
        env_items = dict(entry.env).items()
    except (TypeError, ValueError):
        return "env must be a mapping of strings to strings"
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in env_items):
        return "env must be a mapping of strings to strings"
    return None


def _managed_matches(current: Any, managed: Any) -> bool:
    if isinstance(managed, dict):
        if not isinstance(current, dict):
            return False
        return all(key in current and _managed_matches(current[key], value) for key, value in managed.items())
    return current == managed


def _merge_managed(current: Any, previous: Any, desired: Any) -> Any:
    if not (isinstance(current, dict) and isinstance(previous, dict) and isinstance(desired, dict)):
        return deepcopy(desired)
    merged = deepcopy(current)
    for key, old_value in previous.items():
        if key not in desired:
            if key in merged and _managed_matches(merged[key], old_value):
                del merged[key]
            continue
        new_value = desired[key]
        if key in merged and isinstance(merged[key], dict) and isinstance(old_value, dict) and isinstance(new_value, dict):
            merged[key] = _merge_managed(merged[key], old_value, new_value)
        else:
            merged[key] = deepcopy(new_value)
    for key, value in desired.items():
        if key not in previous:
            merged[key] = deepcopy(value)
    return merged


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_dir(client_id: str) -> Path:
    return aiad_home() / "backups" / client_id


def _manifest_path(client_id: str) -> Path:
    return _backup_dir(client_id) / "manifest.json"


def _is_symlink_or_nonfile(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)


def _read_regular_file(path: Path) -> tuple[bytes | None, int | None]:
    if not os.path.lexists(path):
        return None, None
    if _is_symlink_or_nonfile(path):
        raise OSError(f"refusing to follow non-regular file: {path}")
    info = path.stat()
    return path.read_bytes(), stat.S_IMODE(info.st_mode)


def _mkdir_one(path: Path) -> None:
    path.mkdir(mode=0o700)


def _ensure_dirs(path: Path) -> list[Path]:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    made: list[Path] = []
    for directory in reversed(missing):
        try:
            _mkdir_one(directory)
            made.append(directory)
        except FileExistsError:
            if not directory.is_dir():
                raise
    return made


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _assert_unchanged(path: Path, expected: bytes | None) -> None:
    current, _ = _read_regular_file(path)
    if current != expected:
        raise ConfigError(f"{path} changed while AIAD was editing it; retry after the client finishes saving.")


def _atomic_write(path: Path, data: bytes, mode: int = 0o600, *, expected: Any = _UNCONDITIONAL) -> None:
    _ensure_dirs(path.parent)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, mode)
        if expected is not _UNCONDITIONAL:
            _assert_unchanged(path, expected)
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _unlink(path: Path) -> None:
    path.unlink()
    _fsync_directory(path.parent)


def _rmdir(path: Path) -> None:
    path.rmdir()
    _fsync_directory(path.parent)


def _cleanup_dirs(paths: list[str] | list[Path]) -> None:
    for raw_path in reversed(paths):
        path = raw_path if isinstance(raw_path, Path) else Path(raw_path)
        try:
            if path.is_symlink():
                continue
            _rmdir(path)
        except (FileNotFoundError, OSError):
            continue


def _inside(path: Path, parent: Path) -> bool:
    try:
        return path.absolute().is_relative_to(parent.absolute())
    except (OSError, ValueError):
        return False


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _load_manifest(client_id: str, config_path: Path) -> tuple[dict[str, Any] | None, bytes | None, int | None]:
    path = _manifest_path(client_id)
    data, mode = _read_regular_file(path)
    if data is None:
        return None, None, None
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"AIAD backup manifest is invalid: {exc}") from exc
    required = {
        "version",
        "client_id",
        "config_path",
        "original_bytes_hash",
        "existed_before",
        "written_bytes_hash",
        "created_directories",
        "backup_directories_created",
        "managed_entry",
    }
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise ConfigError("AIAD backup manifest is missing required fields")
    if manifest.get("version") != _MANIFEST_VERSION or manifest.get("client_id") != client_id:
        raise ConfigError("AIAD backup manifest has an unsupported version or client id")
    if manifest.get("config_path") != str(config_path):
        raise ConfigError("AIAD backup manifest points to a different client config path")
    if not isinstance(manifest.get("existed_before"), bool) or not isinstance(manifest.get("managed_entry"), dict):
        raise ConfigError("AIAD backup manifest contains invalid install state")
    if not isinstance(manifest.get("created_directories"), list) or not isinstance(manifest.get("backup_directories_created"), list):
        raise ConfigError("AIAD backup manifest contains invalid directory records")
    for raw_path in manifest["created_directories"]:
        candidate = Path(raw_path)
        if candidate not in config_path.parent.parents and candidate != config_path.parent:
            raise ConfigError("AIAD backup manifest contains an unsafe config directory record")
    backup_dir = _backup_dir(client_id)
    for raw_path in manifest["backup_directories_created"]:
        candidate = Path(raw_path)
        if candidate not in backup_dir.parents and candidate != backup_dir:
            raise ConfigError("AIAD backup manifest contains an unsafe backup directory record")
    return manifest, data, mode


def _original_backup_path(client_id: str, manifest: dict[str, Any] | None) -> Path | None:
    if not manifest:
        return None
    filename = manifest.get("original_backup")
    if filename is None:
        return None
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ConfigError("AIAD backup manifest contains an unsafe backup filename")
    return _backup_dir(client_id) / filename


def _read_config(path: Path) -> tuple[bytes | None, int | None, str | None]:
    try:
        data, mode = _read_regular_file(path)
        text = data.decode("utf-8") if data is not None else None
        return data, mode, text
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"could not read config safely: {exc}") from exc


def _diff(old: bytes | None, new: bytes | None, path: Path) -> str:
    old_text = old.decode("utf-8", errors="replace") if old is not None else ""
    new_text = new.decode("utf-8", errors="replace") if new is not None else ""
    if old_text == new_text:
        return ""
    lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=str(path) if old is not None else "/dev/null",
            tofile=str(path) if new is not None else "/dev/null",
            lineterm="",
        )
    )
    return "\n".join(lines) + ("\n" if lines else "")


def _result(
    client_id: str,
    action: str,
    config_path: Path | None,
    backup_path: Path | None,
    diff: str,
    message: str,
) -> WriteResult:
    return WriteResult(client_id, action, config_path, backup_path, diff, message)  # type: ignore[arg-type]


def _read_entry(definition: Any, text: str) -> tuple[dict[str, Any], Any]:
    document = parse_document(definition, text)
    return document, get_entry(definition, document)


def _make_manifest(
    client_id: str,
    config_path: Path,
    original_data: bytes | None,
    original_mode: int | None,
    written_data: bytes,
    managed_entry: dict[str, Any],
    created_config_dirs: list[Path],
    created_backup_dirs: list[Path],
    original_backup: Path | None,
) -> dict[str, Any]:
    return {
        "version": _MANIFEST_VERSION,
        "client_id": client_id,
        "config_path": str(config_path),
        "original_bytes_hash": _sha256(original_data) if original_data is not None else None,
        "existed_before": original_data is not None,
        "original_mode": original_mode,
        "written_bytes_hash": _sha256(written_data),
        "created_directories": [str(path) for path in created_config_dirs],
        "backup_directories_created": [str(path) for path in created_backup_dirs],
        "original_backup": original_backup.name if original_backup is not None else None,
        "managed_entry": managed_entry,
    }


def _safe_rollback_config(path: Path, previous_data: bytes | None, previous_mode: int | None, *, expected: bytes | None) -> str | None:
    try:
        if previous_data is None:
            if os.path.lexists(path):
                _assert_unchanged(path, expected)
                _unlink(path)
        else:
            _atomic_write(path, previous_data, previous_mode or 0o600, expected=expected)
    except (OSError, ConfigError) as exc:
        return str(exc)
    return None


def _install(client_id: str, entry: ServerEntry, *, dry_run: bool = False) -> WriteResult:
    definition = _CLIENTS.get(client_id)
    if definition is None:
        return _result(client_id, "failed", None, None, "", f"Unknown client id: {client_id}")
    config_path = _client_config_path(client_id)
    if config_path is None:
        return _result(client_id, "skipped", None, None, "", f"{definition.name} has no config location on this OS.")
    invalid = _validate_entry(entry)
    if invalid:
        return _result(client_id, "failed", config_path, None, "", invalid)

    try:
        current_data, current_mode, current_text = _read_config(config_path)
        manifest, manifest_bytes, manifest_mode = _load_manifest(client_id, config_path)
        text = current_text if current_text is not None else empty_text(definition)
        document, current_entry = _read_entry(definition, text)
    except (ConfigError, OSError) as exc:
        return _result(client_id, "failed", config_path, None, "", f"Could not install AIAD: {exc}")

    desired_entry = _entry_for_client(client_id, entry)
    original_backup = _original_backup_path(client_id, manifest)
    is_owned = manifest is not None
    if current_entry is not ABSENT:
        if not is_owned:
            return _result(
                client_id,
                "skipped",
                config_path,
                None,
                "",
                "An aiad entry already exists without an AIAD backup manifest; it was left unchanged.",
            )
        previous_managed = manifest.get("managed_entry")
        if not isinstance(previous_managed, dict):
            return _result(client_id, "failed", config_path, original_backup, "", "AIAD backup manifest has no managed entry.")
        if not _managed_matches(current_entry, previous_managed) and not _managed_matches(current_entry, desired_entry):
            return _result(
                client_id,
                "skipped",
                config_path,
                original_backup,
                "",
                "The aiad entry changed outside AIAD and was left unchanged.",
            )
        if _managed_matches(current_entry, desired_entry):
            proposed_entry = current_entry
        else:
            proposed_entry = _merge_managed(current_entry, previous_managed, desired_entry)
    else:
        proposed_entry = desired_entry

    try:
        rendered_text = set_entry(definition, text, proposed_entry)
        new_data = rendered_text.encode("utf-8")
        # Reparse once more at the generic boundary so no format writer can
        # commit a syntactically invalid result.
        _, verified_entry = _read_entry(definition, rendered_text)
        if verified_entry != proposed_entry:
            raise ConfigError("post-edit verification found a different aiad entry")
    except (ConfigError, OSError, TypeError, ValueError) as exc:
        return _result(client_id, "failed", config_path, original_backup, "", f"Could not safely edit config: {exc}")

    same_entry = current_entry is not ABSENT and _managed_matches(current_entry, desired_entry)
    same_manifest_entry = bool(manifest and manifest.get("managed_entry") == desired_entry)
    write_config = current_data != new_data
    if not write_config and (not is_owned or (same_entry and same_manifest_entry)):
        return _result(client_id, "unchanged", config_path, original_backup if not dry_run else None, "", "AIAD is already configured with this entry.")

    action = "updated" if is_owned else "installed"
    diff = _diff(current_data, new_data, config_path)
    if dry_run:
        message = "Dry run: AIAD config would be updated." if is_owned else "Dry run: AIAD config would be installed."
        return _result(client_id, action, config_path, None, diff, message)

    config_dirs_made: list[Path] = []
    backup_dirs_made: list[Path] = []
    new_backup: Path | None = None
    changed_config = False
    changed_manifest = False
    manifest_path = _manifest_path(client_id)
    try:
        config_dirs_made = _ensure_dirs(config_path.parent)
        backup_dirs_made = _ensure_dirs(_backup_dir(client_id))
        if manifest is None and current_data is not None:
            new_backup = _backup_dir(client_id) / f"original-{uuid.uuid4().hex}.bin"
            _atomic_write(new_backup, current_data, 0o600)
            original_backup = new_backup
        if manifest is None:
            created_config_dirs = config_dirs_made
            created_backup_dirs = backup_dirs_made
            original_data = current_data
            original_file_mode = current_mode
        else:
            created_config_dirs = [Path(p) for p in manifest["created_directories"]]
            created_backup_dirs = [Path(p) for p in manifest["backup_directories_created"]]
            for directory in config_dirs_made:
                if directory not in created_config_dirs:
                    created_config_dirs.append(directory)
            for directory in backup_dirs_made:
                if directory not in created_backup_dirs:
                    created_backup_dirs.append(directory)
            original_data = None
            original_file_mode = manifest.get("original_mode")
        config_mode = current_mode or (manifest.get("original_mode") if manifest else None) or 0o600
        new_manifest = _make_manifest(
            client_id,
            config_path,
            original_data,
            original_file_mode,
            new_data,
            desired_entry,
            created_config_dirs,
            created_backup_dirs,
            original_backup,
        )
        if manifest is not None:
            new_manifest["original_bytes_hash"] = manifest.get("original_bytes_hash")
            new_manifest["existed_before"] = manifest.get("existed_before")
            new_manifest["original_mode"] = manifest.get("original_mode")
            new_manifest["original_backup"] = manifest.get("original_backup")
        if write_config:
            _atomic_write(config_path, new_data, config_mode, expected=current_data)
            changed_config = True
        encoded_manifest = _json_bytes(new_manifest)
        if encoded_manifest != manifest_bytes:
            _atomic_write(manifest_path, encoded_manifest, manifest_mode or 0o600, expected=manifest_bytes)
            changed_manifest = True
    except (OSError, ConfigError, ValueError, TypeError) as exc:
        rollback_problem = None
        if changed_config:
            rollback_problem = _safe_rollback_config(config_path, current_data, current_mode, expected=new_data)
        if changed_manifest:
            try:
                if manifest_bytes is None:
                    if os.path.lexists(manifest_path):
                        _unlink(manifest_path)
                else:
                    _atomic_write(manifest_path, manifest_bytes, manifest_mode or 0o600)
            except OSError as rollback_exc:
                rollback_problem = str(rollback_exc)
        if new_backup is not None and rollback_problem is None:
            try:
                _unlink(new_backup)
            except (FileNotFoundError, OSError):
                pass
        _cleanup_dirs([*config_dirs_made, *backup_dirs_made])
        suffix = f" Rollback issue: {rollback_problem}" if rollback_problem else ""
        return _result(client_id, "failed", config_path, new_backup if rollback_problem else None, diff, f"Could not write AIAD config: {exc}.{suffix}")

    backup_for_result = original_backup
    return _result(
        client_id,
        action,
        config_path,
        backup_for_result,
        diff,
        "AIAD config updated." if is_owned else "AIAD config installed.",
    )


def _manifest_original_bytes(client_id: str, manifest: dict[str, Any]) -> bytes | None:
    if not manifest.get("existed_before"):
        return None
    original_backup = _original_backup_path(client_id, manifest)
    if original_backup is None:
        raise ConfigError("original config backup is missing from the manifest")
    try:
        data, _ = _read_regular_file(original_backup)
    except OSError as exc:
        raise ConfigError(f"could not read original backup: {exc}") from exc
    if data is None:
        raise ConfigError("original config backup file is missing")
    expected_hash = manifest.get("original_bytes_hash")
    if not isinstance(expected_hash, str) or _sha256(data) != expected_hash:
        raise ConfigError("original config backup hash does not match the manifest")
    return data


def _remove_manifest(path: Path, old_manifest: bytes, old_mode: int | None) -> None:
    _assert_unchanged(path, old_manifest)
    _unlink(path)
    _fsync_directory(path.parent)


def _uninstall(client_id: str, *, dry_run: bool = False) -> WriteResult:
    definition = _CLIENTS.get(client_id)
    if definition is None:
        return _result(client_id, "failed", None, None, "", f"Unknown client id: {client_id}")
    config_path = _client_config_path(client_id)
    if config_path is None:
        return _result(client_id, "skipped", None, None, "", f"{definition.name} has no config location on this OS.")
    try:
        current_data, current_mode, current_text = _read_config(config_path)
        manifest, manifest_bytes, manifest_mode = _load_manifest(client_id, config_path)
    except (ConfigError, OSError) as exc:
        return _result(client_id, "failed", config_path, None, "", f"Could not uninstall AIAD: {exc}")
    if manifest is None:
        return _result(client_id, "not_installed", config_path, None, "", "No AIAD backup manifest was found.")
    if current_data is None or current_text is None:
        return _result(client_id, "not_installed", config_path, _original_backup_path(client_id, manifest), "", "The aiad entry is absent.")

    try:
        document, current_entry = _read_entry(definition, current_text)
    except ConfigError as exc:
        return _result(client_id, "failed", config_path, _original_backup_path(client_id, manifest), "", f"Could not safely parse config: {exc}")
    if current_entry is ABSENT:
        return _result(client_id, "not_installed", config_path, _original_backup_path(client_id, manifest), "", "The aiad entry is absent.")

    backup_path = _original_backup_path(client_id, manifest)
    exact_write = _sha256(current_data) == manifest.get("written_bytes_hash")
    if exact_write:
        try:
            original_data = _manifest_original_bytes(client_id, manifest)
        except ConfigError as exc:
            return _result(client_id, "failed", config_path, backup_path, "", f"Could not restore original config: {exc}")
        if original_data is None:
            new_data = None
            action = "restored"
        else:
            new_data = original_data
            action = "restored"
        diff = _diff(current_data, new_data, config_path)
        if dry_run:
            return _result(client_id, action, config_path, None, diff, "Dry run: original config would be restored.")
        try:
            if new_data is None:
                _assert_unchanged(config_path, current_data)
                _unlink(config_path)
            else:
                original_mode = manifest.get("original_mode") or 0o600
                _atomic_write(config_path, new_data, int(original_mode), expected=current_data)
            _remove_manifest(_manifest_path(client_id), manifest_bytes or b"", manifest_mode)
        except (OSError, ConfigError) as exc:
            rollback_problem = _safe_rollback_config(config_path, current_data, current_mode, expected=new_data)
            suffix = f" Rollback issue: {rollback_problem}" if rollback_problem else ""
            return _result(client_id, "failed", config_path, backup_path, diff, f"Could not restore original config: {exc}.{suffix}")
        if new_data is None:
            _cleanup_dirs([*manifest["created_directories"], *manifest["backup_directories_created"]])
        return _result(client_id, action, config_path, backup_path, diff, "Original config restored exactly.")

    managed = manifest.get("managed_entry")
    if not isinstance(managed, dict) or current_entry != managed:
        return _result(
            client_id,
            "skipped",
            config_path,
            backup_path,
            "",
            "The aiad entry changed outside AIAD and was left unchanged.",
        )
    try:
        rendered_text = remove_entry(definition, current_text)
        new_data = rendered_text.encode("utf-8")
        _, remaining_entry = _read_entry(definition, rendered_text)
        if remaining_entry is not ABSENT:
            raise ConfigError("post-edit verification found an aiad entry")
    except (ConfigError, OSError, TypeError, ValueError) as exc:
        return _result(client_id, "failed", config_path, backup_path, "", f"Could not safely remove aiad: {exc}")
    diff = _diff(current_data, new_data, config_path)
    if dry_run:
        return _result(client_id, "removed", config_path, None, diff, "Dry run: only the aiad entry would be removed.")
    try:
        _atomic_write(config_path, new_data, current_mode or 0o600, expected=current_data)
        _remove_manifest(_manifest_path(client_id), manifest_bytes or b"", manifest_mode)
    except (OSError, ConfigError) as exc:
        rollback_problem = _safe_rollback_config(config_path, current_data, current_mode, expected=new_data)
        suffix = f" Rollback issue: {rollback_problem}" if rollback_problem else ""
        return _result(client_id, "failed", config_path, backup_path, diff, f"Could not remove aiad: {exc}.{suffix}")
    return _result(client_id, "removed", config_path, backup_path, diff, "AIAD entry removed; unrelated config was kept.")


def _statuses() -> list[ClientStatus]:
    results: list[ClientStatus] = []
    for client_id in CLIENT_IDS:
        definition = _CLIENTS[client_id]
        config_path = _client_config_path(client_id)
        detected = any(path.exists() for path in _client_detection_paths(client_id))
        installed = False
        if config_path is not None:
            try:
                current_data, _, current_text = _read_config(config_path)
                manifest, _, _ = _load_manifest(client_id, config_path)
                if current_data is not None and current_text is not None and manifest is not None:
                    _, current_entry = _read_entry(definition, current_text)
                    managed = manifest.get("managed_entry")
                    installed = current_entry is not ABSENT and isinstance(managed, dict) and _managed_matches(current_entry, managed)
            except (ConfigError, OSError):
                installed = False
        results.append(
            ClientStatus(
                id=client_id,
                name=definition.name,
                detected=detected,
                config_path=config_path,
                installed=installed,
                restart_hint=definition.restart_hint,
            )
        )
    return results


def install(client_id: str, entry: ServerEntry, *, dry_run: bool = False) -> WriteResult:
    return _install(client_id, entry, dry_run=dry_run)


def uninstall(client_id: str, *, dry_run: bool = False) -> WriteResult:
    return _uninstall(client_id, dry_run=dry_run)


def statuses() -> list[ClientStatus]:
    return _statuses()

