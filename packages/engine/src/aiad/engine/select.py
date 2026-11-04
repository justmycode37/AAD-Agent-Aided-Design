"""Semantic, literal-only selectors for faces, edges, and vertices.

Selectors are expressions such as ``face(feature='plate', where='max_z')``.
They are parsed as Python syntax and their arguments are read with
``ast.literal_eval``; model expressions are never evaluated here.
"""

from __future__ import annotations

import ast
import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .types import Candidate, Entity, Failure

_TOPOLOGY_FUNCTIONS = {"face": "face", "edge": "edge", "vertex": "vertex"}
_ACCESSORS = {"face": "faces", "edge": "edges", "vertex": "vertices"}
_FIELDS = ("feature", "kind", "where", "max_min_xyz", "parallel_xyz", "nth")
_AXES = {"x": 0, "y": 1, "z": 2}
_VECTOR_TOLERANCE = 1e-7


class SelectorError(ValueError):
    """A selector could not be resolved; ``failure`` carries its wire result."""

    def __init__(self, failure: Failure):
        self.failure = failure
        super().__init__(failure.message)


@dataclass(frozen=True, slots=True)
class _Spec:
    function: str
    topology: str
    feature: str | None
    geometry_kind: str | None
    extreme: tuple[int, str] | None
    parallel: tuple[float, float, float] | None
    nth: int | None
    original_fields: tuple[tuple[str, Any], ...]


@dataclass
class _Match:
    entity: Entity
    topology: Any | None


def resolve(ctx: Any, selector: str, expect: int | None = None) -> list[Entity]:
    """Resolve one semantic selector against a live or serialized context.

    When ``expect`` is omitted, a single match is required. An explicit count
    allows a selector to intentionally resolve to multiple entities. A
    selector containing ``nth`` always requests one entity.
    """
    spec = _parse_selector(selector)
    if expect is not None and (isinstance(expect, bool) or not isinstance(expect, int) or expect < 0):
        raise _bad_selector(selector, "expect must be a non-negative integer")

    available = _collect(ctx, spec)
    matches = _filter(available, spec)
    matches.sort(key=lambda match: _semantic_key(match.entity))

    if not matches:
        if expect == 0 and spec.nth is None:
            return []
        available.sort(key=lambda match: _semantic_key(match.entity))
        raise _selector_failure(
            "SELECTOR_EMPTY", selector,
            "The selector matched no entities. Candidates describe the available topology of this kind.",
            available, candidate_spec=_parse_selector(f"{spec.function}()"),
        )
    if not _all_semantically_distinct(matches):
        raise _selector_failure(
            "SELECTOR_AMBIGUOUS", selector,
            "The selector's semantic ordering contains tied candidates; add a distinguishing feature or geometry filter.",
            matches,
        )
    if spec.nth is not None:
        if spec.nth >= len(matches):
            raise _selector_failure(
                "SELECTOR_EMPTY",
                selector,
                f"The selector has no candidate at semantic position {spec.nth}.",
                matches,
            )
        selected = [matches[spec.nth]]
    else:
        selected = matches

    actual = len(selected)
    expected = 1 if expect is None else expect
    if actual != expected:
        raise _selector_failure(
            "SELECTOR_AMBIGUOUS",
            selector,
            f"The selector matched {actual} entities; {expected} were expected.",
            matches,
        )

    result: list[Entity] = []
    for match in selected:
        entity = match.entity
        if spec.nth is not None:
            rank = spec.nth
        elif len(matches) > 1:
            rank = matches.index(match)
        else:
            rank = None
        entity.selector = _candidate_selector(spec, rank)
        if match.topology is not None:
            # Private runtime binding is intentionally absent from dataclass/
            # JSON records. An index alone is never an exact shape reference.
            entity._aiad_binding = (ctx, ctx.shape, getattr(ctx.shape, "wrapped", None), match.topology)
        result.append(entity)
    return result


def entity_shape(ctx: Any, entity: Entity) -> Any:
    """Return topology for an entity resolved in this same live context.

    ``Entity.index`` is revision-local and is not a persistent selector. Call
    this only with the context from which the entity was resolved.
    """
    shape = getattr(ctx, "shape", None)
    if shape is None:
        raise ValueError("entity_shape requires the live shape from the entity's context")
    if entity.kind not in _ACCESSORS:
        raise ValueError(f"unsupported entity kind {entity.kind!r}")
    binding = getattr(entity, "_aiad_binding", None)
    if binding is None or binding[0] is not ctx or binding[1] is not shape or binding[2] is not getattr(shape, "wrapped", None):
        raise ValueError("resolve the entity in this same unchanged live context before measuring it")
    return binding[3]


def _parse_selector(selector: str) -> _Spec:
    if not isinstance(selector, str):
        raise _bad_selector(str(selector), "selector must be text")
    try:
        expression = ast.parse(selector, mode="eval").body
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise _bad_selector(selector, "selector must be a literal face(...), edge(...), or vertex(...) expression") from exc
    if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name):
        raise _bad_selector(selector, "selector must call face(...), edge(...), or vertex(...)")
    function = expression.func.id
    topology = _TOPOLOGY_FUNCTIONS.get(function)
    if topology is None:
        raise _bad_selector(selector, "selector function must be face, edge, or vertex")
    if expression.args:
        raise _bad_selector(selector, "selector arguments must be named literals")

    fields: dict[str, Any] = {}
    for keyword in expression.keywords:
        if keyword.arg is None:
            raise _bad_selector(selector, "selector expansion with ** is not supported")
        if keyword.arg not in _FIELDS:
            raise _bad_selector(selector, f"unsupported selector field {keyword.arg!r}")
        if keyword.arg in fields:
            raise _bad_selector(selector, f"selector field {keyword.arg!r} was supplied more than once")
        try:
            fields[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
            raise _bad_selector(selector, f"selector field {keyword.arg!r} must be a literal value") from exc

    feature = fields.get("feature")
    if feature is not None and (not isinstance(feature, str) or not feature.strip()):
        raise _bad_selector(selector, "feature must be a non-empty string")
    geometry_kind = fields.get("kind")
    if geometry_kind is not None and (not isinstance(geometry_kind, str) or not geometry_kind):
        raise _bad_selector(selector, "kind must be a non-empty geometric type name")
    nth = fields.get("nth")
    if nth is not None and (isinstance(nth, bool) or not isinstance(nth, int) or nth < 0):
        raise _bad_selector(selector, "nth must be a non-negative integer")

    extreme = _parse_extreme(fields.get("max_min_xyz"), selector)
    parallel = _parse_parallel(fields.get("parallel_xyz"), selector)
    where = fields.get("where")
    if where is not None:
        if not isinstance(where, str):
            raise _bad_selector(selector, "where must be a string such as 'max_z' or 'parallel_z'")
        where = where.lower()
        if where.startswith(("max_", "min_")):
            if extreme is not None or parallel is not None:
                raise _bad_selector(selector, "where cannot be combined with max_min_xyz or parallel_xyz")
            extreme = _parse_extreme(where, selector)
        elif where.startswith("parallel_"):
            if extreme is not None or parallel is not None:
                raise _bad_selector(selector, "where cannot be combined with max_min_xyz or parallel_xyz")
            parallel = _parse_parallel(where.removeprefix("parallel_"), selector)
        else:
            raise _bad_selector(selector, "where must use max_x/min_x/max_y/min_y/max_z/min_z or parallel_x/parallel_y/parallel_z")
    if extreme is not None and parallel is not None:
        raise _bad_selector(selector, "max_min_xyz and parallel_xyz cannot be combined")

    return _Spec(
        function=function,
        topology=topology,
        feature=feature,
        geometry_kind=geometry_kind.lower() if geometry_kind else None,
        extreme=extreme,
        parallel=parallel,
        nth=nth,
        original_fields=tuple((name, fields[name]) for name in _FIELDS if name in fields),
    )


def _parse_extreme(value: Any, selector: str) -> tuple[int, str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        token = value.lower()
        if len(token) == 5 and token[:4] in {"max_", "min_"} and token[-1] in _AXES:
            return _AXES[token[-1]], token[:3]
        raise _bad_selector(selector, "max_min_xyz must be 'max_x', 'min_x', 'max_y', 'min_y', 'max_z', or 'min_z'")
    if isinstance(value, dict):
        if set(value) == {"axis", "extreme"}:
            axis = value["axis"]
            extreme = value["extreme"]
        elif len(value) == 1:
            axis, extreme = next(iter(value.items()))
        else:
            raise _bad_selector(selector, "max_min_xyz mapping must name one axis and its max/min extreme")
        axis = axis.lower() if isinstance(axis, str) else axis
        extreme = extreme.lower() if isinstance(extreme, str) else extreme
        if not isinstance(axis, str) or not isinstance(extreme, str):
            raise _bad_selector(selector, "max_min_xyz mapping needs an x/y/z axis and max/min extreme")
        if axis not in _AXES or extreme not in {"max", "min"}:
            raise _bad_selector(selector, "max_min_xyz mapping needs an x/y/z axis and max/min extreme")
        return _AXES[axis], extreme
    if isinstance(value, (tuple, list)) and len(value) == 2:
        first, second = value
        if isinstance(first, str) and first.lower() in {"max", "min"}:
            return _parse_extreme(f"{first.lower()}_{second}", selector)
        if isinstance(second, str) and second.lower() in {"max", "min"}:
            return _parse_extreme(f"{second.lower()}_{first}", selector)
    raise _bad_selector(selector, "max_min_xyz must be an extreme/axis literal")


def _parse_parallel(value: Any, selector: str) -> tuple[float, float, float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        axis = value.lower()
        if axis not in _AXES:
            raise _bad_selector(selector, "parallel_xyz string must be x, y, or z")
        return tuple(1.0 if i == _AXES[axis] else 0.0 for i in range(3))
    if isinstance(value, (tuple, list)) and len(value) == 3:
        vector = []
        for item in value:
            try:
                valid = not isinstance(item, bool) and isinstance(item, (int, float)) and math.isfinite(float(item))
            except OverflowError:
                valid = False
            if not valid:
                raise _bad_selector(selector, "parallel_xyz vector must contain three finite numbers")
            vector.append(float(item))
        scale = max(abs(component) for component in vector)
        if scale == 0:
            raise _bad_selector(selector, "parallel_xyz vector cannot be zero")
        scaled = [component / scale for component in vector]
        magnitude = math.hypot(*scaled)
        return tuple(component / magnitude for component in scaled)
    raise _bad_selector(selector, "parallel_xyz must be x/y/z or a three-number vector")


def _collect(ctx: Any, spec: _Spec) -> list[_Match]:
    shape = getattr(ctx, "shape", None)
    stored: list[Entity] = []
    for feature in getattr(ctx, "features", []) or []:
        topology_entities = getattr(feature, _ACCESSORS[spec.topology], [])
        stored.extend(entity for entity in topology_entities if getattr(entity, "kind", None) == spec.topology)

    if shape is None:
        return [
            _Match(Entity(kind=entity.kind, index=entity.index, feature=entity.feature, geometry=deepcopy(entity.geometry)), None)
            for entity in stored
        ]

    topologies = _topologies(shape, spec.topology)
    by_index: dict[int, list[Entity]] = {}
    for entity in stored:
        if isinstance(entity.index, int) and not isinstance(entity.index, bool):
            by_index.setdefault(entity.index, []).append(entity)

    matches: list[_Match] = []
    for index, topology in enumerate(topologies):
        # Live feature records carry identity evidence. Do not reuse their
        # ownership if a caller swaps or changes the context's shape.
        records = by_index.get(index, [])
        owners = {
            entity.feature for entity in records
            if getattr(entity, "_aiad_topology", None) is None
            or entity._aiad_topology.IsSame(topology.wrapped)
        }
        owner = next(iter(owners)) if len(owners) == 1 else None
        matches.append(
            _Match(
                Entity(kind=spec.topology, index=index, feature=owner, geometry=_geometry(topology, spec.topology)),
                topology,
            )
        )
    return matches


def _topologies(shape: Any, kind: str) -> list[Any]:
    accessor = _ACCESSORS[kind]
    values = getattr(shape, accessor)()
    return list(values)


def _filter(matches: list[_Match], spec: _Spec) -> list[_Match]:
    filtered = []
    for match in matches:
        entity = match.entity
        if spec.feature is not None:
            if spec.feature == "unknown":
                if entity.feature is not None:
                    continue
            elif entity.feature != spec.feature:
                continue
        geometry = entity.geometry or {}
        if spec.geometry_kind is not None:
            kind = str(geometry.get("type", "")).lower()
            aliases = {"planar": "plane", "linear": "line"}
            if aliases.get(kind, kind) != aliases.get(spec.geometry_kind, spec.geometry_kind):
                continue
        if spec.extreme is not None:
            center = geometry.get("center")
            axis, _extreme = spec.extreme
            if not _rounded_vector(center):
                continue
        if spec.parallel is not None:
            direction = geometry.get("normal") if entity.kind == "face" else geometry.get("direction")
            if entity.kind == "face" and str(geometry.get("type", "")).lower() not in {"plane", "planar"}:
                continue
            if entity.kind == "edge" and str(geometry.get("type", "")).lower() not in {"line", "linear"}:
                continue
            if entity.kind not in {"face", "edge"} or not _parallel(direction, spec.parallel):
                continue
        filtered.append(match)

    if spec.extreme is not None and filtered:
        axis, extreme = spec.extreme
        coordinate_values = [float(item.entity.geometry["center"][axis]) for item in filtered]
        best = max(coordinate_values) if extreme == "max" else min(coordinate_values)
        filtered = [
            match
            for match in filtered
            if math.isclose(
                float(match.entity.geometry["center"][axis]), best, rel_tol=0, abs_tol=1e-9
            )
        ]
    return filtered


def _parallel(direction: Any, target: tuple[float, float, float]) -> bool:
    if not isinstance(direction, (tuple, list)) or len(direction) != 3:
        return False
    values = []
    for item in direction:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            return False
        values.append(float(item))
    magnitude = math.hypot(*values)
    if magnitude == 0:
        return False
    dot = sum((value / magnitude) * component for value, component in zip(values, target, strict=True))
    return abs(abs(dot) - 1.0) <= _VECTOR_TOLERANCE


def _geometry(topology: Any, kind: str) -> dict[str, Any]:
    from build123d import CenterOf

    result: dict[str, Any] = {"type": _type_name(topology)}
    try:
        center = topology.center() if kind == "vertex" else topology.center(CenterOf.MASS)
        result["center"] = _vector3(center)
    except Exception:
        pass
    try:
        bounds = topology.bounding_box()
        result["bbox"] = {
            "min": _vector3(bounds.min),
            "max": _vector3(bounds.max),
        }
    except Exception:
        pass
    if kind == "face":
        try:
            result["area"] = _finite_float(topology.area)
        except Exception:
            pass
        if result["type"].lower() in {"plane", "planar"}:
            try:
                result["normal"] = _vector3(topology.normal_at())
            except Exception:
                pass
        try:
            radius = _numeric_property(topology, "radius")
            if radius is not None:
                result["radius"] = radius
        except Exception:
            pass
    elif kind == "edge":
        try:
            result["length"] = _finite_float(topology.length)
        except Exception:
            pass
        if result["type"].lower() in {"line", "linear"}:
            try:
                direction = _vector3(topology.tangent_at(0.5))
                if next((v for v in _rounded_vector(direction) if v), 0) < 0:
                    direction = [-v for v in direction]
                result["direction"] = direction
            except Exception:
                pass
        try:
            radius = _numeric_property(topology, "radius")
            if radius is not None:
                result["radius"] = radius
        except Exception:
            pass
    return result


def _numeric_property(value: Any, name: str) -> float | None:
    item = getattr(value, name)
    if callable(item):
        item = item()
    return _finite_float(item)


def _type_name(topology: Any) -> str:
    value = getattr(topology, "geom_type", None)
    if value is None:
        return type(topology).__name__
    return str(getattr(value, "name", value)).split(".")[-1]


def _vector3(value: Any) -> list[float]:
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return [_finite_float(component) for component in value]
    return [_finite_float(getattr(value, axis)) for axis in ("X", "Y", "Z")]


def _finite_float(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite geometry value")
    return result


def _semantic_key(entity: Entity) -> tuple[Any, ...]:
    geometry = entity.geometry or {}
    center = geometry.get("center")
    normal = geometry.get("normal", geometry.get("direction"))
    bbox = geometry.get("bbox", {})
    minimum = bbox.get("min") if isinstance(bbox, dict) else None
    maximum = bbox.get("max") if isinstance(bbox, dict) else None
    return (
        entity.feature or "",
        str(geometry.get("type", "")).lower(),
        _rounded_vector(center),
        _rounded_vector(normal),
        _rounded_vector(minimum),
        _rounded_vector(maximum),
        _rounded_number(geometry.get("radius")),
        _rounded_number(geometry.get("area")),
        _rounded_number(geometry.get("length")),
    )


def _rounded_vector(value: Any) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return ()
    try:
        return tuple(round(_finite_float(component), 9) for component in value)
    except (TypeError, ValueError, OverflowError):
        return ()


def _rounded_number(value: Any) -> tuple[float, ...]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ()
    try:
        return (round(_finite_float(value), 9),)
    except (ValueError, OverflowError):
        return ()


def _all_semantically_distinct(matches: list[_Match]) -> bool:
    keys = [_semantic_key(match.entity) for match in matches]
    return len(keys) == len(set(keys))


def _candidate_selector(spec: _Spec, rank: int | None) -> str:
    fields = [(name, value) for name, value in spec.original_fields if name != "nth"]
    if rank is not None:
        fields.append(("nth", rank))
    ordered = [name for name in ("feature", "kind", "where", "max_min_xyz", "parallel_xyz", "nth")]
    fields.sort(key=lambda pair: ordered.index(pair[0]))
    arguments = ", ".join(f"{name}={value!r}" for name, value in fields)
    return f"{spec.function}({arguments})"


def _selector_failure(
    code: str, selector: str, message: str, matches: list[_Match], *, candidate_spec: _Spec | None = None,
) -> SelectorError:
    unique = _all_semantically_distinct(matches)
    candidates = []
    spec = candidate_spec or _parse_selector(selector)
    for rank, match in enumerate(matches):
        candidates.append(
            Candidate(
                selector=_candidate_selector(spec, rank if unique else None),
                feature=match.entity.feature,
                kind=match.entity.kind,
                index=match.entity.index,
                geometry=dict(match.entity.geometry or {}),
            )
        )
    return SelectorError(
        Failure(
            code=code,
            phase="selector",
            message=message,
            selector=selector,
            candidates=candidates,
        )
    )


def _bad_selector(selector: str, message: str) -> SelectorError:
    return SelectorError(
        Failure(
            code="BAD_INPUT",
            phase="input",
            message=message,
            selector=selector,
        )
    )
