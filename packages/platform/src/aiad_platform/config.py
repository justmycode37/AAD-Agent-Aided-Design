"""`<AIAD_HOME>/config.toml`. Small on purpose: the daemon reads it on every aiad_show."""

from __future__ import annotations

import os
import tomllib
from typing import Any

from . import paths

DEFAULTS: dict[str, str] = {"viewer": "auto"}
ALLOWED: dict[str, frozenset[str]] = {"viewer": frozenset({"auto", "inline", "window", "off"})}


class ConfigError(ValueError):
    pass


def _path() -> Any:
    return paths.aiad_home() / "config.toml"


def load() -> dict[str, str]:
    """All values, defaults included. Raises ConfigError if the file does not parse."""
    try:
        data = tomllib.loads(_path().read_text())
    except FileNotFoundError:
        data = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{_path()} is not valid TOML: {exc}") from exc
    return {key: str(data.get(key, default)) for key, default in DEFAULTS.items()}


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
    if value not in ALLOWED[key]:
        raise ConfigError(f"{key} must be one of {', '.join(sorted(ALLOWED[key]))}, not {value!r}.")
    values = load()
    values[key] = value
    paths.ensure_private_dir(paths.aiad_home())
    tmp = _path().with_suffix(".toml.tmp")
    tmp.write_text("".join(f'{name} = "{val}"\n' for name, val in sorted(values.items())))
    os.replace(tmp, _path())
