"""Safe parsing and structural validation for ``spec.yaml`` documents."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_QUANTITY = re.compile(
    rf"^\s*(?P<number>{_NUMBER})\s*(?P<unit>mm|in)?"
    rf"(?:\s*\^?\s*(?P<power>[23]|²|³))?\s*$",
    re.IGNORECASE,
)
_TOLERANCED = re.compile(
    rf"^\s*(?P<nominal>{_NUMBER})\s*(?P<unit1>mm|in)?"
    rf"(?:\s*\^?\s*(?P<power1>[23]|²|³))?\s*(?:±|\+/-|\+-)\s*"
    rf"(?P<tolerance>{_NUMBER})\s*(?P<unit2>mm|in)?"
    rf"(?:\s*\^?\s*(?P<power2>[23]|²|³))?\s*$",
    re.IGNORECASE,
)


class _UniqueSafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate keys, including YAML merges."""

    def fetch_flow_collection_start(self, TokenClass):
        # Scanner key lookahead can consume many nested '['/'{' tokens before
        # composition starts. Bound that work before recording another level.
        if self.flow_level >= 64:
            raise yaml.YAMLError("spec exceeds the 64-level flow nesting input limit")
        return super().fetch_flow_collection_start(TokenClass)

    def compose_node(self, parent, index):
        # Bound work before Python recursion or YAML merge construction can expand it.
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are unsupported; write each requirement explicitly")
        self._depth = getattr(self, '_depth', 0) + 1
        self._nodes = getattr(self, '_nodes', 0) + 1
        try:
            if self._depth > 64 or self._nodes > 10000:
                raise yaml.YAMLError("spec exceeds the 64-level or 10000-node input limit")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_yaml_int(self, node):
        scalar = self.construct_scalar(node)
        unsigned = scalar.lstrip('+-')
        if ':' in scalar or (len(unsigned) > 1 and unsigned[0] == '0' and unsigned[1].isdigit()):
            raise yaml.YAMLError("ambiguous YAML 1.1 integer; use decimal without leading zeros")
        return super().construct_yaml_int(node)

    def construct_yaml_float(self, node):
        if ':' in self.construct_scalar(node):
            raise yaml.YAMLError("ambiguous YAML 1.1 float; use ordinary decimal notation")
        return super().construct_yaml_float(node)

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(None, None, "expected a mapping node", node.start_mark)
        self.flatten_mapping(node)
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "mapping keys must be hashable",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


_UniqueSafeLoader.add_constructor('tag:yaml.org,2002:int', _UniqueSafeLoader.construct_yaml_int)
_UniqueSafeLoader.add_constructor('tag:yaml.org,2002:float', _UniqueSafeLoader.construct_yaml_float)


@dataclass(frozen=True)
class SpecIssue:
    """A validation issue. ``requirement_index`` is set for per-item errors."""

    message: str
    path: tuple[str | int, ...] = ()
    requirement_index: int | None = None


REQUIREMENT_TYPES = frozenset({
    "bbox", "solids", "holes", "min_wall", "clearance", "mass_g", "symmetric",
    "measure", "material_at_point", "interference", "matches_reference",
})


class SpecValidationError(ValueError):
    """Invalid or unsafe spec input, with parsed data when parsing succeeded."""

    def __init__(
        self,
        message: str,
        *,
        document: dict[str, Any] | None = None,
        issues: tuple[SpecIssue, ...] = (),
    ) -> None:
        super().__init__(message)
        self.document = document
        self.issues = issues


@dataclass(frozen=True)
class NumericRange:
    """Inclusive numeric bounds in canonical mm**power or dimensionless units."""

    minimum: float | None
    maximum: float | None
    power: int
    display_unit: str
    nominal: float | None = None
    tolerance: float | None = None
    implicit_exact: bool = False

    def __post_init__(self):
        if any(value is not None and not math.isfinite(value) for value in (self.minimum,self.maximum,self.nominal,self.tolerance)):
            raise ValueError('range bounds and converted tolerances must remain finite')


def _power_value(raw: str | None) -> int | None:
    if raw is None:
        return None
    if raw in {"²", "2"}:
        return 2
    if raw in {"³", "3"}:
        return 3
    return None


def _unit_factor(unit: str, power: int) -> float:
    if not unit:
        return 1.0
    if unit.lower() == "mm":
        return 1.0
    if unit.lower() == "in":
        return 25.4**power
    raise ValueError(f"unsupported unit {unit!r}; use mm or in")


def _finite_number(value: Any, path: tuple[str | int, ...]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{_path_text(path)} must be a number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f'{_path_text(path)} must be a finite representable number') from exc
    if not math.isfinite(number):
        raise ValueError(f"{_path_text(path)} must be finite")
    return number


def _path_text(path: tuple[str | int, ...]) -> str:
    result = "spec"
    for part in path:
        result += f"[{part}]" if isinstance(part, int) else f".{part}"
    return result


def _quantity(value: Any, *, default_unit: str, power: int, path: tuple[str | int, ...]) -> float:
    """Parse one value and convert it to mm**power (or a dimensionless value)."""
    if isinstance(value, bool):
        raise ValueError(f"{_path_text(path)} must not be a boolean")
    if isinstance(value, (int, float)):
        number = _finite_number(value, path)
        if power == 0:
            return number
        return number * _unit_factor(default_unit, power)
    if not isinstance(value, str):
        raise ValueError(f"{_path_text(path)} must be a finite number or a unit-bearing number")
    match = _QUANTITY.fullmatch(value)
    if not match:
        raise ValueError(f"{_path_text(path)} must be a finite number with optional mm or in units")
    number = float(match.group("number"))
    if not math.isfinite(number):
        raise ValueError(f"{_path_text(path)} must be finite")
    unit = match.group("unit") or ""
    suffix_power = _power_value(match.group("power"))
    if power == 0:
        if unit or suffix_power is not None:
            raise ValueError(f"{_path_text(path)} does not accept a length unit")
        return number
    if unit and suffix_power is not None and suffix_power != power:
        raise ValueError(f"{_path_text(path)} uses the wrong unit exponent")
    if not unit:
        if suffix_power is not None:
            raise ValueError(f"{_path_text(path)} has an exponent without mm or in")
        unit = default_unit
    return number * _unit_factor(unit, power)


def parse_numeric_range(
    value: Any,
    *,
    default_unit: str,
    power: int = 1,
    path: tuple[str | int, ...] = (),
) -> NumericRange:
    """Parse an exact, ``nominal ± tolerance``, or min/max range."""
    display_unit = default_unit if power else ""
    if isinstance(value, str):
        match = _TOLERANCED.fullmatch(value)
        if match:
            nominal_unit = match.group("unit1") or match.group("unit2") or default_unit
            tolerance_unit = match.group("unit2") or match.group("unit1") or default_unit
            nominal_power = _power_value(match.group("power1"))
            tolerance_power = _power_value(match.group("power2"))
            if power and any(p is not None and p != power for p in (nominal_power, tolerance_power)):
                raise ValueError(f"{_path_text(path)} uses the wrong unit exponent")
            if not power and (match.group("unit1") or match.group("unit2") or nominal_power or tolerance_power):
                raise ValueError(f"{_path_text(path)} does not accept a length unit")
            nominal = _quantity(
                f"{match.group('nominal')} {nominal_unit}" if power else match.group("nominal"),
                default_unit=default_unit,
                power=power,
                path=path,
            )
            tolerance = _quantity(
                f"{match.group('tolerance')} {tolerance_unit}" if power else match.group("tolerance"),
                default_unit=default_unit,
                power=power,
                path=path,
            )
            if tolerance < 0:
                raise ValueError(f"{_path_text(path)} tolerance must be non-negative")
            if match.group("unit1") or match.group('unit2'):
                display_unit = nominal_unit.lower()
            return NumericRange(nominal - tolerance, nominal + tolerance, power, display_unit, nominal, tolerance)

    if isinstance(value, dict):
        keys = set(value)
        if keys == {"nominal", "tolerance"}:
            nominal = _quantity(value["nominal"], default_unit=default_unit, power=power, path=path + ("nominal",))
            tolerance = _quantity(value["tolerance"], default_unit=default_unit, power=power, path=path + ("tolerance",))
            if tolerance < 0:
                raise ValueError(f"{_path_text(path)} tolerance must be non-negative")
            return NumericRange(nominal - tolerance, nominal + tolerance, power, display_unit, nominal, tolerance)
        if keys == {"equals"}:
            exact = _quantity(value["equals"], default_unit=default_unit, power=power, path=path + ("equals",))
            return NumericRange(exact, exact, power, display_unit)
        if not keys or not keys <= {"min", "max"}:
            raise ValueError(f"{_path_text(path)} must contain only min/max or nominal/tolerance")
        minimum = (
            _quantity(value["min"], default_unit=default_unit, power=power, path=path + ("min",))
            if "min" in value
            else None
        )
        maximum = (
            _quantity(value["max"], default_unit=default_unit, power=power, path=path + ("max",))
            if "max" in value
            else None
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"{_path_text(path)} min must not exceed max")
        return NumericRange(minimum, maximum, power, display_unit)

    exact = _quantity(value, default_unit=default_unit, power=power, path=path)
    return NumericRange(exact, exact, power, display_unit, implicit_exact=True)


def _require_mapping(value: Any, path: tuple[str | int, ...]) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{_path_text(path)} must be a mapping with string keys")
    return value


def _check_keys(value: dict[str, Any], allowed: set[str], path: tuple[str | int, ...]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{_path_text(path)} has unsupported field(s): {', '.join(unknown)}")


def _count(value: Any, path: tuple[str | int, ...]) -> None:
    number = _finite_number(value, path)
    if number < 0 or not number.is_integer():
        raise ValueError(f"{_path_text(path)} must be a non-negative integer count")


def _validate_measure_shape(measure: Any, path: tuple[str | int, ...]) -> None:
    fields = _require_mapping(measure, path)
    _check_keys(fields, {"kind", "a", "b", "selector"}, path)
    kind = fields.get("kind")
    if not isinstance(kind, str) or kind not in {"distance", "radius", "angle", "area", "volume", "length"}:
        raise ValueError(f"{_path_text(path)}.kind must be distance, radius, angle, area, volume, or length")
    selector_a = fields.get("a", fields.get("selector"))
    if not isinstance(selector_a, str) or not selector_a.strip():
        raise ValueError(f"{_path_text(path)} requires a non-empty selector in a or selector")
    if kind == "distance":
        selector_b = fields.get("b")
        if not isinstance(selector_b, str) or not selector_b.strip():
            raise ValueError(f"{_path_text(path)} distance requires a non-empty b selector")
    elif "b" in fields:
        raise ValueError(f"{_path_text(path)} only distance accepts b")


def _validate_requirement(req: dict[str, Any], index: int, units: str) -> list[SpecIssue]:
    path = ("requirements", index)
    issues: list[SpecIssue] = []
    req_id = req.get("id")
    if not isinstance(req_id, str) or not req_id.strip():
        issues.append(SpecIssue("id must be a non-empty string", path + ("id",), index))
    level = req.get("level", "error")
    if not isinstance(level, str) or level not in {"error", "warn"}:
        issues.append(SpecIssue("level must be 'error' or 'warn'", path + ("level",), index))

    type_fields = [key for key in req if key not in {"id", "level"}]
    known = [key for key in type_fields if key in REQUIREMENT_TYPES]
    if not known:
        # Unknown types are deliberately retained so the evaluator can emit a skip.
        return issues
    if len(known) != 1:
        issues.append(SpecIssue("a requirement must declare exactly one supported measurement type", path, index))
        return issues

    field = known[0]
    allowed = {
        "bbox": {"id", "level", "bbox"},
        "solids": {"id", "level", "solids"},
        "holes": {"id", "level", "holes"},
        "min_wall": {"id", "level", "min_wall"},
        "clearance": {"id", "level", "clearance"},
        "mass_g": {"id", "level", "mass_g"},
        "symmetric": {"id", "level", "symmetric"},
        "measure": {"id", "level", "measure", "equals", "min", "max"},
        "material_at_point": {"id", "level", "material_at_point"},
        "interference": {"id", "level", "interference"},
        "matches_reference": {"id", "level", "matches_reference"},
    }[field]
    try:
        _check_keys(req, allowed, path)
        body = req[field]
        if field == "bbox":
            values = _require_mapping(body, path + (field,))
            _check_keys(values, {"x", "y", "z"}, path + (field,))
            if not values:
                raise ValueError(f"{_path_text(path + (field,))} must include at least one axis")
            for axis, axis_value in values.items():
                parse_numeric_range(axis_value, default_unit=units, path=path + (field, axis))
        elif field == "solids":
            value = body
            if isinstance(body, dict):
                _check_keys(body, {"count"}, path + (field,))
                value = body.get("count")
            _count(value, path + (field,))
        elif field == "holes":
            values = _require_mapping(body, path + (field,))
            _check_keys(values, {"count", "diameter", "pattern", "scope", "through"}, path + (field,))
            if values.get("scope", "all") not in {"all", "cylindrical"}:
                raise ValueError("holes.scope must be all or cylindrical")
            if "through" in values and not isinstance(values["through"], bool):
                raise ValueError("holes.through must be true or false")
            if "count" not in values and "diameter" not in values:
                raise ValueError(f"{_path_text(path + (field,))} requires count or diameter")
            if "count" in values:
                _count(values["count"], path + (field, "count"))
                if values["count"] == 0 and "diameter" in values:
                    raise ValueError(f"{_path_text(path + (field,))} cannot require a diameter for zero holes")
            if "diameter" in values:
                parse_numeric_range(values["diameter"], default_unit=units, path=path + (field, "diameter"))
            if "pattern" in values:
                pattern = _require_mapping(values["pattern"], path + (field, "pattern"))
                _check_keys(pattern, {"grid"}, path + (field, "pattern"))
                grid = pattern.get("grid")
                if not isinstance(grid, list) or len(grid) != 2:
                    raise ValueError(f"{_path_text(path + (field, 'pattern', 'grid'))} must be [x_span, y_span]")
                for axis, span in zip(("x", "y"), grid, strict=True):
                    parsed = parse_numeric_range(span, default_unit=units, path=path + (field, "pattern", "grid", axis))
                    if parsed.minimum is None or parsed.minimum <= 0:
                        raise ValueError(f"{_path_text(path + (field, 'pattern', 'grid', axis))} must be positive")
        elif field == "min_wall":
            parse_numeric_range(body, default_unit=units, path=path + (field,))
        elif field == "clearance":
            values = _require_mapping(body, path + (field,))
            _check_keys(values, {"with", "min", "max"}, path + (field,))
            asset = values.get("with")
            if not isinstance(asset, str) or not asset.strip():
                raise ValueError(f"{_path_text(path + (field,))}.with must be a project-relative STEP path")
            if "min" not in values and "max" not in values:
                raise ValueError(f"{_path_text(path + (field,))} requires min or max")
            parse_numeric_range({key: values[key] for key in ("min", "max") if key in values}, default_unit=units, path=path + (field,))
        elif field == "mass_g":
            values = body if isinstance(body, dict) else {"max": body}
            _check_keys(values, {"min", "max"}, path + (field,))
            if not values:
                raise ValueError(f"{_path_text(path + (field,))} requires min or max")
            parse_numeric_range(values, default_unit=units, power=0, path=path + (field,))
        elif field == "symmetric":
            values = _require_mapping(body, path + (field,))
            _check_keys(values, {"plane", "tolerance"}, path + (field,))
            if not isinstance(values.get("plane"), str) or values.get("plane") not in {"XY", "XZ", "YZ"}:
                raise ValueError(f"{_path_text(path + (field,))}.plane must be XY, XZ, or YZ")
            if "tolerance" not in values:
                raise ValueError(f"{_path_text(path + (field,))}.tolerance is required")
            tolerance = parse_numeric_range(values["tolerance"], default_unit=units, path=path + (field, "tolerance"))
            if tolerance.minimum is None or tolerance.minimum < 0 or tolerance.minimum != tolerance.maximum:
                raise ValueError(f"{_path_text(path + (field,))}.tolerance must be one non-negative finite length")
        elif field == "measure":
            _validate_measure_shape(body, path + (field,))
            if not any(key in req for key in ("equals", "min", "max")):
                raise ValueError(f"{_path_text(path)} custom measure requires equals or min/max")
            if "equals" in req and ("min" in req or "max" in req):
                raise ValueError(f"{_path_text(path)} cannot combine equals with min/max")
            measure_kind = body["kind"]
            power = {"distance": 1, "radius": 1, "length": 1, "area": 2, "volume": 3, "angle": 0}[measure_kind]
            if "equals" in req:
                parse_numeric_range(req["equals"], default_unit=units, power=power, path=path + ("equals",))
            else:
                parse_numeric_range({key: req[key] for key in ("min", "max") if key in req}, default_unit=units, power=power, path=path)
        elif field == "material_at_point":
            values = _require_mapping(body, path + (field,))
            _check_keys(values, {"point", "expected"}, path + (field,))
            point = values.get("point")
            if not isinstance(point, list) or len(point) != 3:
                raise ValueError(f"{_path_text(path + (field,))}.point must have three coordinates")
            for axis, coordinate in zip("xyz", point, strict=True):
                coordinate_range = parse_numeric_range(coordinate, default_unit=units, path=path + (field, "point", axis))
                if coordinate_range.minimum != coordinate_range.maximum:
                    raise ValueError(f"{_path_text(path + (field, 'point', axis))} must be one exact coordinate")
            if not isinstance(values.get("expected"), str) or values.get("expected") not in {"solid", "void"}:
                raise ValueError(f"{_path_text(path + (field,))}.expected must be 'solid' or 'void'")
        elif field == "interference":
            values = _require_mapping(body, path + (field,))
            _check_keys(values, {"volume_threshold_mm3"}, path + (field,))
            if "volume_threshold_mm3" in values:
                threshold = _finite_number(values["volume_threshold_mm3"], path + (field, "volume_threshold_mm3"))
                if threshold < 0:
                    raise ValueError(f"{_path_text(path + (field, 'volume_threshold_mm3'))} must be non-negative")
        elif field == "matches_reference":
            values = _require_mapping(body, path + (field,))
            _check_keys(values, {"path", "f_score"}, path + (field,))
            reference_path = values.get("path")
            if not isinstance(reference_path, str) or not reference_path.strip():
                raise ValueError(f"{_path_text(path + (field,))}.path must be a non-empty project-relative file path")
            score = _require_mapping(values.get("f_score"), path + (field, "f_score"))
            _check_keys(score, {"min", "tau_pct_of_diagonal"}, path + (field, "f_score"))
            if "min" not in score:
                raise ValueError(f"{_path_text(path + (field, 'f_score'))}.min is required")
            minimum = _finite_number(score["min"], path + (field, "f_score", "min"))
            if not 0.0 <= minimum <= 1.0:
                raise ValueError(f"{_path_text(path + (field, 'f_score', 'min'))} must be between 0 and 1")
            tau_pct = _finite_number(
                score.get("tau_pct_of_diagonal", 0.1),
                path + (field, "f_score", "tau_pct_of_diagonal"),
            )
            if not 0.0 < tau_pct <= 100.0:
                raise ValueError(
                    f"{_path_text(path + (field, 'f_score', 'tau_pct_of_diagonal'))} must be greater than 0 and at most 100"
                )
    except (ValueError, TypeError, KeyError) as exc:
        issues.append(SpecIssue(str(exc), path, index))
    return issues


def _to_plain_data(value: Any, path: tuple[str | int, ...] = (), active: set[int] | None = None, budget: list[int] | None = None) -> Any:
    if active is None:
        active = set()
    if budget is None:
        budget = [10000]
    budget[0] -= 1
    if len(path) > 64 or budget[0] < 0:
        raise ValueError("spec exceeds the 64-level or 10000-node input limit")
    if isinstance(value, dict):
        marker = id(value)
        if marker in active:
            raise ValueError(f"{_path_text(path)} contains a recursive alias")
        active.add(marker)
        plain: dict[str, Any] = {}
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError(f"{_path_text(path)} mapping keys must be strings")
                plain[key] = _to_plain_data(item, path + (key,), active, budget)
        finally:
            active.remove(marker)
        return plain
    if isinstance(value, list):
        marker = id(value)
        if marker in active:
            raise ValueError(f"{_path_text(path)} contains a recursive alias")
        active.add(marker)
        try:
            return [_to_plain_data(item, path + (index,), active, budget) for index, item in enumerate(value)]
        finally:
            active.remove(marker)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f"{_path_text(path)} contains an unsupported value of type {type(value).__name__}")


def _nonfinite_issues(value: Any, path: tuple[str | int, ...], requirement_index: int) -> list[SpecIssue]:
    if isinstance(value, float) and not math.isfinite(value):
        return [SpecIssue(f"{_path_text(path)} must be finite", path, requirement_index)]
    if isinstance(value, dict):
        return [issue for key, item in value.items() for issue in _nonfinite_issues(item, path + (key,), requirement_index)]
    if isinstance(value, list):
        return [issue for index, item in enumerate(value) for issue in _nonfinite_issues(item, path + (index,), requirement_index)]
    return []


def _parse_input(value: str | bytes | dict[str, Any]) -> tuple[dict[str, Any] | None, tuple[SpecIssue, ...]]:
    try:
        if isinstance(value, bytes):
            source: Any = value.decode("utf-8-sig")
        elif isinstance(value, str):
            source = value
        elif isinstance(value, dict):
            source = value
        else:
            raise ValueError("spec input must be YAML text, bytes, or a dict")
        if isinstance(source, str):
            if len(source.encode('utf-8')) > 1024 * 1024:
                raise ValueError("spec exceeds the 1 MiB text limit")
            try:
                loaded = yaml.load(source, Loader=_UniqueSafeLoader)
            except yaml.YAMLError as exc:
                raise ValueError(f"invalid YAML: {exc}") from exc
        else:
            loaded = source
        plain = _to_plain_data(loaded)
        if not isinstance(plain, dict):
            raise ValueError("spec document must be a mapping")
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        return None, (SpecIssue(str(exc)),)

    issues: list[SpecIssue] = []
    allowed_root = {"units", "process", "material", "requirements", "process_settings"}
    unknown_root = sorted(set(plain) - allowed_root)
    for key in unknown_root:
        issues.append(SpecIssue(f"unsupported top-level field: {key}", (key,)))

    units = plain.get("units", "mm")
    if not isinstance(units, str) or units not in {"mm", "in"}:
        issues.append(SpecIssue("units must be 'mm' or 'in'", ("units",)))
        units = "mm"
    plain["units"] = units
    for optional_text in ("process", "material"):
        if optional_text in plain and (not isinstance(plain[optional_text], str) or not plain[optional_text].strip()):
            issues.append(SpecIssue(f"{optional_text} must be a non-empty string", (optional_text,)))
    if 'process_settings' in plain:
        if not isinstance(plain['process_settings'],dict):
            issues.append(SpecIssue('process_settings must be a mapping',('process_settings',)))
        else:
            issues.extend(_nonfinite_issues(plain['process_settings'],('process_settings',),None))

    requirements = plain.get("requirements")
    if not isinstance(requirements, list):
        issues.append(SpecIssue("requirements must be a list", ("requirements",)))
        return plain, tuple(issues)

    seen: dict[str, int] = {}
    for index, raw_req in enumerate(requirements):
        if not isinstance(raw_req, dict):
            issues.append(SpecIssue("requirement must be a mapping", ("requirements", index), index))
            continue
        issues.extend(_nonfinite_issues(raw_req, ("requirements", index), index))
        issues.extend(_validate_requirement(raw_req, index, units))
        req_id = raw_req.get("id")
        if isinstance(req_id, str) and req_id.strip():
            if req_id != req_id.strip():
                issues.append(SpecIssue("id must not contain leading or trailing whitespace", ("requirements", index, "id"), index))
            if req_id in seen:
                previous_index = seen[req_id]
                issues.append(SpecIssue(f"duplicate requirement id {req_id!r}", ("requirements", previous_index, "id"), previous_index))
                issues.append(SpecIssue(f"duplicate requirement id {req_id!r}", ("requirements", index, "id"), index))
            else:
                seen[req_id] = index
    return plain, tuple(issues)


def load_spec(value: str | bytes | dict[str, Any]) -> dict[str, Any]:
    """Load a spec with safe YAML parsing and strict structural checks.

    Unknown requirement types remain parseable so ``spec_checks`` can emit an
    explicit skip for each one. Malformed input raises ``SpecValidationError``.
    """
    document, issues = _parse_input(value)
    if issues:
        details = "; ".join(issue.message for issue in issues)
        raise SpecValidationError(details, document=document, issues=issues)
    assert document is not None
    return document


def parse_spec_for_checks(value: str | bytes | dict[str, Any]) -> tuple[dict[str, Any] | None, tuple[SpecIssue, ...]]:
    """Internal tolerant parse used to retain one diagnostic per requirement."""
    return _parse_input(value)
