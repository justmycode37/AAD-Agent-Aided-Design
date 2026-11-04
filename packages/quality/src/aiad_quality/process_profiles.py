"""Process-profile settings and their explicit screening assumptions.

This module is deliberately independent of build123d and OCCT.  Settings are
read only from ``ctx.spec.process_settings``; model parameters and cached
metrics are not process evidence.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

from ._checks import finite


PROCESS_NAMES = ("fdm_pla", "sla", "cnc_3axis", "sheet_metal", "laser", "injection")

_COMMON_DEFAULTS: dict[str, Any] = {
    "level": "warn",
    "tolerance_mm": 0.01,
    "angle_tolerance_deg": 0.1,
    "max_faces": 128,
    "max_samples": 512,
}

_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "fdm_pla": {
        "build_direction": (0.0, 0.0, 1.0),
        "min_downward_slope_deg": 45.0,
    },
    "sla": {
        "build_direction": (0.0, 0.0, 1.0),
        "min_downward_slope_deg": 45.0,
    },
    "cnc_3axis": {
        "approach_length_mm": 25.0,
        "approach_clearance_mm": 0.25,
        "tool_axis_tolerance_deg": 2.0,
    },
    "sheet_metal": {},
    "laser": {},
    "injection": {
        "pull_direction": (0.0, 0.0, 1.0),
        "min_draft_deg": 1.0,
    },
}

_PROFILE_KEYS: dict[str, set[str]] = {
    "fdm_pla": {
        "build_direction",
        "min_downward_slope_deg",
        "min_wall_mm",
        "nozzle_diameter_mm",
        "wall_line_count",
    },
    "sla": {
        "build_direction",
        "min_downward_slope_deg",
        "min_wall_mm",
    },
    "cnc_3axis": {
        "tool_axis",
        "tool_axis_tolerance_deg",
        "tool_diameter_mm",
        "tool_radius_mm",
        "approach_probe_radius_mm",
        "approach_length_mm",
        "approach_clearance_mm",
        "minimum_internal_radius_mm",
    },
    "sheet_metal": {
        "stock_thickness_mm",
        "minimum_inside_bend_radius_mm",
    },
    "laser": {
        "kerf_mm",
        "minimum_feature_mm",
    },
    "injection": {
        "pull_direction",
        "min_draft_deg",
    },
}

_POSITIVE_KEYS = {
    "min_wall_mm",
    "nozzle_diameter_mm",
    "tool_diameter_mm",
    "tool_radius_mm",
    "approach_probe_radius_mm",
    "approach_length_mm",
    "minimum_internal_radius_mm",
    "stock_thickness_mm",
    "minimum_inside_bend_radius_mm",
    "kerf_mm",
    "minimum_feature_mm",
}
_NONNEGATIVE_KEYS = {"approach_clearance_mm"}
_ANGLE_KEYS = {
    "min_downward_slope_deg": (0.0, 90.0),
    "min_draft_deg": (0.0, 90.0),
    "tool_axis_tolerance_deg": (0.0, 90.0),
    "angle_tolerance_deg": (0.0, 90.0),
}
_VECTOR_KEYS = {"build_direction", "pull_direction", "tool_axis"}


@dataclass(frozen=True)
class SettingsResult:
    values: dict[str, Any] | None
    error: str | None = None


def _vector(value: Any, key: str) -> tuple[float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{key} must be a three-number vector [x, y, z]")
    components = tuple(finite(item) for item in value)
    length = math.sqrt(sum(component * component for component in components))
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError(f"{key} must have a finite nonzero direction")
    return tuple(component / length for component in components)  # type: ignore[return-value]


def _numeric(value: Any, key: str) -> float:
    number = finite(value)
    if key in _POSITIVE_KEYS and number <= 0.0:
        raise ValueError(f"{key} must be greater than 0")
    if key in _NONNEGATIVE_KEYS and number < 0.0:
        raise ValueError(f"{key} must be at least 0")
    if key in _ANGLE_KEYS:
        low, high = _ANGLE_KEYS[key]
        if not low <= number <= high:
            raise ValueError(f"{key} must be between {low:g} and {high:g} degrees")
    if key == "tolerance_mm" and number <= 0.0:
        raise ValueError("tolerance_mm must be greater than 0")
    return number


def process_settings(ctx: Any, process: str) -> SettingsResult:
    """Validate and complete process settings; return one actionable error.

    Millimeter fields are always stated in mm, independent of ``ctx.units``.
    Direction fields are normalized vectors.  Defaults here are screening
    assumptions, never claims of universal manufacturing limits.
    """
    spec = getattr(ctx, "spec", None)
    if spec is None:
        spec = {}
    if not isinstance(spec, Mapping):
        return SettingsResult(None, "ctx.spec must be a mapping to read process_settings")

    raw = spec.get("process_settings", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        return SettingsResult(None, "spec.process_settings must be a mapping")

    allowed = set(_COMMON_DEFAULTS) | _PROFILE_KEYS[process]
    unexpected = sorted(str(key) for key in raw if key not in allowed)
    if unexpected:
        return SettingsResult(
            None,
            "unknown process_settings key(s): " + ", ".join(unexpected),
        )

    values = dict(_COMMON_DEFAULTS)
    values.update(_PROFILE_DEFAULTS[process])

    level = raw.get("level", values["level"])
    if level not in ("warn", "error"):
        return SettingsResult(None, "process_settings.level must be 'warn' or 'error'")
    values["level"] = level

    for key in ("tolerance_mm", "angle_tolerance_deg"):
        if key in raw:
            try:
                values[key] = _numeric(raw[key], key)
            except (TypeError, ValueError) as exc:
                return SettingsResult(None, str(exc))

    for key in ("max_faces", "max_samples"):
        if key in raw:
            item = raw[key]
            if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                return SettingsResult(None, f"{key} must be a positive integer")
            upper = 512 if key == "max_faces" else 4096
            if item > upper:
                return SettingsResult(None, f"{key} must be no greater than {upper}")
            values[key] = item

    for key in _PROFILE_KEYS[process]:
        if key not in raw:
            continue
        try:
            if key in _VECTOR_KEYS:
                values[key] = _vector(raw[key], key)
            elif key == "wall_line_count":
                item = raw[key]
                if isinstance(item, bool) or not isinstance(item, int) or item<1:
                    raise ValueError("wall_line_count must be a positive integer")
                finite(item)
                values[key] = item
            else:
                values[key] = _numeric(raw[key], key)
        except (TypeError, ValueError) as exc:
            return SettingsResult(None, str(exc))

    if "tool_diameter_mm" in values and "tool_radius_mm" in values:
        if "tool_diameter_mm" in raw and "tool_radius_mm" in raw:
            return SettingsResult(None, "configure tool_diameter_mm or tool_radius_mm, not both")

    if process == "fdm_pla" and "nozzle_diameter_mm" in raw:
        values.setdefault("wall_line_count", 2)
    return SettingsResult(values)
