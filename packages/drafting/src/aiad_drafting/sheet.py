"""Drawing request normalization, geometry measurement, and sheet layout."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import math
from numbers import Integral
from pathlib import Path
import re
from typing import Any

from .export import Primitive, arc, circle, line, polyline, text
from .geometry import (
    Bounds3D,
    Hole,
    ProjectedBounds,
    ProjectedView,
    UNIT_TO_MM,
    context_shape,
    context_units,
    context_value,
    project_point,
    project_view,
    shape_bounds,
    through_holes,
    _STANDARD_VIEWS,
    _unit,
)
from .types import DimensionSpec, DrawingRequest, DrawingResult, DrawingView


SHEET_SIZES_MM = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "LETTER": (279.4, 215.9),
    "TABLOID": (431.8, 279.4),
}
MARGIN_MM = 10.0
TITLE_BLOCK_WIDTH_MM = 80.0
TITLE_BLOCK_HEIGHT_MM = 27.0
DIMENSION_TEXT_HEIGHT_MM = 3.0


@dataclass(frozen=True)
class _NormalizedRequest:
    sheet: str
    views: tuple[DrawingView, ...]
    dimensions: str | tuple[DimensionSpec, ...]
    style: str
    title_block: dict[str, str]
    formats: tuple[str, ...]


@dataclass(frozen=True)
class _PlacedView:
    projection: ProjectedView
    center: tuple[float, float]
    bounds: ProjectedBounds
    factor: float


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _coerce_view(value: Any) -> DrawingView:
    if isinstance(value, DrawingView):
        view = value
    elif isinstance(value, str):
        view = DrawingView(name=value)
    else:
        name = _value(value, "name", _value(value, "view", _value(value, "type", "front")))
        direction = _value(value, "direction")
        up = _value(value, "up")
        view = DrawingView(
            name=str(name),
            direction=tuple(direction) if direction is not None else None,
            up=tuple(up) if up is not None else None,
            label=_value(value, "label"),
            hidden_lines=bool(_value(value, "hidden_lines", True)),
            kind=str(_value(value, "kind", "orthographic")),
            plane=str(_value(value, "plane", "XY")),
            offset=_value(value, "offset", 0.0),
            center=_value(value, "center"), radius=_value(value, "radius"),
            scale=_value(value, "scale", _value(value, "scale_multiplier", 1.0)),
            parent=_value(value, "parent"),
        )
    name = str(view.name).strip().lower()
    if name == "isometric":
        name = "iso"
    if not name:
        raise ValueError("Drawing view name cannot be empty.")
    if view.kind == "orthographic" and name not in {"front", "back", "top", "bottom", "right", "left", "iso"} and view.direction is None:
        raise ValueError(f"Custom drawing view {name!r} requires a direction vector.")
    for vector_name, vector in (("direction", view.direction), ("up", view.up)):
        if vector is not None and len(vector) != 3:
            raise ValueError(f"View {vector_name} must have three coordinates.")
    # Named views carry fixed axes because their dimensions refer to those axes.
    # Arbitrary directions remain available under a custom view name.
    if view.kind != "orthographic" and name in _STANDARD_VIEWS:
        raise ValueError("Section and detail views require a unique descriptive name.")
    if view.kind == "orthographic" and float(view.scale) != 1.0:
        raise ValueError("Orthographic view scale uses the common sheet scale; magnification is for sections/details.")
    if name in _STANDARD_VIEWS:
        expected_direction, expected_up = _STANDARD_VIEWS[name]
        for vector_name, vector, expected in (
            ("direction", view.direction, expected_direction),
            ("up", view.up, expected_up),
        ):
            if vector is not None:
                if not all(math.isfinite(float(v)) for v in vector):
                    raise ValueError("View vectors must contain finite coordinates.")
                if math.dist(_unit(tuple(vector)), _unit(expected)) > 1e-8:
                    raise ValueError(f"Named view {name!r} has fixed {vector_name} axes; use a custom view name.")
    return replace(view, name=name,
        direction=tuple(float(value) for value in view.direction) if view.direction is not None else None,
        up=tuple(float(value) for value in view.up) if view.up is not None else None)



def _coerce_dimension(value: Any) -> DimensionSpec:
    if isinstance(value, DimensionSpec):
        dimension = value
    else:
        dimension = DimensionSpec(
            kind=str(_value(value, "kind", _value(value, "type", ""))),
            axis=_value(value, "axis"),
            index=_value(value, "index", _value(value, "hole_index")),
            source=_value(value, "source"),
            precision=int(_value(value, "precision", 2)),
            tolerance=_value(value, "tolerance"),
        )
    kind = str(dimension.kind).strip().lower()
    axis = str(dimension.axis).strip().lower() if dimension.axis is not None else None
    if kind not in {"overall", "hole_diameter", "hole_coordinate", "hole_spacing"}:
        raise ValueError(
            "Dimension kind must be overall, hole_diameter, hole_coordinate, or hole_spacing."
        )
    if axis is not None and axis not in {"x", "y", "z"}:
        raise ValueError("Dimension axis must be x, y, or z.")
    if dimension.precision < 0 or dimension.precision > 6:
        raise ValueError("Dimension precision must be between 0 and 6.")
    if dimension.index is not None and (
        isinstance(dimension.index, bool) or not isinstance(dimension.index, Integral) or dimension.index < 0
    ):
        raise ValueError("Hole index must be a nonnegative integer.")
    if dimension.tolerance is not None and (
        not math.isfinite(float(dimension.tolerance)) or float(dimension.tolerance) < 0
    ):
        raise ValueError("Dimension tolerance must be finite and nonnegative.")
    return DimensionSpec(
        kind=kind,
        axis=axis,
        index=int(dimension.index) if dimension.index is not None else None,
        source=str(dimension.source) if dimension.source is not None else None,
        precision=dimension.precision,
        tolerance=float(dimension.tolerance) if dimension.tolerance is not None else None,
    )


def _normalize_request(req: Any) -> _NormalizedRequest:
    if req is None:
        req = DrawingRequest()
    raw_sheet = str(_value(req, "sheet", _value(req, "sheet_size", "A4"))).strip().upper()
    if raw_sheet not in SHEET_SIZES_MM:
        raise ValueError(f"Unsupported sheet size {raw_sheet!r}.")
    style = str(_value(req, "style", "iso")).strip().lower()
    if style not in {"iso", "asme"}:
        raise ValueError("Drawing style must be iso or asme.")
    raw_views = _value(req, "views", None)
    if raw_views is None:
        raw_views = DrawingRequest().views
    elif isinstance(raw_views, (str, DrawingView, Mapping)):
        raw_views = (raw_views,)
    views = tuple(_coerce_view(view) for view in raw_views)
    if not views:
        raise ValueError("A drawing must request at least one view.")
    if len({view.name for view in views}) != len(views):
        raise ValueError("Drawing view names must be unique.")

    raw_dimensions = _value(req, "dimensions", "auto")
    if raw_dimensions is None:
        raw_dimensions = "auto"
    if isinstance(raw_dimensions, str):
        dimensions: str | tuple[DimensionSpec, ...] = raw_dimensions.strip().lower()
        if dimensions not in {"auto", "none"}:
            raise ValueError("dimensions must be auto, none, or a list of DimensionSpec values.")
    elif isinstance(raw_dimensions, (DimensionSpec, Mapping)):
        dimensions = (_coerce_dimension(raw_dimensions),)
    else:
        dimensions = tuple(_coerce_dimension(dimension) for dimension in raw_dimensions)

    raw_title_block = _value(req, "title_block", {}) or {}
    if not isinstance(raw_title_block, Mapping):
        raise ValueError("title_block must be a mapping of field names to text.")
    title_block = {str(key).strip().lower(): str(value) for key, value in raw_title_block.items()}

    raw_formats = _value(req, "formats", ("svg", "dxf", "pdf"))
    if raw_formats is None:
        raw_formats = ("svg", "dxf", "pdf")
    if isinstance(raw_formats, str):
        raw_formats = (raw_formats,)
    formats = tuple(dict.fromkeys(str(value).strip().lower() for value in raw_formats))
    if not formats:
        formats = ("svg", "dxf", "pdf")
    allowed_formats = {"svg", "dxf", "pdf", "png"}
    unknown = sorted(set(formats) - allowed_formats)
    if unknown:
        raise ValueError(f"Unsupported drawing formats: {', '.join(unknown)}.")
    return _NormalizedRequest(raw_sheet, views, dimensions, style, title_block, formats)


def _format_number(value: float, precision: int) -> str:
    rounded = round(float(value), precision)
    if precision == 0:
        return f"{rounded:.0f}"
    return f"{rounded:.{precision}f}".rstrip("0").rstrip(".")


def _source(kind: str, **details: Any) -> dict[str, Any]:
    return {"type": kind, **details}


def _dimension_record(
    *,
    kind: str,
    value: float,
    axis: str | None,
    unit: str,
    source: dict[str, Any],
    precision: int = 2,
    tolerance: float | None = None,
    count: int | None = None,
    holes: tuple[int, ...] = (),
) -> dict[str, Any]:
    numeric_value = float(value)
    label = _format_number(numeric_value, precision)
    if tolerance is not None:
        label = f"{label} +/- {_format_number(tolerance, precision)}"
    return {
        "id": f"{kind}.{axis or 'value'}.{numeric_value:.12g}.{','.join(map(str, holes))}",
        "kind": kind,
        "axis": axis,
        "value": numeric_value,
        "unit": unit,
        "precision": precision,
        "tolerance": tolerance,
        "display": label,
        "source": source,
        "hole_indices": list(holes),
        "count": count,
    }


def _overall_dimension(
    bounds: Bounds3D,
    axis: str,
    unit: str,
    precision: int = 2,
    tolerance: float | None = None,
) -> dict[str, Any]:
    axis_index = {"x": 0, "y": 1, "z": 2}[axis]
    low = (bounds.xmin, bounds.ymin, bounds.zmin)[axis_index]
    high = (bounds.xmax, bounds.ymax, bounds.zmax)[axis_index]
    return _dimension_record(
        kind="overall",
        value=high - low,
        axis=axis,
        unit=unit,
        precision=precision,
        tolerance=tolerance,
        source=_source(
            "axis_aligned_shape_bounds",
            minimum=f"shape.bounding_box.minimum.{axis}",
            maximum=f"shape.bounding_box.maximum.{axis}",
            measurement="maximum - minimum",
        ),
    )


def _hole_groups(holes: list[Hole], indices: list[int] | None = None) -> list[list[Hole]]:
    selected = [hole for hole in holes if indices is None or hole.index in indices]
    groups: list[list[Hole]] = []
    for hole in selected:
        group = next(
            (
                candidate
                for candidate in groups
                if abs(candidate[0].radius - hole.radius) <= 1e-7
            ),
            None,
        )
        if group is None:
            groups.append([hole])
        else:
            group.append(hole)
    return groups


def _hole_diameter_dimensions(
    holes: list[Hole],
    unit: str,
    precision: int,
    tolerance: float | None,
    indices: list[int] | None = None,
) -> list[dict[str, Any]]:
    result = []
    for group in _hole_groups(holes, indices):
        result.append(
            _dimension_record(
                kind="hole_diameter",
                value=2 * group[0].radius,
                axis=None,
                unit=unit,
                precision=precision,
                tolerance=tolerance,
                count=len(group),
                holes=tuple(hole.index for hole in group),
                source=_source(
                    "cylindrical_through_hole",
                    hole_indices=[hole.index for hole in group],
                    face_indices=[index for hole in group for index in hole.face_indices],
                    measurement="2 * cylinder.radius",
                ),
            )
        )
    return result


def _hole_coordinate_dimensions(
    holes: list[Hole],
    bounds: Bounds3D,
    axes: tuple[str, ...],
    unit: str,
    precision: int,
    tolerance: float | None,
    indices: list[int] | None = None,
) -> list[dict[str, Any]]:
    selected = [hole for hole in holes if indices is None or hole.index in indices]
    result: list[dict[str, Any]] = []
    for axis in axes:
        datum = bounds.xmin if axis == "x" else bounds.ymin
        grouped: list[tuple[float, list[Hole]]] = []
        for hole in selected:
            position = hole.x if axis == "x" else hole.y
            value = position - datum
            group = next(
                (
                    candidate
                    for candidate in grouped
                    if abs(candidate[0] - value) <= 1e-7
                ),
                None,
            )
            if group is None:
                grouped.append((value, [hole]))
            else:
                group[1].append(hole)
        for value, group in sorted(grouped, key=lambda item: item[0]):
            indices_for_group = tuple(hole.index for hole in group)
            result.append(
                _dimension_record(
                    kind="hole_coordinate",
                    value=value,
                    axis=axis,
                    unit=unit,
                    precision=precision,
                    tolerance=tolerance,
                    count=len(group),
                    holes=indices_for_group,
                    source=_source(
                        "hole_center_from_shape_datum",
                        hole_indices=list(indices_for_group),
                        coordinate=f"shape.cylindrical_through_holes.center.{axis}",
                        datum=f"shape.bounding_box.minimum.{axis}",
                        measurement="hole center coordinate - datum coordinate",
                    ),
                )
            )
    return result


def _hole_spacing_dimensions(
    holes: list[Hole],
    axis: str,
    unit: str,
    precision: int,
    tolerance: float | None,
    indices: list[int] | None = None,
) -> list[dict[str, Any]]:
    selected = [hole for hole in holes if indices is None or hole.index in indices]
    coordinates: dict[float, list[int]] = {}
    for hole in selected:
        position = hole.x if axis == "x" else hole.y
        key = round(position, 7)
        coordinates.setdefault(key, []).append(hole.index)
    ordered = sorted(coordinates)
    result = []
    for index, (start, end) in enumerate(zip(ordered, ordered[1:])):
        left_holes, right_holes = coordinates[start], coordinates[end]
        result.append(
            _dimension_record(
                kind="hole_spacing",
                value=end - start,
                axis=axis,
                unit=unit,
                precision=precision,
                tolerance=tolerance,
                holes=tuple(left_holes + right_holes),
                source=_source(
                    "adjacent_hole_center_coordinates",
                    hole_indices=left_holes + right_holes,
                    from_coordinate=start,
                    to_coordinate=end,
                    measurement="absolute coordinate difference",
                ),
            )
        )
    return result


def _indices_from_source(source: str | None) -> list[int] | None:
    if not source:
        return None
    match = re.search(r"holes?\[(\d+)\]", source, flags=re.IGNORECASE)
    if match:
        return [int(match.group(1))]
    if source not in {"bbox.x", "bbox.y", "bbox.z"}:
        raise ValueError(f"Unsupported dimension source {source!r}.")
    return None


def _make_dimensions(
    dimension_request: str | tuple[DimensionSpec, ...],
    bounds: Bounds3D,
    holes: list[Hole],
    unit: str,
) -> list[dict[str, Any]]:
    if dimension_request == "none":
        return []

    if dimension_request == "auto":
        result = [
            _overall_dimension(bounds, axis, unit)
            for axis in ("x", "y", "z")
            if bounds.size[{"x": 0, "y": 1, "z": 2}[axis]] > 1e-9
        ]
        if holes:
            result.extend(_hole_diameter_dimensions(holes, unit, 2, None))
            result.extend(_hole_coordinate_dimensions(holes, bounds, ("x", "y"), unit, 2, None))
        return result

    result: list[dict[str, Any]] = []
    for spec in dimension_request:
        indices = _indices_from_source(spec.source)
        if spec.index is not None:
            indices = [spec.index]
        if indices is not None and any(i >= len(holes) for i in indices):
            raise ValueError(f"Hole selection {indices} is empty; available hole indices are 0..{len(holes)-1}.")
        before = len(result)
        source_axis = spec.source[-1] if spec.source and spec.source.startswith("bbox.") else None
        axis = spec.axis or source_axis
        axes = (axis,) if axis else ("x", "y", "z")
        if spec.kind == "overall":
            result.extend(
                _overall_dimension(bounds, axis, unit, spec.precision, spec.tolerance)
                for axis in axes
            )
        elif spec.kind == "hole_diameter":
            result.extend(
                _hole_diameter_dimensions(holes, unit, spec.precision, spec.tolerance, indices)
            )
        elif spec.kind == "hole_coordinate":
            coordinate_axes = tuple(axis for axis in axes if axis in {"x", "y"})
            result.extend(
                _hole_coordinate_dimensions(
                    holes,
                    bounds,
                    coordinate_axes,
                    unit,
                    spec.precision,
                    spec.tolerance,
                    indices,
                )
            )
        elif spec.kind == "hole_spacing":
            spacing_axes = tuple(axis for axis in axes if axis in {"x", "y"})
            result.extend(
                dimension
                for axis in spacing_axes
                for dimension in _hole_spacing_dimensions(
                    holes, axis, unit, spec.precision, spec.tolerance, indices
                )
            )
        if len(result) == before:
            raise ValueError(f"Dimension request {spec.kind!r} has no matching geometry.")
    return result


def _layout_centers(
    sheet_width: float,
    sheet_height: float,
    style: str,
    names: tuple[str, ...],
) -> dict[str, tuple[float, float]]:
    centers = {
        "front": (sheet_width * 0.49, sheet_height * 0.54),
        "top": (
            sheet_width * 0.49,
            sheet_height * (0.76 if style == "asme" else 0.24),
        ),
        "right": (
            sheet_width * (0.78 if style == "asme" else 0.22),
            sheet_height * 0.54,
        ),
        "iso": (
            sheet_width * (0.19 if style == "asme" else 0.78),
            sheet_height * 0.76,
        ),
        "left": (
            sheet_width * (0.78 if style == "asme" else 0.22),
            sheet_height * 0.54,
        ),
        "back": (sheet_width * 0.49, sheet_height * 0.54),
        "bottom": (
            sheet_width * 0.49,
            sheet_height * (0.24 if style == "asme" else 0.76),
        ),
    }
    generic = [
        (sheet_width * 0.28, sheet_height * 0.75),
        (sheet_width * 0.72, sheet_height * 0.75),
        (sheet_width * 0.28, sheet_height * 0.28),
        (sheet_width * 0.72, sheet_height * 0.28),
    ]
    for index, name in enumerate(names):
        if name not in centers:
            centers[name] = generic[index % len(generic)]
    return centers


def _layout_padding(
    name: str,
    bounds: ProjectedBounds,
    unit_factor: float,
) -> tuple[float, float]:
    if name == "front":
        return bounds.width * unit_factor + 28.0, bounds.height * unit_factor + 22.0
    if name == "top":
        return bounds.width * unit_factor + 30.0, bounds.height * unit_factor + 25.0
    if name in {"right", "left", "back", "bottom"}:
        return bounds.width * unit_factor + 14.0, bounds.height * unit_factor + 14.0
    return bounds.width * unit_factor + 16.0, bounds.height * unit_factor + 16.0


def _choose_scale(
    projections: tuple[ProjectedView, ...],
    sheet_width: float,
    sheet_height: float,
    unit_factor: float,
) -> float:
    capacities = []
    for projection in projections:
        design_width, design_height = _layout_padding(
            projection.name,
            projection.bounds,
            unit_factor,
        )
        if projection.name in {"front", "top"}:
            cell_width = sheet_width * 0.41
        elif projection.name in {"right", "left"}:
            cell_width = sheet_width * 0.27
        else:
            cell_width = sheet_width * 0.37
        if projection.name == "front":
            cell_height = sheet_height * 0.25
        elif projection.name == "top":
            cell_height = sheet_height * 0.40
        elif projection.name in {"right", "left"}:
            cell_height = sheet_height * 0.22
        else:
            cell_height = sheet_height * 0.37
        capacities.extend(
            [
                cell_width / max(design_width, 1e-9),
                cell_height / max(design_height, 1e-9),
            ]
        )

    maximum = min(capacities) if capacities else 1.0
    preferred = (10.0, 5.0, 2.0, 1.0, 0.75, 0.5, 0.4, 1 / 3, 0.25, 0.2, 1 / 6, 0.125, 0.1, 0.05)
    for scale in preferred:
        if scale <= maximum + 1e-9:
            return scale
    return max(maximum, 1e-4)


def _scale_label(scale: float) -> str:
    if scale >= 1:
        return f"{scale:g}:1"
    return f"1:{1 / scale:g}"


def _place_views(
    projections: tuple[ProjectedView, ...],
    sheet_width: float,
    sheet_height: float,
    style: str,
    scale: float,
    unit_factor: float,
) -> dict[str, _PlacedView]:
    centers = _layout_centers(
        sheet_width,
        sheet_height,
        style,
        tuple(projection.name for projection in projections),
    )
    placed = {}
    for projection in projections:
        center = centers[projection.name]
        projected_center = projection.bounds.center
        factor = scale * unit_factor
        half_width = projection.bounds.width * factor / 2
        half_height = projection.bounds.height * factor / 2
        bounds = ProjectedBounds(
            center[0] - half_width,
            center[1] - half_height,
            center[0] + half_width,
            center[1] + half_height,
        )
        placed[projection.name] = _PlacedView(projection, center, bounds, factor)
    return placed


def _sheet_point(
    placed: _PlacedView,
    local: tuple[float, float],
) -> tuple[float, float]:
    local_center = placed.projection.bounds.center
    return (
        placed.center[0] + (local[0] - local_center[0]) * placed.factor,
        placed.center[1] + (local[1] - local_center[1]) * placed.factor,
    )


def _emit_view_geometry(entities: list[Primitive], placed: _PlacedView) -> None:
    projection = placed.projection
    hatches = set(projection.metadata.get("hatch_curves", ()))
    for curve in projection.curves:
        layer = "HATCH" if curve in hatches else "HIDDEN" if curve.hidden else "VISIBLE"
        dash = curve.hidden
        if curve.kind == "line":
            points = [_sheet_point(placed, point) for point in curve.points]
            entities.append(line(*points[0], *points[1], layer=layer, width=0.12 if layer == "HATCH" else 0.22 if not dash else 0.16, dash=dash))
        elif curve.kind == "polyline":
            points = [_sheet_point(placed, point) for point in curve.points]
            entities.append(polyline(points, layer=layer, width=0.18 if not dash else 0.14, dash=dash))
        elif curve.kind == "circle":
            center = _sheet_point(placed, curve.center)
            entities.append(
                circle(center, curve.radius * placed.factor, layer, 0.22 if not dash else 0.16, dash)
            )
        elif curve.kind == "arc":
            center = _sheet_point(placed, curve.center)
            entities.append(
                arc(
                    center,
                    curve.radius * placed.factor,
                    curve.start_angle,
                    curve.end_angle,
                    curve.ccw,
                    layer,
                    0.22 if not dash else 0.16,
                    dash,
                )
            )


def _dimension_line(
    entities: list[Primitive],
    start: tuple[float, float],
    end: tuple[float, float],
    style: str,
    gap_center: tuple[float, float] | None = None,
    gap_half_width: float = 0.0,
) -> None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    angle = math.atan2(dy, dx)
    if gap_center is None or gap_half_width <= 0:
        entities.append(line(*start, *end, layer="DIMENSIONS", width=0.16))
    elif abs(dx) >= abs(dy):
        entities.append(line(start[0], start[1], gap_center[0] - gap_half_width, start[1], "DIMENSIONS", 0.16))
        entities.append(line(gap_center[0] + gap_half_width, end[1], end[0], end[1], "DIMENSIONS", 0.16))
    else:
        entities.append(line(start[0], start[1], start[0], gap_center[1] - gap_half_width, "DIMENSIONS", 0.16))
        entities.append(line(end[0], gap_center[1] + gap_half_width, end[0], end[1], "DIMENSIONS", 0.16))
    arrow = 1.8 if style == "iso" else 1.5
    for tip, inward_angle in ((start, angle), (end, angle + math.pi)):
        base = (
            tip[0] - arrow * math.cos(inward_angle),
            tip[1] - arrow * math.sin(inward_angle),
        )
        normal = (-math.sin(inward_angle) * 0.48, math.cos(inward_angle) * 0.48)
        entities.append(
            line(
                tip[0],
                tip[1],
                base[0] + normal[0],
                base[1] + normal[1],
                layer="DIMENSIONS",
                width=0.16,
            )
        )
        entities.append(
            line(
                tip[0],
                tip[1],
                base[0] - normal[0],
                base[1] - normal[1],
                layer="DIMENSIONS",
                width=0.16,
            )
        )
        if style == "iso":
            entities.append(
                line(
                    base[0] + normal[0],
                    base[1] + normal[1],
                    base[0] - normal[0],
                    base[1] - normal[1],
                    layer="DIMENSIONS",
                    width=0.12,
                )
            )


def _draw_overall_dimensions(
    entities: list[Primitive],
    placed: dict[str, _PlacedView],
    dimensions: list[dict[str, Any]],
    style: str,
) -> None:
    by_axis = {
        dimension["axis"]: dimension
        for dimension in dimensions
        if dimension["kind"] == "overall"
    }
    front = placed.get("front")
    top = placed.get("top")
    if front and "x" in by_axis:
        bounds = front.bounds
        y_dim = bounds.ymin - 12.0
        entities.append(line(bounds.xmin, bounds.ymin, bounds.xmin, y_dim - 1, "DIMENSIONS", 0.12))
        entities.append(line(bounds.xmax, bounds.ymin, bounds.xmax, y_dim - 1, "DIMENSIONS", 0.12))
        label = by_axis["x"]["display"]
        gap = max(4.0, len(label) * 1.8)
        center = (bounds.xmin + bounds.xmax) / 2
        _dimension_line(
            entities,
            (bounds.xmin, y_dim),
            (bounds.xmax, y_dim),
            style,
            (center, y_dim),
            gap / 2 + 1.2,
        )
        entities.append(text(label, center, y_dim + 1.4, 2.8, "DIMENSIONS", "center", dimension_id=by_axis["x"]["id"]))

    if front and "z" in by_axis:
        bounds = front.bounds
        x_dim = bounds.xmax + 13.0
        entities.append(line(bounds.xmax, bounds.ymin, x_dim + 1.0, bounds.ymin, "DIMENSIONS", 0.12))
        entities.append(line(bounds.xmax, bounds.ymax, x_dim + 1.0, bounds.ymax, "DIMENSIONS", 0.12))
        _dimension_line(entities, (x_dim, bounds.ymin), (x_dim, bounds.ymax), style)
        entities.append(text(by_axis["z"]["display"], x_dim + 2.2, (bounds.ymin + bounds.ymax) / 2 - 1, 2.8, "DIMENSIONS", dimension_id=by_axis["z"]["id"]))

    if top and "y" in by_axis:
        bounds = top.bounds
        x_dim = bounds.xmax + 14.0
        entities.append(line(bounds.xmax, bounds.ymin, x_dim + 1.0, bounds.ymin, "DIMENSIONS", 0.12))
        entities.append(line(bounds.xmax, bounds.ymax, x_dim + 1.0, bounds.ymax, "DIMENSIONS", 0.12))
        _dimension_line(entities, (x_dim, bounds.ymin), (x_dim, bounds.ymax), style)
        entities.append(
            text(
                by_axis["y"]["display"],
                x_dim + 2.2,
                (bounds.ymin + bounds.ymax) / 2 - 1,
                2.8,
                "DIMENSIONS",
                dimension_id=by_axis["y"]["id"],
            )
        )


def _draw_hole_dimensions(
    entities: list[Primitive],
    placed: dict[str, _PlacedView],
    dimensions: list[dict[str, Any]],
    holes: list[Hole],
    bounds: Bounds3D,
    style: str,
) -> None:
    top = placed.get("top")
    if not top:
        return
    diameters = [d for d in dimensions if d["kind"] == "hole_diameter"]
    for index, diameter in enumerate(diameters):
        selected = [h for h in holes if h.index in diameter["hole_indices"]]
        hole = selected[0]
        point = _sheet_point(top, project_point((hole.x, hole.y, 0), top.projection))
        count = diameter.get("count") or 1
        prefix = f"{count}X " if count > 1 else ""
        label_x = top.bounds.xmax + 25
        label_y = top.bounds.ymax - 5 - 8 * index
        entities.append(line(point[0], point[1], label_x - 2, label_y, "DIMENSIONS", 0.12))
        entities.append(text(f"{prefix}DIA {diameter['display']} THRU", label_x, label_y, 2.8,
                             "DIMENSIONS", dimension_id=diameter["id"]))

    top_view = top.projection
    left, bottom = top.bounds.xmin, top.bounds.ymin

    x_dimensions = sorted(
        (dimension for dimension in dimensions if dimension["kind"] == "hole_coordinate" and dimension["axis"] == "x"),
        key=lambda dimension: dimension["value"],
    )
    y_dimensions = sorted(
        (dimension for dimension in dimensions if dimension["kind"] == "hole_coordinate" and dimension["axis"] == "y"),
        key=lambda dimension: dimension["value"],
    )

    if x_dimensions:
        baseline_y = bottom - 8.0
        max_x = top.bounds.xmin
        for dimension in x_dimensions:
            hole_indices = dimension["hole_indices"]
            group_holes = [hole for hole in holes if hole.index in hole_indices]
            if not group_holes:
                continue
            local_x = sum(hole.x for hole in group_holes) / len(group_holes)
            station_x = _sheet_point(top, project_point((local_x, bounds.ymin, 0.0), top_view))[0]
            max_x = max(max_x, station_x)
            entities.append(line(station_x, bottom, station_x, baseline_y - 1, "DIMENSIONS", 0.12))
            entities.append(line(station_x, baseline_y - 1, station_x, baseline_y + 1, "DIMENSIONS", 0.16))
            entities.append(
                text(
                    f"X{dimension['display']}",
                    station_x,
                    baseline_y - 3.2,
                    2.6,
                    "DIMENSIONS",
                    "center",
                    dimension_id=dimension["id"],
                )
            )
        entities.append(line(top.bounds.xmin, baseline_y, max_x, baseline_y, "DIMENSIONS", 0.16))
        entities.append(circle((top.bounds.xmin, baseline_y), 0.6, "DIMENSIONS", 0.16))

    if y_dimensions:
        baseline_x = left - 8.0
        max_y = top.bounds.ymin
        for dimension in y_dimensions:
            hole_indices = dimension["hole_indices"]
            group_holes = [hole for hole in holes if hole.index in hole_indices]
            if not group_holes:
                continue
            local_y = sum(hole.y for hole in group_holes) / len(group_holes)
            station_y = _sheet_point(top, project_point((bounds.xmin, local_y, 0.0), top_view))[1]
            max_y = max(max_y, station_y)
            entities.append(line(left, station_y, baseline_x - 1, station_y, "DIMENSIONS", 0.12))
            entities.append(line(baseline_x - 1, station_y, baseline_x + 1, station_y, "DIMENSIONS", 0.16))
            entities.append(
                text(
                    f"Y{dimension['display']}",
                    baseline_x - 2.2,
                    station_y - 1,
                    2.6,
                    "DIMENSIONS",
                    "right",
                    dimension_id=dimension["id"],
                )
            )
        entities.append(line(baseline_x, top.bounds.ymin, baseline_x, max_y, "DIMENSIONS", 0.16))
        entities.append(circle((baseline_x, top.bounds.ymin), 0.6, "DIMENSIONS", 0.16))

    spacings = [d for d in dimensions if d["kind"] == "hole_spacing"]
    for index, dimension in enumerate(spacings):
        axis = dimension["axis"]
        source = dimension["source"]
        first = source["from_coordinate"]
        last = source["to_coordinate"]
        if axis == "x":
            a = _sheet_point(top, project_point((first, bounds.ymin, 0), top_view))
            b = _sheet_point(top, project_point((last, bounds.ymin, 0), top_view))
            station = bottom - (18 if x_dimensions else 8) - 8 * index
            entities.extend([line(a[0], bottom, a[0], station-1, "DIMENSIONS"),
                             line(b[0], bottom, b[0], station-1, "DIMENSIONS")])
            _dimension_line(entities, (a[0],station), (b[0],station), style)
            entities.append(text(dimension["display"], (a[0]+b[0])/2, station+1.5, 2.8,
                                 "DIMENSIONS", "center", dimension_id=dimension["id"]))
        else:
            a = _sheet_point(top, project_point((bounds.xmin, first, 0), top_view))
            b = _sheet_point(top, project_point((bounds.xmin, last, 0), top_view))
            station = left - (20 if y_dimensions else 8) - 8 * index
            entities.extend([line(left, a[1], station-1, a[1], "DIMENSIONS"),
                             line(left, b[1], station-1, b[1], "DIMENSIONS")])
            _dimension_line(entities, (station,a[1]), (station,b[1]), style)
            entities.append(text(dimension["display"], station-2, (a[1]+b[1])/2, 2.8,
                                 "DIMENSIONS", "right", dimension_id=dimension["id"]))


def _title_block_entities(
    entities: list[Primitive],
    width: float,
    height: float,
    request: _NormalizedRequest,
    context: Any,
    scale: float,
    units: str,
) -> tuple[float, float, float, float]:
    block_width = min(TITLE_BLOCK_WIDTH_MM, width * 0.34)
    block_height = TITLE_BLOCK_HEIGHT_MM
    x0 = width - MARGIN_MM - block_width
    y0 = MARGIN_MM
    x1, y1 = x0 + block_width, y0 + block_height

    default_title = (
        context_value(context, "name")
        or context_value(context, "project")
        or (Path(context_value(context, "project_dir")).name if context_value(context, "project_dir") else None)
        or "UNTITLED PART"
    )
    fields = {
        "title": default_title,
        "drawing_no": "AIAD-S4-001",
        "revision": "A",
        "units": units,
        "sheet": request.sheet,
        "style": request.style.upper(),
    }
    fields.update(request.title_block)

    entities.extend(
        [
            line(x0, y0, x1, y0, "TITLE", 0.22),
            line(x1, y0, x1, y1, "TITLE", 0.22),
            line(x1, y1, x0, y1, "TITLE", 0.22),
            line(x0, y1, x0, y0, "TITLE", 0.22),
            line(x0, y0 + 9, x1, y0 + 9, "TITLE", 0.16),
            line(x0, y0 + 18, x1, y0 + 18, "TITLE", 0.16),
            line(x0 + block_width * 0.56, y0, x0 + block_width * 0.56, y0 + 18, "TITLE", 0.16),
        ]
    )
    entities.append(text(fields["title"][:48], x0 + 2, y0 + 20, 3.0, "TITLE"))
    entities.append(text(f"NO. {fields['drawing_no']}", x0 + 2, y0 + 11.5, 2.4, "TITLE"))
    entities.append(text(f"REV {fields['revision']}", x0 + block_width * 0.56 + 2, y0 + 11.5, 2.4, "TITLE"))
    entities.append(text(f"SCALE {_scale_label(scale)}", x0 + 2, y0 + 2.5, 2.4, "TITLE"))
    entities.append(
        text(
            f"{fields['units'].upper()}  {fields['sheet'].upper()}  {fields['style'].upper()}",
            x0 + block_width * 0.56 + 2,
            y0 + 2.5,
            2.4,
            "TITLE",
        )
    )
    return (x0, y0, x1, y1)


def _sheet_frame(entities: list[Primitive], width: float, height: float) -> None:
    x0 = MARGIN_MM
    y0 = MARGIN_MM
    x1 = width - MARGIN_MM
    y1 = height - MARGIN_MM
    entities.extend(
        [
            line(x0, y0, x1, y0, "BORDER", 0.3),
            line(x1, y0, x1, y1, "BORDER", 0.3),
            line(x1, y1, x0, y1, "BORDER", 0.3),
            line(x0, y1, x0, y0, "BORDER", 0.3),
        ]
    )


def _view_label_entities(
    entities: list[Primitive],
    placed: dict[str, _PlacedView],
    request: _NormalizedRequest,
) -> None:
    labels = {view.name: view.label for view in request.views}
    for name, item in placed.items():
        label = labels.get(name) or name.upper()
        if item.projection.metadata.get("kind") in {"section", "detail"}:
            magnification = item.projection.metadata.get("scale", 1.0)
            label += f"  x{magnification:g} SHEET SCALE"
        entities.append(
            text(
                str(label).upper(),
                item.center[0],
                item.bounds.ymax + 5.0,
                2.6,
                "ANNOTATION",
                "center",
            )
        )


def _view_markers(entities, shape, placed, request):
    """Locate section cuts and detail crops on the actual parent projection."""
    from .views import section_plane_trace
    for spec in request.views:
        if spec.kind == "section":
            trace = section_plane_trace(shape, spec)
            parent = placed.get(trace.parent)
            if parent is None or trace.curve is None:
                continue
            start, end = [_sheet_point(parent, p) for p in trace.curve.points]
            entities.append(line(*start, *end, layer="SECTION_MARKERS", width=0.3, dash=True))
            dx, dy = end[0]-start[0], end[1]-start[1]
            length = math.hypot(dx,dy)
            nx, ny = -dy/length, dx/length
            for x,y in (start,end):
                entities.append(line(x,y,x+3*nx,y+3*ny,layer="SECTION_MARKERS",width=0.3))
            entities.append(text(spec.label or spec.name.upper(), start[0]-3, start[1]+4,
                                 2.6, "SECTION_MARKERS", "right"))
        elif spec.kind == "detail":
            parent = placed.get(spec.parent or "front")
            if parent is None:
                continue
            center = _sheet_point(parent, spec.center)
            radius = float(spec.radius)*parent.factor
            entities.append(circle(center,radius,"DETAIL_MARKERS",0.2,True))
            entities.append(text(spec.label or spec.name.upper(),center[0]+radius+2,center[1]+radius,
                                 2.6,"DETAIL_MARKERS"))


def _entity_text_bounds(entity: Primitive) -> tuple[float, float, float, float] | None:
    if entity.kind != "text":
        return None
    x, y = entity.points[0]
    width = max(len(entity.text or ""), 1) * entity.height * 0.54
    if entity.align == "center":
        xmin, xmax = x - width / 2, x + width / 2
    elif entity.align == "right":
        xmin, xmax = x - width, x
    else:
        xmin, xmax = x, x + width
    return (xmin, y - entity.height * 0.35, xmax, y + entity.height)


def _drawing_checks(
    entities: list[Primitive],
    dimensions: list[dict[str, Any]],
    placed: dict[str, _PlacedView],
    sheet_width: float,
    sheet_height: float,
    title_bounds: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    checks = []

    def add_check(check_id: str, status: str, message: str, actual: str | None = None) -> None:
        checks.append(
            {
                "id": check_id,
                "suite": "2d",
                "level": "error" if status == "fail" else "warn" if status == "warn" else "error",
                "status": status,
                "message": message,
                "actual": actual,
            }
        )

    escaped = [
        name
        for name, view in placed.items()
        if view.bounds.xmin < MARGIN_MM - 1e-6
        or view.bounds.ymin < MARGIN_MM - 1e-6
        or view.bounds.xmax > sheet_width - MARGIN_MM + 1e-6
        or view.bounds.ymax > sheet_height - MARGIN_MM + 1e-6
    ]
    if escaped:
        add_check(
            "sheet_margins",
            "fail",
            "One or more projected views extend beyond the drawing frame.",
            ", ".join(escaped),
        )
    else:
        add_check("sheet_margins", "pass", "Projected views fit inside the sheet frame.")

    x0, y0, x1, y1 = title_bounds
    title_ok = (
        x0 >= MARGIN_MM
        and y0 >= MARGIN_MM
        and x1 <= sheet_width - MARGIN_MM
        and y1 <= sheet_height - MARGIN_MM
    )
    add_check(
        "title_block_bounds",
        "pass" if title_ok else "fail",
        "Title block fits inside the drawing frame." if title_ok else "Title block exceeds the drawing frame.",
    )

    displayed_ids = {e.dimension_id for e in entities if e.kind == "text" and e.dimension_id}
    missing = [d["id"] for d in dimensions if d["id"] not in displayed_ids]
    add_check("dimension_coverage", "fail" if missing else "pass",
              "Requested dimensions missing from the sheet." if missing else "Every reported dimension appears on the sheet.",
              ", ".join(missing) if missing else None)

    source_ok = all(
        isinstance(dimension.get("source"), dict)
        and math.isfinite(float(dimension.get("value", float("nan"))))
        for dimension in dimensions
    )
    add_check(
        "dimension_sources",
        "pass" if source_ok else "fail",
        "Every displayed dimension has a numeric value measured from model geometry."
        if source_ok
        else "A dimension is missing its geometry source or numeric value.",
    )

    text_bounds = [
        bounds
        for entity in entities
        if (bounds := _entity_text_bounds(entity)) is not None
    ]
    text_outside = [
        bounds
        for bounds in text_bounds
        if bounds[0] < MARGIN_MM - 1e-6
        or bounds[1] < MARGIN_MM - 1e-6
        or bounds[2] > sheet_width - MARGIN_MM + 1e-6
        or bounds[3] > sheet_height - MARGIN_MM + 1e-6
    ]
    add_check(
        "text_margins",
        "fail" if text_outside else "pass",
        "Drawing text fits inside the sheet frame."
        if not text_outside
        else "One or more text labels extend beyond the sheet frame.",
        str(len(text_outside)) if text_outside else None,
    )

    # Flag text collisions that can be resolved without interpreting geometry.
    # Intentional hole callouts inside the top view are excluded from this check.
    text_items = [
        entity
        for entity in entities
        if entity.kind == "text"
        and entity.layer in {"DIMENSIONS", "TITLE"}
    ]
    overlaps = 0
    for index, first in enumerate(text_items):
        first_bounds = _entity_text_bounds(first)
        for second in text_items[index + 1 :]:
            if first.layer == "TITLE" and second.layer == "TITLE":
                continue
            second_bounds = _entity_text_bounds(second)
            if (
                first_bounds[0] < second_bounds[2]
                and second_bounds[0] < first_bounds[2]
                and first_bounds[1] < second_bounds[3]
                and second_bounds[1] < first_bounds[3]
            ):
                overlaps += 1
    add_check(
        "dimension_text_collisions",
        "fail" if overlaps else "pass",
        "Dimension and title text labels do not overlap."
        if not overlaps
        else "Dimension or title text labels overlap.",
        str(overlaps) if overlaps else None,
    )
    return checks


def make_drawing(ctx: Any, req: Any, out: str | Path) -> DrawingResult:
    """Project a structural model context onto a first dimensioned sheet."""
    request = _normalize_request(req)
    shape = context_shape(ctx)
    units = context_units(ctx)
    bounds = shape_bounds(shape)
    if min(bounds.size) < -1e-9 or max(bounds.size) <= 1e-9:
        raise ValueError("The supplied shape has an empty or invalid bounding box.")
    holes = through_holes(shape, bounds)
    dimensions = _make_dimensions(request.dimensions, bounds, holes, units)

    from .views import project_drawing_view
    projections = tuple(project_drawing_view(shape, view) for view in request.views)
    page_width, page_height = SHEET_SIZES_MM[request.sheet]
    unit_factor = UNIT_TO_MM[units]
    scale = _choose_scale(projections, page_width, page_height, unit_factor)
    placed = _place_views(
        projections,
        page_width,
        page_height,
        request.style,
        scale,
        unit_factor,
    )

    entities: list[Primitive] = []
    _sheet_frame(entities, page_width, page_height)
    for view in placed.values():
        _emit_view_geometry(entities, view)
    _view_label_entities(entities, placed, request)
    _view_markers(entities, shape, placed, request)
    _draw_overall_dimensions(entities, placed, dimensions, request.style)
    _draw_hole_dimensions(entities, placed, dimensions, holes, bounds, request.style)
    title_bounds = _title_block_entities(
        entities,
        page_width,
        page_height,
        request,
        ctx,
        scale,
        units,
    )
    checks = _drawing_checks(
        entities,
        dimensions,
        placed,
        page_width,
        page_height,
        title_bounds,
    )

    output_dir = Path(out)
    output_dir.mkdir(parents=True, exist_ok=True)
    preview = output_dir / "drawing.png"
    artifacts: dict[str, Path] = {}
    writers = {
        "svg": ("drawing.svg", "write_svg"),
        "dxf": ("drawing.dxf", "write_dxf"),
        "pdf": ("drawing.pdf", "write_pdf"),
    }
    from . import export as export_module

    for format_name in request.formats:
        if format_name == "png":
            artifacts["png"] = preview
            continue
        filename, writer_name = writers[format_name]
        file_path = output_dir / filename
        writer = getattr(export_module, writer_name)
        writer(file_path, page_width, page_height, entities)
        artifacts[format_name] = file_path
    export_module.write_png(preview, page_width, page_height, entities)

    view_results = [
        {
            "name": name,
            "direction": list(view.projection.direction),
            "center_mm": list(view.center),
            "bounds_mm": [
                view.bounds.xmin,
                view.bounds.ymin,
                view.bounds.xmax,
                view.bounds.ymax,
            ],
            "visible_edges": view.projection.visible_count,
            "hidden_edges": view.projection.hidden_count,
            "kind": view.projection.metadata.get("kind", "orthographic"),
            "scale_multiplier": view.projection.metadata.get("scale", 1.0),
            "plane": view.projection.metadata.get("plane"),
            "offset": view.projection.metadata.get("offset"),
        }
        for name, view in placed.items()
    ]
    all_passed = all(check["status"] in {"pass", "warn"} for check in checks)
    scale_text = _scale_label(scale)
    format_summary = ", ".join(
        (*[format_name for format_name in request.formats if format_name != "png"], "png preview")
    )
    summary = (
        f"{request.sheet} {request.style.upper()} sheet at {scale_text}; "
        f"{len(placed)} views, {len(dimensions)} geometry-derived dimensions; "
        f"{format_summary}."
    )
    return DrawingResult(
        artifacts=artifacts,
        preview=preview,
        dimensions=dimensions,
        checks=checks,
        sheet={
            "name": request.sheet,
            "width_mm": page_width,
            "height_mm": page_height,
            "orientation": "landscape",
            "units": units,
            "scale": scale,
            "scale_label": scale_text,
            "margin_mm": MARGIN_MM,
            "title_block_bounds_mm": list(title_bounds),
            "hole_count": len(holes),
        },
        views=view_results,
        ok=all_passed,
        summary=summary,
    )
