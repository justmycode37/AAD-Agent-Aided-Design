"""Native, exact 2D SVG and DXF export.

This module reads the structural ``ctx.sheet`` contract without importing the
CAD kernel.  SVG and DXF are regenerated from Sheet2D's source entities; import
attributes and history are provenance only and are never replayed as markup.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import io
import math
import os
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ._safe_output import OutputDirectory


_SVG_NS = "http://www.w3.org/2000/svg"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_VALID_UNITS = {"mm", "in"}
_ENTITY_KINDS = {"line", "circle", "arc", "polyline", "rectangle", "bezier", "ellipse", "text"}
_TRANSFORM_KEYS = {"transform", "transforms", "matrix", "affine_transform"}
_PROVENANCE_KEYS = (
    "source_name", "source_id", "source_tag", "source_layer", "source_group_ids", "feature"
)
_EPS = 1e-12


@dataclass(frozen=True)
class _Entity:
    name: str
    kind: str
    layer: str
    point_names: tuple[str, ...]
    points: tuple[tuple[float, float], ...]
    closed: bool
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    text: str | None = None
    height: float | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class _Sheet:
    name: str
    units: str
    layers: tuple[str, ...]
    entities: tuple[_Entity, ...]


@dataclass(frozen=True)
class _Bounds:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def width(self) -> float:
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        return self.ymax - self.ymin


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _units(value: Any, label: str) -> str:
    if hasattr(value, "value"):
        value = value.value
    if not isinstance(value, str) or value.strip().lower() not in _VALID_UNITS:
        raise ValueError(f"{label} must be exactly 'mm' or 'in'.")
    return value.strip().lower()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number, not a boolean.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite.")
    return result


def _positive(value: Any, label: str) -> float:
    result = _number(value, label)
    if result <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return result


def _json_value(value: Any, label: str) -> Any:
    """Copy JSON-native data while rejecting non-string keys and non-finite values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must contain only finite JSON-native data.")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} mapping keys must be strings.")
            result[key] = _json_value(item, label)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item, label) for item in value]
    raise ValueError(f"{label} must contain only finite JSON-native data.")


def _read_sheet(ctx: Any) -> _Sheet:
    """Take a validated, immutable snapshot without rebuilding or mutating Sheet2D."""
    raw_sheet = _field(ctx, "sheet")
    if raw_sheet is None:
        raise ValueError("2D context must expose ctx.sheet as Sheet2D or its to_dict() mapping.")
    to_dict = getattr(raw_sheet, "to_dict", None)
    if callable(to_dict):
        raw_sheet = to_dict()
    if not isinstance(raw_sheet, Mapping):
        raise ValueError("ctx.sheet must be Sheet2D or a serialized Sheet2D mapping.")

    context_units = _units(_field(ctx, "units"), "Context units")
    sheet_units = _units(raw_sheet.get("units"), "Sheet units")
    if context_units != sheet_units:
        raise ValueError(
            f"Context units {context_units!r} do not match Sheet2D units {sheet_units!r}."
        )

    raw_points = raw_sheet.get("points")
    raw_entities = raw_sheet.get("entities")
    raw_layers = raw_sheet.get("layers")
    if not isinstance(raw_points, Mapping):
        raise ValueError("Serialized Sheet2D points must be a mapping.")
    if not isinstance(raw_entities, Mapping):
        raise ValueError("Serialized Sheet2D entities must be a mapping.")
    if not isinstance(raw_layers, Mapping):
        raise ValueError("Serialized Sheet2D layers must be a mapping.")

    points: dict[str, tuple[float, float]] = {}
    for key, value in raw_points.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, Mapping):
            raise ValueError("Every serialized Sheet2D point must be a named coordinate mapping.")
        name = value.get("name", key)
        if name != key:
            raise ValueError(f"Serialized point key {key!r} does not match its name {name!r}.")
        points[key] = (_number(value.get("x"), f"point {key}.x"),
                       _number(value.get("y"), f"point {key}.y"))

    layers: list[str] = []
    for name, metadata in raw_layers.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Sheet2D layer names must be non-empty strings.")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise ValueError(f"Layer {name!r} metadata must be a mapping.")
        if name not in layers:
            layers.append(name)

    entities: list[_Entity] = []
    for key, raw in raw_entities.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(raw, Mapping):
            raise ValueError("Every serialized Sheet2D entity must be a named entity mapping.")
        name = raw.get("name", key)
        if name != key:
            raise ValueError(f"Serialized entity key {key!r} does not match its name {name!r}.")
        kind = raw.get("kind")
        if not isinstance(kind, str) or kind not in _ENTITY_KINDS:
            raise ValueError(f"Entity {name!r} has unsupported kind {kind!r}.")
        layer = raw.get("layer", "cut")
        if not isinstance(layer, str) or not layer.strip():
            raise ValueError(f"Entity {name!r} has an invalid layer name.")
        if layer not in layers:
            layers.append(layer)
        raw_names = raw.get("points", ())
        if isinstance(raw_names, (str, bytes)) or not isinstance(raw_names, Sequence):
            raise ValueError(f"Entity {name!r} points must be a sequence of point names.")
        point_names = tuple(raw_names)
        if any(not isinstance(point_name, str) or point_name not in points for point_name in point_names):
            raise ValueError(f"Entity {name!r} refers to missing or invalid point names.")
        coords = tuple(points[point_name] for point_name in point_names)
        closed = raw.get("closed", False)
        if not isinstance(closed, bool):
            raise ValueError(f"Entity {name!r} closed must be a boolean.")
        raw_metadata = raw.get("metadata", {})
        if not isinstance(raw_metadata, Mapping):
            raise ValueError(f"Entity {name!r} metadata must be a mapping.")
        metadata = _json_value(raw_metadata, f"Entity {name!r} metadata")
        # SVG import stores transforms and source attributes as provenance after
        # it has already converted them into exact sheet coordinates. A direct
        # transform on a Sheet2D entity has no defined native interpretation.
        active_transforms = _TRANSFORM_KEYS.intersection(metadata)
        if active_transforms:
            # Imported2D has already resolved static SVG geometry into sheet
            # coordinates. Its only remaining top-level transform is text
            # provenance; accept that record only when the importer marked it
            # as an unrotated similarity transform. Never flatten a caller
            # supplied affine transform here.
            flattened_text_transform = False
            if active_transforms == {"transform"} and kind == "text":
                matrix = metadata.get("transform")
                imported_text = (
                    metadata.get("source_tag") == "text"
                    and isinstance(metadata.get("source_element_attributes"), Mapping)
                    and metadata.get("text_is_similarity_transform") is True
                )
                if imported_text and isinstance(matrix, Sequence) and not isinstance(matrix, (str, bytes)) and len(matrix) == 6:
                    a, b, c, d, e, f = (
                        _number(value, f"Entity {name!r} imported text transform") for value in matrix
                    )
                    scale_x, scale_y = math.hypot(a, b), math.hypot(c, d)
                    scale = max(1.0, scale_x, scale_y)
                    rotation = _number(metadata.get("rotation", 0.0), f"Entity {name!r} imported text rotation")
                    flattened_text_transform = (
                        scale_x > _EPS
                        and abs(scale_x - scale_y) <= scale * 1e-9
                        and abs(a * c + b * d) <= scale * scale * 1e-9
                        and abs(rotation) <= 1e-9
                    )
            if not flattened_text_transform:
                raise ValueError(
                    f"Entity {name!r} has an unsupported transform; represent it in native coordinates first."
                )
        rotation = metadata.get("rotation_deg")
        if rotation is not None:
            rotation = _number(rotation, f"Entity {name!r} rotation_deg")
            if kind not in {"ellipse", "text"} and abs(rotation) > _EPS:
                raise ValueError(
                    f"Entity {name!r} rotation_deg is unsupported for {kind}; represent it in native coordinates."
                )

        radius = raw.get("radius")
        start = raw.get("start_angle")
        end = raw.get("end_angle")
        height = raw.get("height")
        text_value = raw.get("text")
        if kind == "line":
            if len(coords) != 2 or math.dist(*coords) <= _EPS:
                raise ValueError(f"Line entity {name!r} must have two distinct points.")
        elif kind == "circle":
            if len(coords) != 1:
                raise ValueError(f"Circle entity {name!r} must have one center point.")
            radius = _positive(radius, f"Entity {name!r} radius")
            closed = True
        elif kind == "arc":
            if len(coords) != 1:
                raise ValueError(f"Arc entity {name!r} must have one center point.")
            radius = _positive(radius, f"Entity {name!r} radius")
            start = _number(start, f"Entity {name!r} start_angle")
            end = _number(end, f"Entity {name!r} end_angle")
            if _sweep_degrees(start, end, bool(metadata.get("clockwise", False))) <= _EPS:
                raise ValueError(f"Arc entity {name!r} must have a nonzero sweep below 360 degrees.")
        elif kind in {"polyline", "rectangle"}:
            if len(coords) < (3 if closed or kind == "rectangle" else 2):
                raise ValueError(f"Polyline entity {name!r} has too few vertices.")
            if kind == "rectangle" and (len(coords) != 4 or not closed):
                raise ValueError(f"Rectangle entity {name!r} must have four vertices and be closed.")
        elif kind == "bezier":
            if len(coords) not in {3, 4}:
                raise ValueError(f"Bezier entity {name!r} must have three or four control points.")
        elif kind == "ellipse":
            if len(coords) != 1:
                raise ValueError(f"Ellipse entity {name!r} must have one center point.")
            _positive(metadata.get("rx"), f"Entity {name!r} ellipse rx")
            _positive(metadata.get("ry"), f"Entity {name!r} ellipse ry")
            start = _number(start, f"Entity {name!r} start_angle")
            end = _number(end, f"Entity {name!r} end_angle")
            span = abs(end - start)
            if span <= _EPS or span > 360.0 + _EPS:
                raise ValueError(f"Ellipse entity {name!r} must have a nonzero span of at most 360 degrees.")
            if not closed and math.isclose((end - start) % 360.0, 0.0, abs_tol=1e-10):
                raise ValueError(f"Open ellipse entity {name!r} cannot make a full revolution.")
        elif kind == "text":
            if len(coords) != 1 or not isinstance(text_value, str):
                raise ValueError(f"Text entity {name!r} needs one insertion point and string content.")
            height = _positive(height, f"Entity {name!r} text height")
            if rotation is None:
                metadata = {**metadata, "rotation_deg": 0.0}
            else:
                metadata = {**metadata, "rotation_deg": rotation}

        clockwise = metadata.get("clockwise", False)
        if not isinstance(clockwise, bool):
            raise ValueError(f"Entity {name!r} metadata clockwise must be a boolean.")
        entities.append(_Entity(
            name=name,
            kind=kind,
            layer=layer,
            point_names=point_names,
            points=coords,
            closed=closed,
            radius=float(radius) if radius is not None else None,
            start_angle=float(start) if start is not None else None,
            end_angle=float(end) if end is not None else None,
            text=text_value,
            height=float(height) if height is not None else None,
            metadata=metadata,
        ))

    sheet_name = raw_sheet.get("name", "sheet")
    if not isinstance(sheet_name, str) or not sheet_name.strip():
        sheet_name = "sheet"
    return _Sheet(sheet_name, context_units, tuple(layers), tuple(entities))


def _check_job_confinement(ctx: Any, path: Path) -> Path:
    target = path.expanduser().resolve()
    job_dir = _field(ctx, "job_dir")
    if job_dir is None:
        return target
    root = Path(job_dir).expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Output path {target} must be under ctx.job_dir {root}.") from exc
    return target


def _atomic_write(path: Path, data: bytes, job_root: Path | None = None) -> Path:
    with OutputDirectory(path.parent, job_root) as directory:
        if directory.is_dir(path.name):
            raise ValueError(f"Output path must name a file, got directory {path}.")
        temporary = directory.stage(path.name, data)
        backup = None
        installed = False
        try:
            directory.ensure_current()
            backup = directory.preserve(path.name)
            directory.replace(temporary, path.name)
            installed = True
            directory.verify_staged(temporary, path.name)
            directory.ensure_current()
        except BaseException:
            if backup is not None:
                if installed:
                    directory.replace(backup, path.name)
                else:
                    directory.unlink(backup)
            elif installed:
                directory.unlink(path.name)
            directory.unlink(temporary)
            raise
        if backup is not None:
            try:
                directory.unlink(backup)
            except OSError:
                pass
        return directory.path / path.name


def _fmt(value: float) -> str:
    number = float(value)
    if number == 0:
        return "0"
    result = repr(number)
    if result.endswith(".0"):
        result = result[:-2]
    return result


def _text_lines(value: str) -> list[str]:
    return value.replace("\r\n", "\n").replace("\r", "\n").expandtabs(8).split("\n")


def _sweep_degrees(start: float, end: float, clockwise: bool) -> float:
    if clockwise:
        return (start - end) % 360.0
    return (end - start) % 360.0


def _angle_is_on_sweep(angle: float, start: float, end: float, clockwise: bool) -> bool:
    sweep = _sweep_degrees(start, end, clockwise)
    if clockwise:
        delta = (start - angle) % 360.0
    else:
        delta = (angle - start) % 360.0
    return delta <= sweep + 1e-10


def _arc_point(center: tuple[float, float], radius: float, angle_deg: float) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    return (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))


def _ellipse_point(
    center: tuple[float, float], rx: float, ry: float, rotation_deg: float, parameter_deg: float
) -> tuple[float, float]:
    phi = math.radians(rotation_deg)
    parameter = math.radians(parameter_deg)
    x0 = rx * math.cos(parameter)
    y0 = ry * math.sin(parameter)
    return (
        center[0] + x0 * math.cos(phi) - y0 * math.sin(phi),
        center[1] + x0 * math.sin(phi) + y0 * math.cos(phi),
    )


def _bezier_point(points: Sequence[tuple[float, float]], t: float) -> tuple[float, float]:
    if len(points) == 3:
        u = 1.0 - t
        return tuple(u * u * points[0][axis] + 2 * u * t * points[1][axis] + t * t * points[2][axis]
                     for axis in (0, 1))  # type: ignore[return-value]
    u = 1.0 - t
    return tuple(
        u * u * u * points[0][axis]
        + 3 * u * u * t * points[1][axis]
        + 3 * u * t * t * points[2][axis]
        + t * t * t * points[3][axis]
        for axis in (0, 1)
    )  # type: ignore[return-value]


def _quadratic_roots(a: float, b: float, c: float) -> tuple[float, ...]:
    scale = max(abs(a), abs(b), abs(c), 1.0)
    tolerance = scale * 1e-14
    if abs(a) <= tolerance:
        if abs(b) <= tolerance:
            return ()
        return (-c / b,)
    discriminant = b * b - 4.0 * a * c
    if discriminant < -tolerance * scale:
        return ()
    if discriminant <= tolerance * scale:
        return (-b / (2.0 * a),)
    root = math.sqrt(discriminant)
    # The q-form avoids losing the smaller root when b and sqrt(discriminant) nearly cancel.
    q = -0.5 * (b + math.copysign(root, b))
    if q == 0:
        return (-b / (2.0 * a),)
    return (q / a, c / q)


def _bezier_extrema_points(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    parameters = {0.0, 1.0}
    degree = len(points) - 1
    for axis in (0, 1):
        p = [point[axis] for point in points]
        if degree == 2:
            denom = p[0] - 2.0 * p[1] + p[2]
            if abs(denom) > max(abs(value) for value in p) * 1e-14:
                root = (p[0] - p[1]) / denom
                if 0.0 < root < 1.0:
                    parameters.add(root)
        else:
            a = -p[0] + 3.0 * p[1] - 3.0 * p[2] + p[3]
            b = 3.0 * p[0] - 6.0 * p[1] + 3.0 * p[2]
            c = -3.0 * p[0] + 3.0 * p[1]
            for root in _quadratic_roots(3.0 * a, 2.0 * b, c):
                if 0.0 < root < 1.0:
                    parameters.add(root)
    return [_bezier_point(points, t) for t in sorted(parameters)]


def _entity_bounds(entity: _Entity) -> _Bounds | None:
    if entity.kind == "text":
        return None
    if entity.kind == "line" or entity.kind in {"polyline", "rectangle", "bezier"}:
        points = _bezier_extrema_points(entity.points) if entity.kind == "bezier" else entity.points
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return _Bounds(min(xs), min(ys), max(xs), max(ys))
    center = entity.points[0]
    if entity.kind == "circle":
        radius = entity.radius
        return _Bounds(center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius)
    if entity.kind == "arc":
        start, end = entity.start_angle, entity.end_angle
        clockwise = bool(entity.metadata.get("clockwise", False))
        angles = [start, end]
        angles.extend(
            angle for angle in (0.0, 90.0, 180.0, 270.0)
            if _angle_is_on_sweep(angle, start, end, clockwise)
        )
        coords = [_arc_point(center, entity.radius, angle) for angle in angles]
        xs = [point[0] for point in coords]
        ys = [point[1] for point in coords]
        return _Bounds(min(xs), min(ys), max(xs), max(ys))

    rx = float(entity.metadata["rx"])
    ry = float(entity.metadata["ry"])
    rotation = float(entity.metadata.get("rotation_deg", 0.0))
    if entity.closed:
        phi = math.radians(rotation)
        x_radius = math.hypot(rx * math.cos(phi), ry * math.sin(phi))
        y_radius = math.hypot(rx * math.sin(phi), ry * math.cos(phi))
        return _Bounds(center[0] - x_radius, center[1] - y_radius,
                       center[0] + x_radius, center[1] + y_radius)

    start, end = entity.start_angle, entity.end_angle
    clockwise = bool(entity.metadata.get("clockwise", False))
    phi = math.radians(rotation)
    x_a, x_b = rx * math.cos(phi), -ry * math.sin(phi)
    y_a, y_b = rx * math.sin(phi), ry * math.cos(phi)
    candidates = [start, end]
    x_critical = math.degrees(math.atan2(x_b, x_a))
    y_critical = math.degrees(math.atan2(y_b, y_a))
    candidates.extend((x_critical, x_critical + 180.0, y_critical, y_critical + 180.0))
    angles = [
        angle for angle in candidates
        if angle in (start, end) or _angle_is_on_sweep(angle, start, end, clockwise)
    ]
    coords = [_ellipse_point(center, rx, ry, rotation, angle) for angle in angles]
    xs = [point[0] for point in coords]
    ys = [point[1] for point in coords]
    return _Bounds(min(xs), min(ys), max(xs), max(ys))


def _geometry_bounds(sheet: _Sheet, entities: Iterable[_Entity] | None = None) -> _Bounds | None:
    bounds = [bound for entity in (sheet.entities if entities is None else entities)
              if (bound := _entity_bounds(entity)) is not None]
    if not bounds:
        return None
    return _Bounds(
        min(bound.xmin for bound in bounds), min(bound.ymin for bound in bounds),
        max(bound.xmax for bound in bounds), max(bound.ymax for bound in bounds),
    )


def _viewport(sheet: _Sheet, entities: Iterable[_Entity] | None = None) -> tuple[float, float, float, float]:
    selected = tuple(sheet.entities if entities is None else entities)
    bounds = _geometry_bounds(sheet, selected)
    xs = [] if bounds is None else [bounds.xmin, bounds.xmax]
    ys = [] if bounds is None else [bounds.ymin, bounds.ymax]
    for entity in selected:
        if entity.kind == "text":
            # Text is not native cut geometry, but its viewport must contain
            # more than the insertion point. Use a conservative font envelope
            # (including fallback Unicode glyphs), without altering model bbox.
            height = float(entity.height)
            lines = _text_lines(entity.text or "")
            width = max(1, *(len(line) for line in lines)) * 2 * height
            below = (0.4 + 1.3 * (len(lines) - 1)) * height
            phi = math.radians(float(entity.metadata.get("rotation_deg", 0)))
            cx, cy = entity.points[0]
            for x, y in ((-.3*height, -below), (width+.3*height, -below),
                         (width+.3*height, 1.2*height), (-.3*height, 1.2*height)):
                xs.append(cx + x * math.cos(phi) - y * math.sin(phi))
                ys.append(cy + x * math.sin(phi) + y * math.cos(phi))
    if not xs:
        return (0.0, 0.0, 1.0, 1.0)
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    width, height = xmax - xmin, ymax - ymin
    if width <= _EPS:
        width = 1.0
        xmin -= width / 2.0
    if height <= _EPS:
        height = 1.0
        ymin -= height / 2.0
    return (xmin, ymin, width, height)


def _selected(sheet: _Sheet, layers: str | Iterable[str] | None) -> tuple[tuple[str, ...], tuple[_Entity, ...]]:
    if layers is None:
        layer_names = sheet.layers
    else:
        if isinstance(layers, str):
            requested = (layers,)
        else:
            try:
                requested = tuple(layers)
            except TypeError as exc:
                raise ValueError("layers must be a layer name or iterable of layer names.") from exc
        if not requested or any(not isinstance(name, str) or not name.strip() for name in requested):
            raise ValueError("layers must contain at least one non-empty layer name.")
        unknown = set(requested).difference(sheet.layers)
        if unknown:
            raise ValueError("Unknown 2D layer(s): " + ", ".join(sorted(unknown)) + ".")
        requested_set = set(requested)
        layer_names = tuple(name for name in sheet.layers if name in requested_set)
    chosen = tuple(entity for entity in sheet.entities if entity.layer in set(layer_names))
    return tuple(layer_names), chosen


def _svg_coordinate(point: tuple[float, float]) -> tuple[float, float]:
    return (point[0], -point[1])


def _validate_xml_string(value: str, label: str) -> None:
    for char in value:
        codepoint = ord(char)
        valid = (
            codepoint in {0x9, 0xA, 0xD}
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        )
        if not valid:
            raise ValueError(f"{label} contains XML-incompatible character U+{codepoint:04X}.")


def _svg_point_text(point: tuple[float, float]) -> str:
    x, y = _svg_coordinate(point)
    return f"{_fmt(x)} {_fmt(y)}"


def _append_svg_entity(parent: ET.Element, entity: _Entity) -> None:
    attributes = {"id": entity.name, "data-entity": entity.name, "data-layer": entity.layer}
    if entity.kind == "line":
        first, second = entity.points
        ET.SubElement(parent, f"{{{_SVG_NS}}}line", {
            **attributes,
            "x1": _fmt(first[0]), "y1": _fmt(-first[1]),
            "x2": _fmt(second[0]), "y2": _fmt(-second[1]),
            "fill": "none", "stroke": "#000000", "stroke-width": "0.01",
        })
    elif entity.kind == "circle":
        center = entity.points[0]
        ET.SubElement(parent, f"{{{_SVG_NS}}}circle", {
            **attributes, "cx": _fmt(center[0]), "cy": _fmt(-center[1]),
            "r": _fmt(entity.radius), "fill": "none", "stroke": "#000000",
            "stroke-width": "0.01",
        })
    elif entity.kind == "arc":
        center = entity.points[0]
        start, end = entity.start_angle, entity.end_angle
        clockwise = bool(entity.metadata.get("clockwise", False))
        sweep_angle = _sweep_degrees(start, end, clockwise)
        first = _svg_coordinate(_arc_point(center, entity.radius, start))
        last = _svg_coordinate(_arc_point(center, entity.radius, end))
        sweep_flag = 1 if clockwise else 0
        d = (
            f"M {_fmt(first[0])} {_fmt(first[1])} A {_fmt(entity.radius)} {_fmt(entity.radius)} "
            f"0 {int(sweep_angle > 180.0)} {sweep_flag} {_fmt(last[0])} {_fmt(last[1])}"
        )
        ET.SubElement(parent, f"{{{_SVG_NS}}}path", {
            **attributes, "d": d, "fill": "none", "stroke": "#000000", "stroke-width": "0.01",
        })
    elif entity.kind in {"polyline", "rectangle"}:
        points = " ".join(_svg_point_text(point) for point in entity.points)
        tag = "polygon" if entity.closed else "polyline"
        ET.SubElement(parent, f"{{{_SVG_NS}}}{tag}", {
            **attributes, "points": points, "fill": "none", "stroke": "#000000",
            "stroke-width": "0.01",
        })
    elif entity.kind == "bezier":
        coords = [_svg_coordinate(point) for point in entity.points]
        start = f"M {_fmt(coords[0][0])} {_fmt(coords[0][1])}"
        if len(coords) == 3:
            curve = f"Q {_fmt(coords[1][0])} {_fmt(coords[1][1])} {_fmt(coords[2][0])} {_fmt(coords[2][1])}"
        else:
            curve = (
                f"C {_fmt(coords[1][0])} {_fmt(coords[1][1])} {_fmt(coords[2][0])} {_fmt(coords[2][1])} "
                f"{_fmt(coords[3][0])} {_fmt(coords[3][1])}"
            )
        ET.SubElement(parent, f"{{{_SVG_NS}}}path", {
            **attributes, "d": f"{start} {curve}", "fill": "none", "stroke": "#000000",
            "stroke-width": "0.01",
        })
    elif entity.kind == "ellipse":
        center = entity.points[0]
        rx, ry = float(entity.metadata["rx"]), float(entity.metadata["ry"])
        rotation = float(entity.metadata.get("rotation_deg", 0.0))
        if entity.closed:
            transform = f"rotate({_fmt(-rotation)} {_fmt(center[0])} {_fmt(-center[1])})"
            ET.SubElement(parent, f"{{{_SVG_NS}}}ellipse", {
                **attributes, "cx": _fmt(center[0]), "cy": _fmt(-center[1]),
                "rx": _fmt(rx), "ry": _fmt(ry), "transform": transform,
                "fill": "none", "stroke": "#000000", "stroke-width": "0.01",
            })
        else:
            start, end = entity.start_angle, entity.end_angle
            clockwise = bool(entity.metadata.get("clockwise", False))
            sweep_angle = _sweep_degrees(start, end, clockwise)
            first = _svg_coordinate(_ellipse_point(center, rx, ry, rotation, start))
            last = _svg_coordinate(_ellipse_point(center, rx, ry, rotation, end))
            sweep_flag = 1 if clockwise else 0
            d = (
                f"M {_fmt(first[0])} {_fmt(first[1])} A {_fmt(rx)} {_fmt(ry)} {_fmt(-rotation)} "
                f"{int(sweep_angle > 180.0)} {sweep_flag} {_fmt(last[0])} {_fmt(last[1])}"
            )
            ET.SubElement(parent, f"{{{_SVG_NS}}}path", {
                **attributes, "d": d, "fill": "none", "stroke": "#000000", "stroke-width": "0.01",
            })
    elif entity.kind == "text":
        x, y = _svg_coordinate(entity.points[0])
        rotation = float(entity.metadata.get("rotation_deg", 0.0))
        attrs = {
            **attributes, "x": _fmt(x), "y": _fmt(y), "font-size": _fmt(entity.height),
            "font-family": "sans-serif",
            "fill": "#000000", "stroke": "none", f"{{{_XML_NS}}}space": "preserve",
        }
        if abs(rotation) > _EPS:
            attrs["transform"] = f"rotate({_fmt(-rotation)} {_fmt(x)} {_fmt(y)})"
        text = ET.SubElement(parent, f"{{{_SVG_NS}}}text", attrs)
        lines = _text_lines(entity.text)
        if len(lines) == 1:
            text.text = lines[0]
        else:
            for index, value in enumerate(lines):
                span = ET.SubElement(text, f"{{{_SVG_NS}}}tspan", {
                    "x": _fmt(x), "y": _fmt(y + index * 1.3 * entity.height),
                })
                span.text = value
    else:  # guarded by _read_sheet
        raise ValueError(f"Unsupported 2D entity kind {entity.kind!r}.")


def _svg_string(
    sheet: _Sheet,
    *,
    layer_names: tuple[str, ...] | None = None,
    entities: tuple[_Entity, ...] | None = None,
) -> str:
    if layer_names is None:
        layer_names = sheet.layers
    if entities is None:
        entities = sheet.entities
    for layer in layer_names:
        _validate_xml_string(layer, "Layer name")
    for entity in entities:
        _validate_xml_string(entity.name, "Entity name")
        _validate_xml_string(entity.layer, f"Entity {entity.name!r} layer")
        if entity.text is not None:
            _validate_xml_string(entity.text, f"Entity {entity.name!r} text")
    xmin, ymin, width, height = _viewport(sheet, entities)
    viewbox = f"{_fmt(xmin)} {_fmt(-(ymin + height))} {_fmt(width)} {_fmt(height)}"
    ET.register_namespace("", _SVG_NS)
    root = ET.Element(f"{{{_SVG_NS}}}svg", {
        "version": "1.1",
        "width": f"{_fmt(width)}{sheet.units}",
        "height": f"{_fmt(height)}{sheet.units}",
        "viewBox": viewbox,
    })
    for layer in layer_names:
        group = ET.SubElement(root, f"{{{_SVG_NS}}}g", {"data-layer": layer})
        for entity in entities:
            if entity.layer == layer:
                _append_svg_entity(group, entity)
    return ET.tostring(root, encoding="unicode", xml_declaration=True, short_empty_elements=True)


def _ellipse_dxf_parameters(entity: _Entity) -> tuple[tuple[float, float], float, float, float]:
    rx, ry = float(entity.metadata["rx"]), float(entity.metadata["ry"])
    rotation = math.radians(float(entity.metadata.get("rotation_deg", 0.0)))
    if rx >= ry:
        major = (rx * math.cos(rotation), rx * math.sin(rotation))
        ratio = ry / rx
        parameter_shift = 0.0
    else:
        # DXF stores its major axis first. Swapping axes changes the source
        # parameter by -90 degrees; the shape and endpoints remain analytic.
        major = (-ry * math.sin(rotation), ry * math.cos(rotation))
        ratio = rx / ry
        parameter_shift = -math.pi / 2.0
    if entity.closed:
        return major, ratio, 0.0, math.tau
    clockwise = bool(entity.metadata.get("clockwise", False))
    if clockwise:
        source_start = float(entity.end_angle)
        sweep = _sweep_degrees(entity.start_angle, entity.end_angle, True)
    else:
        source_start = float(entity.start_angle)
        sweep = _sweep_degrees(entity.start_angle, entity.end_angle, False)
    start_param = math.radians(source_start) + parameter_shift
    start_param %= math.tau
    return major, ratio, start_param, start_param + math.radians(sweep)


def _dxf_bytes(sheet: _Sheet, layer_names: tuple[str, ...], entities: tuple[_Entity, ...]) -> bytes:
    try:
        import ezdxf
    except ImportError as exc:
        raise RuntimeError("DXF export requires the pinned permissive ezdxf dependency.") from exc

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4 if sheet.units == "mm" else 1
    doc.header["$MEASUREMENT"] = 1 if sheet.units == "mm" else 0
    for layer in layer_names:
        if layer.casefold() != "0" and layer not in doc.layers:
            doc.layers.new(layer)
    modelspace = doc.modelspace()
    for entity in entities:
        attrs = {"layer": entity.layer}
        if entity.kind == "line":
            start, end = entity.points
            modelspace.add_line((start[0], start[1], 0), (end[0], end[1], 0), dxfattribs=attrs)
        elif entity.kind == "circle":
            center = entity.points[0]
            modelspace.add_circle((center[0], center[1], 0), entity.radius, dxfattribs=attrs)
        elif entity.kind == "arc":
            center = entity.points[0]
            clockwise = bool(entity.metadata.get("clockwise", False))
            if clockwise:
                start, end = entity.end_angle, entity.start_angle
            else:
                start, end = entity.start_angle, entity.end_angle
            modelspace.add_arc((center[0], center[1], 0), entity.radius,
                               float(start) % 360.0, float(end) % 360.0, dxfattribs=attrs)
        elif entity.kind in {"polyline", "rectangle"}:
            modelspace.add_lwpolyline(entity.points, close=entity.closed, dxfattribs=attrs)
        elif entity.kind == "bezier":
            degree = len(entity.points) - 1
            knots = [0.0] * (degree + 1) + [1.0] * (degree + 1)
            modelspace.add_rational_spline(
                control_points=[(x, y, 0) for x, y in entity.points],
                weights=[1.0] * len(entity.points),
                degree=degree,
                knots=knots,
                dxfattribs=attrs,
            )
        elif entity.kind == "ellipse":
            center = entity.points[0]
            major, ratio, start, end = _ellipse_dxf_parameters(entity)
            modelspace.add_ellipse(
                (center[0], center[1], 0), (major[0], major[1], 0), ratio,
                start_param=start, end_param=end, dxfattribs=attrs,
            )
        elif entity.kind == "text":
            point = entity.points[0]
            rotation = float(entity.metadata.get("rotation_deg", 0.0))
            phi = math.radians(rotation)
            for index, value in enumerate(_text_lines(entity.text)):
                offset = index * 1.3 * entity.height
                modelspace.add_text(value, dxfattribs={
                    **attrs,
                    "insert": (point[0] + offset * math.sin(phi), point[1] - offset * math.cos(phi), 0),
                    "height": entity.height,
                    "rotation": rotation,
                })

    audit = doc.audit()
    if audit.errors:
        details = ", ".join(str(getattr(error, "code", "DXF audit error")) for error in audit.errors[:8])
        raise ValueError(f"Generated R2013 DXF failed ezdxf audit: {details}.")
    output = io.StringIO(newline="\n")
    doc.write(output)
    return output.getvalue().encode("utf-8")


def export_2d(ctx: Any, fmt: str, out: Path, *, layers: str | Iterable[str] | None = None) -> Path:
    """Export a Sheet2D context to one raw SVG or R2013 DXF file.

    Coordinates and units remain those of the source sheet. ``out`` is a file
    path; when ``ctx.job_dir`` is present it must resolve below that directory.
    The function writes atomically and returns the resolved file path.
    """
    if not isinstance(out, (str, os.PathLike)):
        raise TypeError("out must be a file path.")
    format_name = fmt.strip().lower() if isinstance(fmt, str) else ""
    if format_name not in {"svg", "dxf"}:
        raise ValueError("2D export format must be 'svg' or 'dxf'.")
    sheet = _read_sheet(ctx)
    target = _check_job_confinement(ctx, Path(out))
    layer_names, entities = _selected(sheet, layers)
    if format_name == "svg":
        data = _svg_string(sheet, layer_names=layer_names, entities=entities).encode("utf-8")
    else:
        data = _dxf_bytes(sheet, layer_names, entities)
    job_root = _field(ctx, "job_dir")
    return _atomic_write(target, data, Path(job_root) if job_root is not None else None)


__all__ = ["export_2d"]
