"""Small, kernel-free helpers available to model scripts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

_overrides: dict[str, Any] = {}
_values: dict[str, Any] = {}


def configure_params(overrides: dict[str, Any] | None = None) -> None:
    """Set parameter overrides for the next model execution in this process."""
    global _overrides, _values
    _overrides = dict(overrides or {})
    _values = {}


def params(**defaults: Any) -> SimpleNamespace:
    """Declare simple named model parameters and apply request overrides."""
    global _values
    unknown = sorted(set(_overrides) - set(defaults))
    if unknown:
        names = ", ".join(unknown)
        raise ValueError(f"parameter override(s) are not declared by this model: {names}")
    _values = {**defaults, **_overrides}
    return SimpleNamespace(**_values)


def current_params() -> dict[str, Any]:
    return dict(_values)
