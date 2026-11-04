"""Editable, exact 2D sheet geometry backed by build123d and OCCT.

The data model and serialization helpers have no CAD imports. Kernel bindings
are imported only when native geometry is requested or an operation runs.
Text remains editable annotation; it is never converted to cutting geometry.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import copy
from functools import wraps
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypeAlias


_ACTIVE_SHEET: ContextVar[Any] = ContextVar("aiad_active_sheet2d", default=None)
_LATEST_SHEET: ContextVar[Any] = ContextVar("aiad_latest_sheet2d", default=None)
_DEFAULT_LAYERS: dict[str, dict[str, str]] = {
    "cut": {"role": "cut"},
    "score": {"role": "score"},
    "engrave": {"role": "engrave"},
}
_POINT_TOLERANCE = 1e-10
_GEOMETRY_TOLERANCE = 1e-7


def _checked_name(value: Any, label: str = "name") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number, not a boolean.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite.")
    return number


def _positive(value: Any, label: str) -> float:
    number = _finite(value, label)
    if number <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return number


def _coordinate_pair(value: Any, label: str = "coordinate") -> tuple[float, float]:
    if isinstance(value, (str, bytes, Point2D)):
        raise ValueError(f"{label} must be a point name, Point2D, or two-number sequence.")
    if not isinstance(value, Sequence) or len(value) != 2:
        raise ValueError(f"{label} must contain exactly two coordinates.")
    return (_finite(value[0], f"{label}.x"), _finite(value[1], f"{label}.y"))


def _json_clone(value: Any, label: str) -> Any:
    """Copy a value through JSON to enforce the public JSON-native contract."""
    try:
        return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain only finite JSON-native data.") from exc


@dataclass(frozen=True)
class Point2D:
    """A stable named point in the sheet's user units."""

    name: str
    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _checked_name(self.name, "point name"))
        object.__setattr__(self, "x", _finite(self.x, f"point {self.name}.x"))
        object.__setattr__(self, "y", _finite(self.y, f"point {self.name}.y"))

    def as_tuple(self) -> tuple[float, float]:
        """Return ``(x, y)`` without changing the point's identity."""
        return (self.x, self.y)


@dataclass
class Entity2D:
    """One editable sketch primitive.

    ``points`` stores point names rather than coordinates, so entities that use
    the same named point continue to share it after serialization and edits.
    Arc direction is counter-clockwise by default; set
    ``metadata["clockwise"]`` to true for a clockwise arc.
    """

    name: str
    kind: str
    layer: str
    points: tuple[str, ...]
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    closed: bool = False
    text: str | None = None
    height: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = _checked_name(self.name, "entity name")
        self.kind = _checked_name(self.kind, "entity kind").lower()
        self.layer = _checked_name(self.layer, "layer name")
        if self.kind not in {"line", "circle", "arc", "polyline", "rectangle", "text", "bezier", "ellipse"}:
            raise ValueError(f"Unsupported 2D entity kind {self.kind!r}.")
        if isinstance(self.points, (str, bytes)):
            raise ValueError("Entity points must be a sequence of point names.")
        self.points = tuple(_checked_name(name, "point reference") for name in self.points)
        if not isinstance(self.closed, bool):
            raise ValueError("Entity closed must be a boolean.")
        if not isinstance(self.metadata, dict):
            raise ValueError("Entity metadata must be a dictionary.")
        self.metadata = copy.deepcopy(self.metadata)

        expected_points = {"line": 2, "circle": 1, "arc": 1, "text": 1, "ellipse": 1}
        if self.kind in expected_points and len(self.points) != expected_points[self.kind]:
            raise ValueError(
                f"A {self.kind} entity must reference {expected_points[self.kind]} point(s)."
            )
        if self.kind == "rectangle" and (len(self.points) != 4 or not self.closed):
            raise ValueError("A rectangle entity must have four points and be closed.")
        if self.kind == "bezier" and len(self.points) not in {3, 4}:
            raise ValueError("A Bezier entity requires three or four control points.")
        if self.kind == "ellipse":
            self.metadata["rx"] = _positive(self.metadata.get("rx"), "ellipse rx")
            self.metadata["ry"] = _positive(self.metadata.get("ry"), "ellipse ry")
            self.metadata["rotation_deg"] = _finite(self.metadata.get("rotation_deg", 0), "ellipse rotation")
            self.start_angle = _finite(self.start_angle, "ellipse start_angle")
            self.end_angle = _finite(self.end_angle, "ellipse end_angle")
            if not self.closed and math.isclose((self.end_angle - self.start_angle) % 360, 0, abs_tol=1e-10):
                raise ValueError("An open elliptical arc requires a nonzero sweep below 360 degrees.")
        if self.kind == "polyline" and len(self.points) < (3 if self.closed else 2):
            raise ValueError("A polyline needs at least two points, or three when closed.")
        if self.kind in {"circle", "arc"}:
            if self.radius is None:
                raise ValueError(f"A {self.kind} entity needs a radius.")
            self.radius = _positive(self.radius, f"entity {self.name} radius")
        if self.kind == "arc":
            if self.start_angle is None or self.end_angle is None:
                raise ValueError("An arc needs start_angle and end_angle in degrees.")
            self.start_angle = _finite(self.start_angle, f"entity {self.name} start_angle")
            self.end_angle = _finite(self.end_angle, f"entity {self.name} end_angle")
            if math.isclose((self.end_angle - self.start_angle) % 360.0, 0.0, abs_tol=1e-10):
                raise ValueError("An arc must have a non-zero sweep smaller than 360 degrees.")
        if self.kind == "circle":
            self.closed = True
        if self.kind == "text":
            if not isinstance(self.text, str):
                raise ValueError("A text entity needs editable text content.")
            if self.height is None:
                raise ValueError("A text entity needs a positive height.")
            self.height = _positive(self.height, f"entity {self.name} text height")
        elif self.height is not None:
            self.height = _positive(self.height, f"entity {self.name} height")


CoordinateInput: TypeAlias = str | Point2D | Sequence[float]


def _default_layers() -> dict[str, dict[str, str]]:
    return copy.deepcopy(_DEFAULT_LAYERS)


def _atomic_primitive(method):
    """Restore live registries when validation fails after resolving points."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        points, entities = dict(self.points), dict(self.entities)
        layers, history_size = copy.deepcopy(self.layers), len(self.history)
        token = _LATEST_SHEET.set(_LATEST_SHEET.get())
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self.points.clear()
            self.points.update(points)
            self.entities.clear()
            self.entities.update(entities)
            self.layers.clear()
            self.layers.update(layers)
            del self.history[history_size:]
            _LATEST_SHEET.reset(token)
            raise
    return wrapped


@dataclass
class Sheet2D:
    """A named 2D model with exact native geometry and editable source data."""

    name: str = "sheet"
    units: str = "mm"
    points: dict[str, Point2D] = field(default_factory=dict)
    entities: dict[str, Entity2D] = field(default_factory=dict)
    layers: dict[str, dict[str, Any]] = field(default_factory=_default_layers)
    history: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    _context_tokens: list[Token[Any]] = field(default_factory=list, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.name = _checked_name(self.name, "sheet name")
        if not isinstance(self.units, str) or self.units.lower() not in {"mm", "in"}:
            raise ValueError("Sheet units must be 'mm' or 'in'.")
        self.units = self.units.lower()
        if not isinstance(self.points, dict) or not isinstance(self.entities, dict):
            raise ValueError("Sheet points and entities must be dictionaries.")
        if not isinstance(self.layers, dict):
            raise ValueError("Sheet layers must be a dictionary.")
        layers = _default_layers()
        for name, value in self.layers.items():
            layer_name = _checked_name(name, "layer name")
            if value is None:
                layers[layer_name] = {}
            elif isinstance(value, dict):
                layers[layer_name] = copy.deepcopy(value)
            else:
                raise ValueError(f"Layer {layer_name!r} metadata must be a dictionary.")
        self.layers = layers

        for name, point in self.points.items():
            if not isinstance(point, Point2D) or point.name != name:
                raise ValueError(f"Sheet point key {name!r} must match a Point2D name.")
        for name, entity in self.entities.items():
            if not isinstance(entity, Entity2D) or entity.name != name:
                raise ValueError(f"Sheet entity key {name!r} must match an Entity2D name.")
            missing = set(entity.points).difference(self.points)
            if missing:
                raise ValueError(
                    f"Entity {name!r} refers to missing point(s): {', '.join(sorted(missing))}."
                )
            self.layers.setdefault(entity.layer, {"role": entity.layer})
        if not isinstance(self.history, list) or not all(isinstance(item, dict) for item in self.history):
            raise ValueError("Sheet history must be a list of dictionaries.")
        if not isinstance(self.constraints, list) or not all(
            isinstance(item, dict) for item in self.constraints
        ):
            raise ValueError("Sheet constraints must be a list of dictionaries.")
        self.history = copy.deepcopy(self.history)
        self.constraints = copy.deepcopy(self.constraints)
        _LATEST_SHEET.set(self)

    def __enter__(self) -> Sheet2D:
        self._context_tokens.append(_ACTIVE_SHEET.set(self))
        _LATEST_SHEET.set(self)
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        if self._context_tokens:
            _ACTIVE_SHEET.reset(self._context_tokens.pop())
        return False

    def _store_point(self, name: str, x: float, y: float) -> Point2D:
        point_name = _checked_name(name, "point name")
        point_x = _finite(x, f"point {point_name}.x")
        point_y = _finite(y, f"point {point_name}.y")
        previous = self.points.get(point_name)
        if previous is not None:
            if math.dist(previous.as_tuple(), (point_x, point_y)) <= _POINT_TOLERANCE:
                return previous
            raise ValueError(f"Point name {point_name!r} is already used at another location.")
        point = Point2D(point_name, point_x, point_y)
        self.points[point_name] = point
        return point

    def _resolve_point(self, value: CoordinateInput, auto_name: str) -> Point2D:
        if isinstance(value, str):
            try:
                return self.points[value]
            except KeyError as exc:
                raise ValueError(f"Unknown point name {value!r}.") from exc
        if isinstance(value, Point2D):
            previous = self.points.get(value.name)
            if previous is not None:
                if math.dist(previous.as_tuple(), value.as_tuple()) <= _POINT_TOLERANCE:
                    return previous
                raise ValueError(f"Point name {value.name!r} is already used at another location.")
            self.points[value.name] = value
            return value
        x, y = _coordinate_pair(value)
        return self._store_point(auto_name, x, y)

    def _ensure_layer(self, name: str) -> str:
        layer = _checked_name(name, "layer name")
        self.layers.setdefault(layer, {"role": layer})
        return layer

    def _add_entity(self, entity: Entity2D, *, history: bool = True) -> Entity2D:
        if entity.name in self.entities:
            raise ValueError(f"Entity name {entity.name!r} is already used on this sheet.")
        missing = set(entity.points).difference(self.points)
        if missing:
            raise ValueError(
                f"Entity {entity.name!r} refers to missing point(s): {', '.join(sorted(missing))}."
            )
        self._ensure_layer(entity.layer)
        self.entities[entity.name] = entity
        if history:
            self.history.append(
                {
                    "operation": "primitive",
                    "kind": entity.kind,
                    "name": entity.name,
                    "layer": entity.layer,
                    "entity": _entity_data(entity),
                    "points": {
                        point_name: _point_data(self.points[point_name])
                        for point_name in entity.points
                    },
                }
            )
        _LATEST_SHEET.set(self)
        return entity

    @_atomic_primitive
    def point(self, name: str, x: float, y: float) -> Point2D:
        """Create or retrieve a stable named point."""
        point_name = _checked_name(name, "point name")
        existed = point_name in self.points
        point = self._store_point(point_name, x, y)
        if not existed:
            self.history.append(
                {"operation": "point", "name": point.name, "coordinates": [point.x, point.y]}
            )
        _LATEST_SHEET.set(self)
        return point

    @_atomic_primitive
    def line(
        self,
        name: str,
        start: CoordinateInput,
        end: CoordinateInput,
        layer: str = "cut",
    ) -> Entity2D:
        """Add an exact segment. Tuple inputs become ``name.start``/``name.end``."""
        entity_name = _checked_name(name, "entity name")
        first = self._resolve_point(start, f"{entity_name}.start")
        second = self._resolve_point(end, f"{entity_name}.end")
        if math.dist(first.as_tuple(), second.as_tuple()) <= _POINT_TOLERANCE:
            raise ValueError("A line must have two distinct points.")
        return self._add_entity(
            Entity2D(entity_name, "line", self._ensure_layer(layer), (first.name, second.name))
        )

    @_atomic_primitive
    def circle(
        self,
        name: str,
        center: CoordinateInput,
        radius: float,
        layer: str = "cut",
    ) -> Entity2D:
        """Add a true circular edge. Tuple centers use ``name.center``."""
        entity_name = _checked_name(name, "entity name")
        center_point = self._resolve_point(center, f"{entity_name}.center")
        return self._add_entity(
            Entity2D(
                entity_name,
                "circle",
                self._ensure_layer(layer),
                (center_point.name,),
                radius=_positive(radius, "circle radius"),
                closed=True,
            )
        )

    @_atomic_primitive
    def arc(
        self,
        name: str,
        center: CoordinateInput,
        radius: float,
        start_angle: float,
        end_angle: float,
        layer: str = "cut",
        *,
        clockwise: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> Entity2D:
        """Add an exact circular arc, with angles in degrees.

        The default sweep is counter-clockwise and wraps through 360 degrees
        when ``end_angle`` is numerically below ``start_angle``. Use
        ``clockwise=True`` to select the other direction.
        """
        entity_name = _checked_name(name, "entity name")
        if not isinstance(clockwise, bool):
            raise ValueError("clockwise must be a boolean.")
        center_point = self._resolve_point(center, f"{entity_name}.center")
        start = _finite(start_angle, "arc start_angle")
        end = _finite(end_angle, "arc end_angle")
        sweep = (start - end) % 360.0 if clockwise else (end - start) % 360.0
        if sweep <= 1e-10 or sweep >= 360.0 - 1e-10:
            raise ValueError("An arc sweep must be greater than zero and less than 360 degrees.")
        entity_metadata = dict(metadata or {})
        entity_metadata["clockwise"] = clockwise
        return self._add_entity(
            Entity2D(
                entity_name,
                "arc",
                self._ensure_layer(layer),
                (center_point.name,),
                radius=_positive(radius, "arc radius"),
                start_angle=start,
                end_angle=end,
                metadata=entity_metadata,
            )
        )

    @_atomic_primitive
    def polyline(
        self,
        name: str,
        points: Sequence[CoordinateInput],
        closed: bool = False,
        layer: str = "cut",
    ) -> Entity2D:
        """Add an exact chain of line segments with stable ``name.pN`` points."""
        entity_name = _checked_name(name, "entity name")
        if not isinstance(closed, bool):
            raise ValueError("Polyline closed must be a boolean.")
        if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
            raise ValueError("Polyline points must be a sequence of point inputs.")
        if len(points) < (3 if closed else 2):
            raise ValueError("A polyline needs at least two points, or three when closed.")
        # An explicitly repeated first vertex is a common closed-polyline input;
        # remove it so that no zero-length closing edge is produced.
        inputs = list(points)
        if closed and len(inputs) > 3:
            first_xy = self._point_value(inputs[0])
            last_xy = self._point_value(inputs[-1])
            if math.dist(first_xy, last_xy) <= _POINT_TOLERANCE:
                inputs.pop()
        if len(inputs) < (3 if closed else 2):
            raise ValueError("A polyline needs at least two distinct points.")
        resolved = [
            self._resolve_point(value, f"{entity_name}.p{index}")
            for index, value in enumerate(inputs)
        ]
        coords = [point.as_tuple() for point in resolved]
        if any(
            math.dist(coords[index], coords[index + 1]) <= _POINT_TOLERANCE
            for index in range(len(coords) - 1)
        ):
            raise ValueError("A polyline cannot contain consecutive duplicate points.")
        if closed and math.dist(coords[-1], coords[0]) <= _POINT_TOLERANCE:
            raise ValueError("A closed polyline cannot repeat its first point at the end.")
        if closed and len({(round(x, 10), round(y, 10)) for x, y in coords}) < 3:
            raise ValueError("A closed polyline needs at least three distinct points.")
        if closed:
            twice_area = sum(
                coords[index][0] * coords[(index + 1) % len(coords)][1]
                - coords[(index + 1) % len(coords)][0] * coords[index][1]
                for index in range(len(coords))
            )
            scale = max(
                max(point[0] for point in coords) - min(point[0] for point in coords),
                max(point[1] for point in coords) - min(point[1] for point in coords),
                1.0,
            )
            if abs(twice_area) <= _POINT_TOLERANCE * scale * scale:
                raise ValueError("A closed polyline must enclose non-zero area.")
        return self._add_entity(
            Entity2D(
                entity_name,
                "polyline",
                self._ensure_layer(layer),
                tuple(point.name for point in resolved),
                closed=closed,
            )
        )

    def _point_value(self, value: CoordinateInput) -> tuple[float, float]:
        if isinstance(value, str):
            try:
                return self.points[value].as_tuple()
            except KeyError as exc:
                raise ValueError(f"Unknown point name {value!r}.") from exc
        if isinstance(value, Point2D):
            return value.as_tuple()
        return _coordinate_pair(value)

    @_atomic_primitive
    def rectangle(
        self,
        name: str,
        width: float,
        height: float,
        origin: CoordinateInput = (0.0, 0.0),
        layer: str = "cut",
    ) -> Entity2D:
        """Add an axis-aligned closed rectangle with exact line edges."""
        entity_name = _checked_name(name, "entity name")
        rect_width = _positive(width, "rectangle width")
        rect_height = _positive(height, "rectangle height")
        lower_left = self._resolve_point(origin, f"{entity_name}.p0")
        x0, y0 = lower_left.as_tuple()
        point_specs = (
            lower_left,
            (x0 + rect_width, y0),
            (x0 + rect_width, y0 + rect_height),
            (x0, y0 + rect_height),
        )
        resolved = [lower_left]
        for index, value in enumerate(point_specs[1:], start=1):
            resolved.append(self._resolve_point(value, f"{entity_name}.p{index}"))
        return self._add_entity(
            Entity2D(
                entity_name,
                "rectangle",
                self._ensure_layer(layer),
                tuple(point.name for point in resolved),
                closed=True,
                metadata={"primitive": "rectangle", "width": rect_width, "height": rect_height},
            )
        )

    @_atomic_primitive
    def text(
        self,
        name: str,
        value: str,
        position: CoordinateInput,
        height: float,
        layer: str = "engrave",
    ) -> Entity2D:
        """Add editable text annotation; it is not converted to a cutting path."""
        entity_name = _checked_name(name, "entity name")
        if not isinstance(value, str):
            raise ValueError("Text content must be a string.")
        point = self._resolve_point(position, f"{entity_name}.position")
        return self._add_entity(
            Entity2D(
                entity_name,
                "text",
                self._ensure_layer(layer),
                (point.name,),
                text=value,
                height=_positive(height, "text height"),
            )
        )

    def add_constraint(self, name: str, kind: str, entities: Sequence[str],
                       value: float | None = None, **options: Any) -> dict[str, Any]:
        """Add a named constraint; polygon edges use ``<name>.edge0`` aliases."""
        from .sheet_constraints import add_constraint
        return add_constraint(self, name, kind, entities, value, **options)

    def solve(self, tolerance: float = 1e-6, inplace: bool = True) -> Any:
        """Solve named sheet geometry, applying only a consistent solution."""
        from .sheet_constraints import solve
        return solve(self, tolerance=tolerance, inplace=inplace)

    def bezier(self, name: str, control_points: Sequence[CoordinateInput], layer: str = "cut") -> Entity2D:
        """Add an exact quadratic or cubic Bezier, retaining named controls."""
        entity_name = _checked_name(name, "entity name")
        if len(control_points) not in {3, 4}:
            raise ValueError("A Bezier requires three or four control points.")
        points = [self._resolve_point(value, f"{entity_name}.p{index}")
                  for index, value in enumerate(control_points)]
        if all(math.dist(points[0].as_tuple(), point.as_tuple()) <= _POINT_TOLERANCE for point in points[1:]):
            raise ValueError("Bezier controls cannot all coincide.")
        return self._add_entity(Entity2D(entity_name, "bezier", self._ensure_layer(layer),
                                        tuple(point.name for point in points)))

    def ellipse(self, name: str, center: CoordinateInput, rx: float, ry: float,
                rotation: float = 0, start_angle: float = 0, end_angle: float = 360,
                layer: str = "cut", *, clockwise: bool = False) -> Entity2D:
        """Add an exact ellipse or elliptical arc; angles are conic parameters."""
        entity_name = _checked_name(name, "entity name")
        start, end = _finite(start_angle, "ellipse start_angle"), _finite(end_angle, "ellipse end_angle")
        span = abs(end - start)
        if not isinstance(clockwise, bool):
            raise ValueError("clockwise must be a boolean")
        if span <= 1e-10 or span > 360 + 1e-10:
            raise ValueError("Ellipse span must be nonzero and at most 360 degrees")
        point = self._resolve_point(center, f"{entity_name}.center")
        return self._add_entity(Entity2D(entity_name, "ellipse", self._ensure_layer(layer), (point.name,),
            start_angle=start, end_angle=end, closed=math.isclose(span, 360, abs_tol=1e-10),
            metadata={"rx": rx, "ry": ry, "rotation_deg": rotation, "clockwise": clockwise}))

    def to_shape(self, layers: Iterable[str] = ("cut",)) -> Any:
        """Return exact build123d edge/wire geometry from selected layers.

        The default includes only ``cut``. If a selected layer contains text,
        conversion raises instead of silently outlining or cutting the text.
        """
        from build123d import Compound

        selected = self._selected_entities(layers)
        native_shapes = [_entity_to_native(self, entity) for entity in selected]
        if not native_shapes:
            raise ValueError("No geometric entities were selected for native shape conversion.")
        return Compound(children=native_shapes)

    def to_face(self, layers: Iterable[str] = ("cut",)) -> Any:
        """Return exact filled planar faces, with nested loops represented as holes."""
        selected = self._selected_entities(layers)
        wires = _closed_wires_from_entities(self, selected)
        faces = _faces_from_nested_wires(wires)
        if len(faces) == 1:
            return faces[0]
        from build123d import Compound

        return Compound(children=faces)

    def bounding_box(self, layers: Iterable[str] = ("cut",)) -> Any:
        """Return build123d's exact native bounding box for selected geometry."""
        return self.to_shape(layers=layers).bounding_box()

    def offset(
        self,
        distance: float,
        layers: Iterable[str] = ("cut",),
        *,
        layer: str | None = None,
        join: str = "arc",
        name: str | None = None,
    ) -> Sheet2D:
        """Offset selected native paths and return a derived sheet.

        Positive distance expands each selected closed contour; negative
        distance contracts it. OCCT creates exact line and circular-arc joins.
        """
        offset_distance = _finite(distance, "offset distance")
        if abs(offset_distance) <= _POINT_TOLERANCE:
            raise ValueError("Offset distance must be non-zero.")
        from build123d import Compound, Edge, Kind, Side, Wire

        kind_by_name = {"arc": Kind.ARC, "intersection": Kind.INTERSECTION}
        try:
            offset_kind = kind_by_name[str(join).lower()]
        except KeyError as exc:
            raise ValueError("Offset join must be 'arc' or 'intersection'.") from exc
        selected_layers = _normalize_layers(layers, self.layers)
        selected = self._selected_entities(selected_layers)
        wires = _wires_from_entities(self, selected)
        offset_shapes = []
        for index, wire in enumerate(wires):
            try:
                result = wire.offset_2d(offset_distance, kind=offset_kind, side=Side.BOTH, closed=True)
            except Exception as exc:
                raise ValueError(f"OCCT could not offset path {index} by {offset_distance:g}.") from exc
            offset_shapes.append(Wire(result) if isinstance(result, Edge) else result)
        native = Compound(children=offset_shapes)
        output_layer = layer or _single_layer(selected, fallback="cut")
        return _derived_sheet(
            self,
            native,
            operation="offset",
            result_name=name or f"{self.name}_offset",
            output_layer=output_layer,
            sources=[_geometry_snapshot(self, selected_layers)],
            parameters={"distance": offset_distance, "join": str(join).lower(), "layers": list(selected_layers)},
            source_entities=selected,
        )

    def union(
        self,
        other: Sheet2D,
        layers: Iterable[str] = ("cut",),
        *,
        other_layers: Iterable[str] | None = None,
        layer: str = "cut",
        name: str | None = None,
    ) -> Sheet2D:
        """Exact planar union with another sheet, preserving native curves."""
        return self._boolean(other, "union", layers, other_layers, layer, name)

    def difference(
        self,
        other: Sheet2D,
        layers: Iterable[str] = ("cut",),
        *,
        other_layers: Iterable[str] | None = None,
        layer: str = "cut",
        name: str | None = None,
    ) -> Sheet2D:
        """Subtract another sheet's exact planar geometry from this sheet."""
        return self._boolean(other, "difference", layers, other_layers, layer, name)

    def intersection(
        self,
        other: Sheet2D,
        layers: Iterable[str] = ("cut",),
        *,
        other_layers: Iterable[str] | None = None,
        layer: str = "cut",
        name: str | None = None,
    ) -> Sheet2D:
        """Exact planar intersection with another sheet."""
        return self._boolean(other, "intersection", layers, other_layers, layer, name)

    def _boolean(
        self,
        other: Sheet2D,
        operation: str,
        layers: Iterable[str],
        other_layers: Iterable[str] | None,
        output_layer: str,
        result_name: str | None,
    ) -> Sheet2D:
        if not isinstance(other, Sheet2D):
            raise TypeError(f"{operation} expects another Sheet2D.")
        if self.units != other.units:
            raise ValueError("Boolean sheets must use the same units.")
        from build123d import Compound, Face, Shape, Solid, Vector
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

        self_layers = _normalize_layers(layers, self.layers)
        rhs_layers = _normalize_layers(
            other_layers if other_layers is not None else self_layers,
            other.layers,
        )
        left_entities = self._selected_entities(self_layers)
        right_entities = other._selected_entities(rhs_layers)
        left_face = self.to_face(layers=self_layers)
        right_face = other.to_face(layers=rhs_layers)

        def temporary_prism(face_shape: Any) -> Any:
            faces = [face_shape] if isinstance(face_shape, Face) else list(face_shape.faces())
            solids = [Solid.extrude(face, Vector(0, 0, 1.0)) for face in faces]
            if not solids:
                raise ValueError("A boolean input has no filled planar faces.")
            return solids[0] if len(solids) == 1 else Compound(children=solids)

        left_shape = temporary_prism(left_face)
        right_shape = temporary_prism(right_face)
        builder_by_operation = {
            "union": BRepAlgoAPI_Fuse,
            "difference": BRepAlgoAPI_Cut,
            "intersection": BRepAlgoAPI_Common,
        }
        builder = builder_by_operation[operation](left_shape.wrapped, right_shape.wrapped)
        builder.Build()
        native = builder.Shape()
        if not builder.IsDone() or native.IsNull():
            raise ValueError(f"OCCT could not complete the planar {operation}.")
        # Coplanar face booleans can leave internal face partitions. Unify
        # adjacent same-domain faces before extracting the exact top profile.
        unified_builder = ShapeUpgrade_UnifySameDomain(native, True, True, True)
        unified_builder.Build()
        unified = Shape.cast(unified_builder.Shape())
        top_faces = []
        for face in unified.faces():
            if face.geom_type.name.lower() != "plane":
                continue
            bounds = face.bounding_box()
            if (
                abs(float(bounds.min.Z) - 1.0) <= _GEOMETRY_TOLERANCE * 10
                and abs(float(bounds.max.Z) - 1.0) <= _GEOMETRY_TOLERANCE * 10
            ):
                top_faces.append(face)
        if not top_faces:
            raise ValueError(f"The planar {operation} produced no filled result geometry.")
        native = Compound(children=top_faces)
        output_layer = _checked_name(output_layer, "output layer")
        return _derived_sheet(
            self,
            native,
            operation=operation,
            result_name=result_name or f"{self.name}_{operation}_{other.name}",
            output_layer=output_layer,
            sources=[
                _geometry_snapshot(self, self_layers),
                _geometry_snapshot(other, rhs_layers),
            ],
            parameters={
                "layers": list(self_layers),
                "other_layers": list(rhs_layers),
                "output_layer": output_layer,
            },
            other=other,
            source_entities=left_entities + right_entities,
            z_offset=1.0,
        )

    def linear_pattern(
        self,
        entities: str | Iterable[str] | None = None,
        count: int = 2,
        spacing: CoordinateInput | None = None,
        *,
        dx: float | None = None,
        dy: float | None = None,
        name_prefix: str | None = None,
    ) -> Sheet2D:
        """Copy named entities ``count`` times along a fixed 2D vector.

        The returned sheet includes the original geometry as instance zero.
        ``spacing`` is the per-instance ``(dx, dy)`` vector; ``dx``/``dy`` are
        accepted as equivalent keyword coordinates.
        """
        vector = _pattern_vector(spacing, dx, dy)
        if math.hypot(*vector) <= _POINT_TOLERANCE:
            raise ValueError("Linear pattern spacing must be non-zero.")
        selected = self._pattern_entities(entities)
        instance_count = _pattern_count(count)
        prefix = _checked_name(name_prefix or "linear", "pattern name prefix")
        result = self._copy()
        generated_names: list[str] = []
        for instance in range(1, instance_count):
            shift = (vector[0] * instance, vector[1] * instance)
            names = _copy_entities_transformed(
                result,
                selected,
                transform=lambda x, y, shift=shift: (x + shift[0], y + shift[1]),
                angle=0.0,
                name_suffix=f"{prefix}{instance}",
            )
            generated_names.extend(names)
        self._append_pattern_history(
            result,
            operation="linear_pattern",
            selected=selected,
            parameters={"count": instance_count, "spacing": list(vector), "name_prefix": prefix},
            generated_names=generated_names,
        )
        return result

    def radial_pattern(
        self,
        entities: str | Iterable[str] | None = None,
        count: int = 2,
        center: CoordinateInput = (0.0, 0.0),
        angle: float = 360.0,
        *,
        rotate: bool = True,
        name_prefix: str | None = None,
    ) -> Sheet2D:
        """Copy named entities around a center using exact point transforms.

        A full 360-degree pattern omits the duplicate endpoint. A partial
        pattern includes both endpoints. The returned sheet includes instance
        zero, the original geometry.
        """
        if not isinstance(rotate, bool):
            raise ValueError("rotate must be a boolean.")
        angle_degrees = _finite(angle, "radial pattern angle")
        if abs(angle_degrees) <= _POINT_TOLERANCE or abs(angle_degrees) > 360.0 + 1e-10:
            raise ValueError("Radial pattern angle must be non-zero and at most 360 degrees.")
        instance_count = _pattern_count(count)
        if isinstance(center, str):
            try:
                pivot = self.points[center].as_tuple()
            except KeyError as exc:
                raise ValueError(f"Unknown radial pattern center point {center!r}.") from exc
        elif isinstance(center, Point2D):
            pivot = center.as_tuple()
        else:
            pivot = _coordinate_pair(center, "radial pattern center")
        selected = self._pattern_entities(entities)
        prefix = _checked_name(name_prefix or "radial", "pattern name prefix")
        full_turn = math.isclose(abs(angle_degrees), 360.0, abs_tol=1e-10)
        result = self._copy()
        generated_names: list[str] = []
        for instance in range(1, instance_count):
            fraction = instance / instance_count if full_turn else instance / (instance_count - 1)
            rotation = math.radians(angle_degrees * fraction)

            def transform(x: float, y: float, rotation: float = rotation) -> tuple[float, float]:
                cosine, sine = math.cos(rotation), math.sin(rotation)
                dx, dy = x - pivot[0], y - pivot[1]
                return (pivot[0] + cosine * dx - sine * dy, pivot[1] + sine * dx + cosine * dy)

            names = _copy_entities_transformed(
                result,
                selected,
                transform=transform,
                angle=math.degrees(rotation) if rotate else 0.0,
                name_suffix=f"{prefix}{instance}",
                rotate_arcs=rotate,
            )
            generated_names.extend(names)
        self._append_pattern_history(
            result,
            operation="radial_pattern",
            selected=selected,
            parameters={
                "count": instance_count,
                "center": list(pivot),
                "angle": angle_degrees,
                "rotate": rotate,
                "name_prefix": prefix,
            },
            generated_names=generated_names,
        )
        return result

    def _pattern_entities(self, names: str | Iterable[str] | None) -> list[Entity2D]:
        if names is None:
            selected_names = list(self.entities)
        elif isinstance(names, str):
            selected_names = [names]
        else:
            selected_names = list(names)
        if not selected_names:
            raise ValueError("A pattern needs at least one source entity.")
        selected_names = [_checked_name(name, "pattern source entity name") for name in selected_names]
        if len(set(selected_names)) != len(selected_names):
            raise ValueError("Pattern source entity names must be unique.")
        try:
            return [self.entities[name] for name in selected_names]
        except KeyError as exc:
            raise ValueError(f"Unknown pattern source entity {exc.args[0]!r}.") from exc

    def _copy(self) -> Sheet2D:
        return Sheet2D(
            name=self.name,
            units=self.units,
            points=copy.deepcopy(self.points),
            entities=copy.deepcopy(self.entities),
            layers=copy.deepcopy(self.layers),
            history=copy.deepcopy(self.history),
            constraints=copy.deepcopy(self.constraints),
        )

    def _append_pattern_history(
        self,
        result: Sheet2D,
        *,
        operation: str,
        selected: list[Entity2D],
        parameters: dict[str, Any],
        generated_names: list[str],
    ) -> None:
        result.history.append(
            {
                "operation": operation,
                "source_sheet": self.name,
                "source_geometry": _entity_geometry_snapshot(self, selected),
                "source_names": [entity.name for entity in selected],
                "parameters": _json_clone(parameters, "pattern parameters"),
                "generated_names": generated_names,
            }
        )
        _LATEST_SHEET.set(result)

    def extrude(self, height: float, layers: Iterable[str] = ("cut",)) -> Any:
        """Extrude exact closed cut contours and attach editable 2D provenance.

        Returns a real build123d Solid or Compound. The source is also retained
        on ``shape.aiad_source_sheet`` as JSON-native data, and operation
        history is available as ``shape.aiad_history``.
        """
        extrusion_height = _finite(height, "extrusion height")
        if abs(extrusion_height) <= _POINT_TOLERANCE:
            raise ValueError("Extrusion height must be non-zero.")
        from build123d import Compound, Face, Solid, Vector

        selected_layers = _normalize_layers(layers, self.layers)
        face_shape = self.to_face(layers=selected_layers)
        faces = [face_shape] if isinstance(face_shape, Face) else list(face_shape.faces())
        if not faces:
            raise ValueError("The selected sheet has no filled faces to extrude.")
        direction = Vector(0, 0, extrusion_height)
        solids = [Solid.extrude(face, direction) for face in faces]
        shape = solids[0] if len(solids) == 1 else Compound(children=solids)
        volume = float(shape.volume)
        if not shape.is_valid or not math.isfinite(volume) or volume <= 0:
            raise ValueError("Extrusion produced zero-volume or invalid geometry.")
        operation_history = copy.deepcopy(self.history)
        operation_history.append(
            {
                "operation": "extrude",
                "source_sheet": self.name,
                "source_geometry": _geometry_snapshot(self, selected_layers),
                "parameters": {"height": extrusion_height, "layers": list(selected_layers)},
            }
        )
        shape.aiad_history = _json_clone(operation_history, "extrusion history")
        shape.aiad_source_sheet = self.to_dict()
        return shape

    def _selected_entities(self, layers: Iterable[str]) -> list[Entity2D]:
        selected_layers = _normalize_layers(layers, self.layers)
        missing = set(selected_layers).difference(self.layers)
        if missing:
            raise ValueError(f"Unknown layer(s): {', '.join(sorted(missing))}.")
        selected = [entity for entity in self.entities.values() if entity.layer in selected_layers]
        text_entities = [entity.name for entity in selected if entity.kind == "text"]
        if text_entities:
            raise ValueError(
                "Editable text is not cutting geometry. Explicitly outline text before using "
                f"these layer(s): {', '.join(selected_layers)} (text: {', '.join(text_entities)})."
            )
        if not selected:
            raise ValueError(f"No geometric entities exist on layer(s): {', '.join(selected_layers)}.")
        return selected

    def _geometry_snapshot(self, layers: Iterable[str]) -> dict[str, Any]:
        return _geometry_snapshot(self, _normalize_layers(layers, self.layers))

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-native sheet data without writing a file."""
        data = {
            "name": self.name,
            "units": self.units,
            "points": {name: _point_data(point) for name, point in self.points.items()},
            "entities": {name: _entity_data(entity) for name, entity in self.entities.items()},
            "layers": copy.deepcopy(self.layers),
            "history": copy.deepcopy(self.history),
            "constraints": copy.deepcopy(self.constraints),
        }
        return _json_clone(data, "Sheet2D data")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Sheet2D:
        """Restore a sheet from the JSON-native mapping returned by ``to_dict``."""
        if not isinstance(data, Mapping):
            raise ValueError("Sheet data must be a mapping.")
        points_data = data.get("points", {})
        entities_data = data.get("entities", {})
        if not isinstance(points_data, Mapping) or not isinstance(entities_data, Mapping):
            raise ValueError("Serialized points and entities must be mappings.")
        points: dict[str, Point2D] = {}
        for key, value in points_data.items():
            if isinstance(value, Point2D):
                point = value
            elif isinstance(value, Mapping):
                point = Point2D(str(value.get("name", key)), value["x"], value["y"])
            else:
                raise ValueError(f"Serialized point {key!r} must be a mapping.")
            if str(key) != point.name:
                raise ValueError(f"Serialized point key {key!r} does not match its name.")
            points[point.name] = point
        entities: dict[str, Entity2D] = {}
        for key, value in entities_data.items():
            if isinstance(value, Entity2D):
                entity = copy.deepcopy(value)
            elif isinstance(value, Mapping):
                entity = Entity2D(
                    name=str(value.get("name", key)),
                    kind=str(value["kind"]),
                    layer=str(value.get("layer", "cut")),
                    points=tuple(value.get("points", ())),
                    radius=value.get("radius"),
                    start_angle=value.get("start_angle"),
                    end_angle=value.get("end_angle"),
                    closed=value.get("closed", False),
                    text=value.get("text"),
                    height=value.get("height"),
                    metadata=copy.deepcopy(value.get("metadata", {})),
                )
            else:
                raise ValueError(f"Serialized entity {key!r} must be a mapping.")
            if str(key) != entity.name:
                raise ValueError(f"Serialized entity key {key!r} does not match its name.")
            entities[entity.name] = entity
        return cls(
            name=str(data.get("name", "sheet")),
            units=str(data.get("units", "mm")),
            points=points,
            entities=entities,
            layers=copy.deepcopy(data.get("layers", _default_layers())),
            history=copy.deepcopy(data.get("history", [])),
            constraints=copy.deepcopy(data.get("constraints", [])),
        )


def _point_data(point: Point2D) -> dict[str, Any]:
    return {"name": point.name, "x": point.x, "y": point.y}


def _entity_data(entity: Entity2D) -> dict[str, Any]:
    return {
        "name": entity.name,
        "kind": entity.kind,
        "layer": entity.layer,
        "points": list(entity.points),
        "radius": entity.radius,
        "start_angle": entity.start_angle,
        "end_angle": entity.end_angle,
        "closed": entity.closed,
        "text": entity.text,
        "height": entity.height,
        "metadata": copy.deepcopy(entity.metadata),
    }


def _normalize_layers(layers: Iterable[str] | str | None, available: Mapping[str, Any]) -> tuple[str, ...]:
    if layers is None:
        names = tuple(available)
    elif isinstance(layers, str):
        names = (layers,)
    else:
        names = tuple(layers)
    if not names:
        raise ValueError("At least one layer must be selected.")
    normalized = tuple(_checked_name(name, "layer name") for name in names)
    return tuple(dict.fromkeys(normalized))


def _single_layer(entities: Sequence[Entity2D], fallback: str) -> str:
    names = {entity.layer for entity in entities}
    return next(iter(names)) if len(names) == 1 else fallback


def _entity_to_native(sheet: Sheet2D, entity: Entity2D) -> Any:
    from build123d import AngularDirection, Edge, Plane, Wire

    points = [sheet.points[name] for name in entity.points]
    if entity.kind == "line":
        first, second = points
        return Edge.make_line((first.x, first.y, 0), (second.x, second.y, 0))
    if entity.kind == "circle":
        center = points[0]
        return Edge.make_circle(entity.radius, Plane(origin=(center.x, center.y, 0)))
    if entity.kind == "arc":
        center = points[0]
        start = float(entity.start_angle) % 360.0
        clockwise = bool(entity.metadata.get("clockwise", False))
        sweep = (
            (float(entity.start_angle) - float(entity.end_angle)) % 360.0
            if clockwise
            else (float(entity.end_angle) - float(entity.start_angle)) % 360.0
        )
        if sweep <= 1e-10 or sweep >= 360.0 - 1e-10:
            raise ValueError(f"Arc {entity.name!r} has a zero or full-circle sweep.")
        plane = Plane(origin=(center.x, center.y, 0))
        if clockwise:
            ccw_start = (start - sweep) % 360.0
            ccw_edge = Edge.make_circle(
                entity.radius,
                plane,
                start_angle=ccw_start,
                end_angle=ccw_start + sweep,
                angular_direction=AngularDirection.COUNTER_CLOCKWISE,
            )
            return Edge(ccw_edge.wrapped.Reversed())
        return Edge.make_circle(
            entity.radius,
            plane,
            start_angle=start,
            end_angle=start + sweep,
            angular_direction=AngularDirection.COUNTER_CLOCKWISE,
        )
    if entity.kind in {"polyline", "rectangle"}:
        vertices = [(point.x, point.y, 0) for point in points]
        return Wire.make_polygon(vertices, close=entity.closed)
    if entity.kind == "bezier":
        return Edge.make_bezier(*[(point.x, point.y, 0) for point in points])
    if entity.kind == "ellipse":
        center = points[0]
        phi = math.radians(entity.metadata["rotation_deg"])
        plane = Plane(origin=(center.x, center.y, 0), x_dir=(math.cos(phi), math.sin(phi), 0))
        if entity.closed:
            return Edge.make_ellipse(entity.metadata["rx"], entity.metadata["ry"], plane)
        start, end = float(entity.start_angle), float(entity.end_angle)
        if entity.metadata.get("clockwise", False):
            edge = Edge.make_ellipse(entity.metadata["rx"], entity.metadata["ry"], plane,
                                     start_angle=end, end_angle=start)
            return Edge(edge.wrapped.Reversed())
        return Edge.make_ellipse(entity.metadata["rx"], entity.metadata["ry"], plane,
                                  start_angle=start, end_angle=end)
    if entity.kind == "text":
        raise ValueError("Editable text is not native cutting geometry; outline it explicitly first.")
    raise ValueError(f"Unsupported entity kind {entity.kind!r}.")


def _entity_edges(sheet: Sheet2D, entities: Sequence[Entity2D]) -> list[Any]:
    edges: list[Any] = []
    for entity in entities:
        native = _entity_to_native(sheet, entity)
        edges.extend(native.edges() if native.__class__.__name__ == "Wire" else [native])
    return edges


def _wires_from_entities(sheet: Sheet2D, entities: Sequence[Entity2D]) -> list[Any]:
    from build123d import Wire

    edges = _entity_edges(sheet, entities)
    wires = list(Wire.combine(edges, tol=1e-9))
    if not wires:
        raise ValueError("Selected entities did not form any native wires.")
    return wires


def _closed_wires_from_entities(sheet: Sheet2D, entities: Sequence[Entity2D]) -> list[Any]:
    wires = _wires_from_entities(sheet, entities)
    open_names = [index for index, wire in enumerate(wires) if not wire.is_closed]
    if open_names:
        raise ValueError(
            "A face needs closed contours; open wire(s) at selection index "
            + ", ".join(str(index) for index in open_names)
            + "."
        )
    invalid = [index for index, wire in enumerate(wires) if not wire.is_valid]
    if invalid:
        raise ValueError("OCCT reported invalid closed contour(s): " + ", ".join(map(str, invalid)) + ".")
    return wires


def _faces_from_nested_wires(wires: Sequence[Any]) -> list[Any]:
    from build123d import Face
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON
    from OCP.gp import gp_Pnt

    if not wires:
        raise ValueError("No closed contours were supplied for face creation.")
    simple_faces = [Face(wire) for wire in wires]
    areas = [float(face.area) for face in simple_faces]
    if any(not math.isfinite(area) or area <= 0 for area in areas):
        raise ValueError("A contour has zero or non-finite native area.")
    samples = []
    for wire in wires:
        edge = wire.edges()[0]
        point = edge.position_at(0.371)
        samples.append(gp_Pnt(float(point.X), float(point.Y), 0.0))

    parents: list[int | None] = [None] * len(wires)
    for child_index, sample in enumerate(samples):
        containing: list[int] = []
        for parent_index, parent_face in enumerate(simple_faces):
            if child_index == parent_index or areas[parent_index] <= areas[child_index] + 1e-9:
                continue
            classifier = BRepClass_FaceClassifier(
                parent_face.wrapped, sample, _GEOMETRY_TOLERANCE
            )
            state = classifier.State()
            if state == TopAbs_ON:
                raise ValueError("Closed contours touch or overlap and cannot form nested faces.")
            if state == TopAbs_IN:
                containing.append(parent_index)
        if containing:
            parents[child_index] = min(containing, key=lambda index: areas[index])

    depths: list[int] = [-1] * len(wires)

    def depth(index: int) -> int:
        if depths[index] >= 0:
            return depths[index]
        parent = parents[index]
        depths[index] = 0 if parent is None else depth(parent) + 1
        return depths[index]

    for index in range(len(wires)):
        depth(index)

    faces = []
    for outer_index, wire in enumerate(wires):
        if depths[outer_index] % 2:
            continue
        holes = [
            wires[index]
            for index, parent_index in enumerate(parents)
            if parent_index == outer_index and depths[index] % 2 == 1
        ]
        face = Face(wire, inner_wires=holes)
        if not face.is_valid or not math.isfinite(float(face.area)) or face.area <= 0:
            raise ValueError("OCCT could not make a valid face from the selected contours.")
        faces.append(face)
    if not faces:
        raise ValueError("Selected contours produced no filled planar region.")
    return faces


def _geometry_snapshot(sheet: Sheet2D, layers: Iterable[str]) -> dict[str, Any]:
    layer_names = tuple(layers)
    layer_set = set(layer_names)
    selected = [entity for entity in sheet.entities.values() if entity.layer in layer_set]
    point_names = list(dict.fromkeys(name for entity in selected for name in entity.points))
    return _json_clone(
        {
            "sheet": sheet.name,
            "units": sheet.units,
            "layers": list(layer_names),
            "points": {name: _point_data(sheet.points[name]) for name in point_names},
            "entities": [_entity_data(entity) for entity in selected],
        },
        "source geometry",
    )


def _entity_geometry_snapshot(sheet: Sheet2D, entities: Sequence[Entity2D]) -> dict[str, Any]:
    point_names = list(dict.fromkeys(name for entity in entities for name in entity.points))
    return _json_clone(
        {
            "sheet": sheet.name,
            "units": sheet.units,
            "layers": list(dict.fromkeys(entity.layer for entity in entities)),
            "points": {name: _point_data(sheet.points[name]) for name in point_names},
            "entities": [_entity_data(entity) for entity in entities],
        },
        "pattern source geometry",
    )


def _result_edges(native_shape: Any) -> list[Any]:
    from build123d import Edge, Shape

    native = getattr(native_shape, "wrapped", native_shape)
    shape = Shape.cast(native)
    if isinstance(shape, Edge):
        return [shape]
    wires = list(shape.wires())
    if wires:
        return [edge for wire in wires for edge in wire.edges()]
    return list(shape.edges())


def _edge_record(edge: Any, z_offset: float = 0.0) -> dict[str, Any]:
    geom_name = edge.geom_type.name.lower()
    if geom_name == "line":
        start = edge.start_point()
        end = edge.end_point()
        if abs(float(start.Z) - z_offset) > _GEOMETRY_TOLERANCE or abs(
            float(end.Z) - z_offset
        ) > _GEOMETRY_TOLERANCE:
            raise ValueError("OCCT returned a line outside the selected sheet plane.")
        record = {
            "kind": "line",
            "start": (float(start.X), float(start.Y), float(start.Z) - z_offset),
            "end": (float(end.X), float(end.Y), float(end.Z) - z_offset),
        }
        if math.dist(record["start"], record["end"]) <= _POINT_TOLERANCE:
            raise ValueError("OCCT returned a zero-length line edge.")
        return record
    if geom_name == "circle":
        center = edge.arc_center
        center_xyz = (float(center.X), float(center.Y), float(center.Z) - z_offset)
        radius = _positive(edge.radius, "native circular edge radius")
        if abs(center_xyz[2]) > _GEOMETRY_TOLERANCE:
            raise ValueError("OCCT returned a curve outside the selected sheet plane.")
        if edge.is_closed:
            return {"kind": "circle", "center": center_xyz, "radius": radius}
        start = edge.start_point()
        end = edge.end_point()
        tangent = edge.tangent_at(0)
        start_xyz = (float(start.X), float(start.Y), float(start.Z) - z_offset)
        end_xyz = (float(end.X), float(end.Y), float(end.Z) - z_offset)
        clockwise = (
            (start.X - center.X) * tangent.Y - (start.Y - center.Y) * tangent.X
        ) < 0
        start_angle = math.degrees(math.atan2(start.Y - center.Y, start.X - center.X)) % 360.0
        end_angle = math.degrees(math.atan2(end.Y - center.Y, end.X - center.X)) % 360.0
        sweep = (start_angle - end_angle) % 360.0 if clockwise else (end_angle - start_angle) % 360.0
        if sweep <= 1e-8:
            raise ValueError("OCCT returned a circular edge with an indeterminate sweep.")
        if abs(start_xyz[2]) > _GEOMETRY_TOLERANCE or abs(end_xyz[2]) > _GEOMETRY_TOLERANCE:
            raise ValueError("OCCT returned a curve outside the selected sheet plane.")
        return {
            "kind": "arc",
            "center": center_xyz,
            "radius": radius,
            "start_angle": start_angle,
            "end_angle": end_angle,
            "clockwise": clockwise,
        }
    raise NotImplementedError(
        f"Exact editable 2D results currently support lines and circles/arcs; OCCT returned {geom_name}."
    )


def _record_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    kind = record["kind"]
    rounded = lambda value: round(float(value), 9)
    if kind == "line":
        points = [tuple(rounded(value) for value in record[key][:2]) for key in ("start", "end")]
        return (kind, tuple(sorted(points)))
    if kind == "circle":
        center = record["center"]
        return (kind, rounded(center[0]), rounded(center[1]), rounded(record["radius"]))
    center = record["center"]
    return (
        kind,
        rounded(center[0]),
        rounded(center[1]),
        rounded(record["radius"]),
        rounded(record["start_angle"]),
        rounded(record["end_angle"]),
        bool(record["clockwise"]),
    )


def _coalesce_full_circles(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    arc_groups: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    output: list[dict[str, Any]] = []
    for record in records:
        if record["kind"] != "arc":
            output.append(record)
            continue
        center = record["center"]
        key = (round(center[0] * 1e8), round(center[1] * 1e8), round(record["radius"] * 1e8))
        arc_groups.setdefault(key, []).append(record)
    for group in arc_groups.values():
        sweeps = [
            (record["start_angle"] - record["end_angle"]) % 360.0
            if record["clockwise"]
            else (record["end_angle"] - record["start_angle"]) % 360.0
            for record in group
        ]
        if (
            len(group) >= 2
            and len({bool(record["clockwise"]) for record in group}) == 1
            and math.isclose(sum(sweeps), 360.0, abs_tol=1e-6)
        ):
            first = group[0]
            output.append(
                {"kind": "circle", "center": first["center"], "radius": first["radius"]}
            )
        else:
            output.extend(group)
    return output


def _derived_sheet(
    source: Sheet2D,
    native_shape: Any,
    *,
    operation: str,
    result_name: str,
    output_layer: str,
    sources: list[dict[str, Any]],
    parameters: dict[str, Any],
    other: Sheet2D | None = None,
    source_entities: Sequence[Entity2D] = (),
    z_offset: float = 0.0,
) -> Sheet2D:
    from build123d import Shape

    result_name = _checked_name(result_name, "result sheet name")
    output_layer = _checked_name(output_layer, "output layer")
    shape = Shape.cast(getattr(native_shape, "wrapped", native_shape))
    if not shape.is_valid:
        raise ValueError(f"OCCT returned invalid geometry for {operation}.")
    records = _coalesce_full_circles(
        [_edge_record(edge, z_offset=z_offset) for edge in _result_edges(shape)]
    )
    records.sort(key=_record_key)
    if not records:
        raise ValueError(f"The {operation} result contains no non-zero native geometry.")

    points: dict[str, Point2D] = {}
    coordinate_names: dict[tuple[int, int], str] = {}

    def point_name(x: float, y: float) -> str:
        key = (round(x * 1e9), round(y * 1e9))
        if key in coordinate_names:
            return coordinate_names[key]
        name = f"{result_name}.p{len(points)}"
        point = Point2D(name, x, y)
        points[name] = point
        coordinate_names[key] = name
        return name

    entities: dict[str, Entity2D] = {}
    for index, record in enumerate(records):
        entity_name = f"{result_name}.e{index}"
        metadata = {"operation": operation}
        if record["kind"] == "line":
            start, end = record["start"], record["end"]
            first = point_name(start[0], start[1])
            second = point_name(end[0], end[1])
            entity = Entity2D(entity_name, "line", output_layer, (first, second), metadata=metadata)
        elif record["kind"] == "circle":
            center = record["center"]
            center_name = point_name(center[0], center[1])
            entity = Entity2D(
                entity_name,
                "circle",
                output_layer,
                (center_name,),
                radius=record["radius"],
                closed=True,
                metadata=metadata,
            )
        else:
            center = record["center"]
            center_name = point_name(center[0], center[1])
            entity = Entity2D(
                entity_name,
                "arc",
                output_layer,
                (center_name,),
                radius=record["radius"],
                start_angle=record["start_angle"],
                end_angle=record["end_angle"],
                metadata={**metadata, "clockwise": record["clockwise"]},
            )
        entities[entity_name] = entity

    layers = copy.deepcopy(source.layers)
    if other is not None:
        for layer_name, properties in other.layers.items():
            layers.setdefault(layer_name, copy.deepcopy(properties))
    layers.setdefault(output_layer, {"role": output_layer})
    history = copy.deepcopy(source.history)
    if other is not None:
        history.extend(copy.deepcopy(other.history))
    result_entity_names = list(entities)
    result_points = {name: _point_data(point) for name, point in points.items()}
    history.append(
        {
            "operation": operation,
            "name": result_name,
            "sources": _json_clone(sources, "operation sources"),
            "source_names": [entity.name for entity in source_entities],
            "parameters": _json_clone(parameters, "operation parameters"),
            "result_geometry": {
                "points": result_points,
                "entities": [_entity_data(entity) for entity in entities.values()],
            },
            "result_names": result_entity_names,
        }
    )
    return Sheet2D(
        name=result_name,
        units=source.units,
        points=points,
        entities=entities,
        layers=layers,
        history=history,
        constraints=[],
    )


def _pattern_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 2:
        raise ValueError("Pattern count must be an integer of at least 2.")
    return value


def _pattern_vector(
    spacing: CoordinateInput | None,
    dx: float | None,
    dy: float | None,
) -> tuple[float, float]:
    if dx is not None or dy is not None:
        if spacing is not None:
            raise ValueError("Specify either spacing or dx/dy, not both.")
        return (_finite(0.0 if dx is None else dx, "pattern dx"), _finite(0.0 if dy is None else dy, "pattern dy"))
    if spacing is None:
        raise ValueError("Linear pattern needs spacing=(dx, dy) or dx/dy.")
    if isinstance(spacing, str):
        raise ValueError("Linear pattern spacing must be a vector, not a point name.")
    if isinstance(spacing, Point2D):
        return spacing.as_tuple()
    return _coordinate_pair(spacing, "pattern spacing")


def _copy_entities_transformed(
    result: Sheet2D,
    selected: Sequence[Entity2D],
    *,
    transform: Any,
    angle: float,
    name_suffix: str,
    rotate_arcs: bool = True,
) -> list[str]:
    point_map: dict[str, str] = {}
    for entity in selected:
        for point_name in entity.points:
            if point_name in point_map:
                continue
            source_point = result.points[point_name]
            x, y = transform(source_point.x, source_point.y)
            new_point_name = f"{point_name}.{name_suffix}"
            if new_point_name in result.points:
                raise ValueError(f"Pattern point name {new_point_name!r} already exists.")
            result.points[new_point_name] = Point2D(new_point_name, x, y)
            point_map[point_name] = new_point_name
    generated_names = []
    for entity in selected:
        entity_name = f"{entity.name}.{name_suffix}"
        if entity_name in result.entities:
            raise ValueError(f"Pattern entity name {entity_name!r} already exists.")
        metadata = copy.deepcopy(entity.metadata)
        if entity.kind == "text" and rotate_arcs and angle:
            metadata["rotation_deg"] = float(metadata.get("rotation_deg", 0.0)) + angle
        if entity.kind == "ellipse" and rotate_arcs and angle:
            metadata["rotation_deg"] += angle
        start_angle = entity.start_angle
        end_angle = entity.end_angle
        if entity.kind == "arc" and rotate_arcs and angle:
            start_angle = float(start_angle) + angle
            end_angle = float(end_angle) + angle
        clone = Entity2D(
            name=entity_name,
            kind=entity.kind,
            layer=entity.layer,
            points=tuple(point_map[point_name] for point_name in entity.points),
            radius=entity.radius,
            start_angle=start_angle,
            end_angle=end_angle,
            closed=entity.closed,
            text=entity.text,
            height=entity.height,
            metadata=metadata,
        )
        result._add_entity(clone, history=False)
        generated_names.append(entity_name)
    return generated_names


def sheet2d(name: str = "sheet", units: str = "mm") -> Sheet2D:
    """Create a Sheet2D that can be used directly or as a context manager."""
    return Sheet2D(name=name, units=units)


def latest_sheet() -> Sheet2D | None:
    """Return the active sheet, or the most recently created sheet in this context."""
    return _ACTIVE_SHEET.get() or _LATEST_SHEET.get()


__all__ = ["Point2D", "Entity2D", "Sheet2D", "sheet2d", "latest_sheet"]
