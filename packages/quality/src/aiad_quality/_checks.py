"""Contract-compatible checks without importing a kernel during package import."""
from __future__ import annotations
from dataclasses import asdict, is_dataclass
from importlib import import_module
from collections.abc import Mapping
from typing import Any
import math

def make_check(**fields: Any) -> Any:
    """Construct S1's contractual Check without importing a kernel here."""
    return import_module('aiad.engine.types').Check(**fields)


def record(check: Any) -> dict[str, Any]:
    if isinstance(check, Mapping):
        return dict(check)
    if is_dataclass(check):
        return asdict(check)
    if hasattr(check, 'model_dump'):
        return check.model_dump()
    raise TypeError('Check is not a contract mapping, dataclass or model')


def finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError('Expected a finite number (booleans are not measurements)')
    try:
        result=float(value)
    except (OverflowError,ValueError) as exc:
        raise ValueError('Number is outside the finite measurement range') from exc
    if not math.isfinite(result):
        raise ValueError('Expected a finite number')
    return result


def mm_per_unit(units: str) -> float:
    if units == 'mm':
        return 1.0
    if units == 'in':
        return 25.4
    raise ValueError('Units must be mm or in')
