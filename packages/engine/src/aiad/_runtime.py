"""Kernel-free helpers shared by model scripts and the worker."""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

_overrides: dict[str, Any] = {}
_values: dict[str, Any] = {}
_definitions: dict[str, dict[str, Any]] = {}
_declared = False


class ModelInputError(ValueError):
    """A model declaration or parameter override is invalid."""


@dataclass(frozen=True, slots=True)
class Param:
    """A parameter declaration with optional numeric bounds and a unit."""

    value: Any
    min: int | float | None = None
    max: int | float | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        _validate_value(self.value, "Param.value")
        if self.min is not None:
            _finite_number(self.min, "Param.min")
        if self.max is not None:
            _finite_number(self.max, "Param.max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ModelInputError("Param.min must be less than or equal to Param.max")
        if self.unit is not None and (not isinstance(self.unit, str) or not self.unit.strip()):
            raise ModelInputError("Param.unit must be non-empty text or None")
        if (self.min is not None or self.max is not None) and not _is_number(self.value):
            raise ModelInputError("Param bounds are supported only for a numeric value")
        _check_range(self.value, self.min, self.max, "Param.value")


def configure_params(overrides: Mapping[str, Any] | None = None) -> None:
    """Set overrides for the next model execution in this worker process."""
    global _overrides, _values, _definitions, _declared
    if overrides is not None and not isinstance(overrides, Mapping):
        raise ModelInputError("parameter overrides must be a mapping")
    _overrides = deepcopy(dict(overrides or {}))
    if any(not isinstance(name, str) for name in _overrides):
        raise ModelInputError("parameter override names must be text")
    _values = {}
    _definitions = {}
    _declared = False


def params(**defaults: Any) -> SimpleNamespace:
    """Declare model parameters, validate overrides, and return their values."""
    global _values, _definitions, _declared
    if _declared:
        raise ModelInputError("declare model parameters in a single params() call")
    unknown = sorted(set(_overrides) - set(defaults))
    if unknown:
        names = ", ".join(unknown)
        raise ModelInputError(f"parameter override(s) are not declared by this model: {names}")

    values: dict[str, Any] = {}
    definitions: dict[str, dict[str, Any]] = {}
    for name, declaration in defaults.items():
        if not name.isidentifier() or name.startswith("_"):
            raise ModelInputError(f"parameter name {name!r} must be a public Python identifier")
        if isinstance(declaration, Param):
            default = declaration.value
            minimum = declaration.min
            maximum = declaration.max
            unit = declaration.unit
        else:
            default = declaration
            minimum = maximum = unit = None

        _validate_value(default, f"parameter {name!r}")
        selected = _overrides.get(name, default)
        selected = _validate_override(name, default, selected, minimum, maximum)
        values[name] = deepcopy(selected)
        definitions[name] = {
            "value": selected,
            "min": minimum,
            "max": maximum,
            "unit": unit,
        }

    _values = values
    _definitions = definitions
    _declared = True
    return SimpleNamespace(**deepcopy(values))


def validate_params() -> None:
    """Reject overrides when the script never declared parameters."""
    if _overrides and not _declared:
        raise ModelInputError("parameter overrides require a params() declaration in the model")


def current_params() -> dict[str, Any]:
    """Return the applied parameter values for the current model execution."""
    return deepcopy(_values)


def current_param_definitions() -> dict[str, dict[str, Any]]:
    """Return declared parameter values and optional slider metadata."""
    return deepcopy(_definitions)


def _validate_override(
    name: str,
    default: Any,
    value: Any,
    minimum: int | float | None,
    maximum: int | float | None,
) -> Any:
    label = f"override for parameter {name!r}"
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ModelInputError(f"{label} must be text")
        return value
    if _is_number(default):
        if not _is_number(value):
            raise ModelInputError(f"{label} must be a finite number")
        _finite_number(value, label)
        _check_range(value, minimum, maximum, label)
        return value
    if isinstance(default, (tuple, list)):
        if not isinstance(value, (tuple, list)) or len(value) != len(default):
            raise ModelInputError(f"{label} must contain {len(default)} values")
        checked = [
            _validate_override(f"{name}[{index}]", default_item, item, None, None)
            for index, (default_item, item) in enumerate(zip(default, value, strict=True))
        ]
        return tuple(checked) if isinstance(default, tuple) else checked
    raise ModelInputError(f"{label} must be a number, text, or a tuple/list of those values")


def _validate_value(value: Any, label: str) -> None:
    if _is_number(value):
        _finite_number(value, label)
        return
    if isinstance(value, str):
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _validate_value(item, f"{label}[{index}]")
        return
    raise ModelInputError(f"{label} must be a number, text, or a tuple/list of those values")


def _finite_number(value: Any, label: str) -> None:
    if not _is_number(value):
        raise ModelInputError(f"{label} must be a finite number")
    try:
        finite = math.isfinite(float(value))
    except OverflowError:
        finite = False
    if not finite:
        raise ModelInputError(f"{label} must be finite")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_range(
    value: Any,
    minimum: int | float | None,
    maximum: int | float | None,
    label: str,
) -> None:
    if not _is_number(value):
        return
    if minimum is not None and value < minimum:
        raise ModelInputError(f"{label}={value!r} is below the declared minimum {minimum!r}")
    if maximum is not None and value > maximum:
        raise ModelInputError(f"{label}={value!r} is above the declared maximum {maximum!r}")
