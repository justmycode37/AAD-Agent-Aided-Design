"""`<AAD_HOME>/config.toml`. Small on purpose: the daemon reads it on every aad_show."""

from __future__ import annotations

import os
import tomllib
from typing import Any

from . import paths

DEFAULTS: dict[str, str] = {"viewer": "auto", "engine.memory": "auto"}
ALLOWED: dict[str, frozenset[str]] = {"viewer": frozenset({"auto", "inline", "window", "off"})}


class ConfigError(ValueError):
    pass


def _path() -> Any:
    return paths.aad_home() / "config.toml"


def load() -> dict[str, str]:
    """All values, defaults included. Raises ConfigError if the file does not parse."""
    try:
        data = tomllib.loads(_path().read_text())
    except FileNotFoundError:
        data = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{_path()} is not valid TOML: {exc}") from exc
    engine = data.get("engine", {})
    if not isinstance(engine, dict):
        raise ConfigError("The [engine] config value must be a TOML table.")
    values = {
        "viewer": str(data.get("viewer", DEFAULTS["viewer"])),
        "engine.memory": str(engine.get("memory", data.get("engine.memory", DEFAULTS["engine.memory"]))),
    }
    _validate_memory(values["engine.memory"])
    return values


def get(key: str) -> str:
    """One value. A broken file falls back to the default, because a viewer choice must not stop a build."""
    if key not in DEFAULTS:
        raise ConfigError(f"Unknown config key {key!r}. Known keys: {', '.join(sorted(DEFAULTS))}.")
    try:
        return load()[key]
    except ConfigError:
        return DEFAULTS[key]


def set_value(key: str, value: str) -> None:
    if key not in DEFAULTS:
        raise ConfigError(f"Unknown config key {key!r}. Known keys: {', '.join(sorted(DEFAULTS))}.")
    if key == "engine.memory":
        _validate_memory(value)
    elif value not in ALLOWED[key]:
        raise ConfigError(f"{key} must be one of {', '.join(sorted(ALLOWED[key]))}, not {value!r}.")
    values = load()
    values[key] = value
    paths.ensure_private_dir(paths.aad_home())
    tmp = _path().with_suffix(".toml.tmp")
    tmp.write_text(
        f'viewer = "{values["viewer"]}"\n\n'
        f'[engine]\nmemory = "{values["engine.memory"]}"\n'
    )
    os.replace(tmp, _path())


def engine_memory_limit_bytes():
    """Resolve the configured per-worker virtual-address growth budget."""
    from aad.engine.memory import AddressSpaceUnavailable, memory_budget_bytes

    value = get("engine.memory")
    try:
        return memory_budget_bytes(value)
    except AddressSpaceUnavailable:
        if value == "auto":
            return None
        raise


def _validate_memory(value: str) -> None:
    if value == "auto":
        return
    from aad.engine.memory import memory_budget_bytes

    try:
        memory_budget_bytes(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"engine.memory must be 'auto', a positive byte count, or a positive size using B, KiB, MiB, GiB or TiB: {exc}") from exc
