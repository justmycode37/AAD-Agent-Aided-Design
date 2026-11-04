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
from .layout import (
    AuxiliaryLayout,
    MARGIN_MM,
    PackedViews,
    Rect,
    bounds_collide,
    bom_from_context,
    compose_auxiliary_layout,
    drawing_geometry_margin_overflow,
    hole_callout_text,
    pack_views,
    primitive_intersects_rect,
    text_bounds,
    text_margin_overflow,
    text_text_collisions,
    text_view_collisions,
    text_width_mm,
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
DIMENSION_TEXT_HEIGHT_MM = 3.0


@dataclass(frozen=True)
class _NormalizedRequest:
    sheet: str
    views: tuple[DrawingView, ...]
    dimensions: str | tuple[DimensionSpec, ...]
    style: str
    title_block: dict[str, str]
    notes: tuple[str, ...]
    revisions: tuple[dict[str, str], ...]
    bom: bool
    formats: tuple[str, ...]


@dataclass(frozen=True)
class _PlacedView:
    projection: ProjectedView
    center: tuple[float, float]
    bounds: ProjectedBounds
    factor: float
    cell: Rect


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
    kind = str(view.kind).strip().lower()
    if kind not in {"orthographic", "section", "detail"}:
        raise ValueError("Drawing view kind must be orthographic, section, or detail.")
    parent = str(view.parent).strip().lower() if view.parent is not None else None
    parent = {"isometric":"iso"}.get(parent,parent)
    view = replace(view, kind=kind, parent=parent)
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
    reserved_title_fields = {"units", "sheet", "scale", "style", "projection"}
    conflicting_title_fields = sorted(reserved_title_fields.intersection(title_block))
    if conflicting_title_fields:
        raise ValueError(
            "These title block fields are measured from the request and cannot be overridden: "
            + ", ".join(conflicting_title_fields)
            + "."
        )

    raw_notes = _value(req, "notes", ()) or ()
    if isinstance(raw_notes, str):
        raw_notes = (raw_notes,)
    if isinstance(raw_notes, Mapping) or not isinstance(raw_notes, Sequence):
        raise ValueError("notes must be a string or a sequence of strings.")
    if any(not isinstance(note, str) for note in raw_notes):
        raise ValueError("Every drawing note must be text.")
    notes = tuple(raw_notes)

    raw_revisions = _value(req, "revisions", ()) or ()
    if isinstance(raw_revisions, Mapping):
        raw_revisions = (raw_revisions,)
    if isinstance(raw_revisions, (str, bytes)) or not isinstance(raw_revisions, Sequence):
        raise ValueError("revisions must be a sequence of revision mappings.")
    revisions: list[dict[str, str]] = []
    allowed_revision_fields = {"revision", "rev", "date", "description", "by", "author"}
    for row in raw_revisions:
        if not isinstance(row, Mapping):
            raise ValueError("Each revision entry must be a mapping.")
        normalized = {str(key).strip().lower(): str(value) for key, value in row.items()}
        unknown_revision_fields = sorted(set(normalized) - allowed_revision_fields)
        if unknown_revision_fields:
            raise ValueError(
                "Unsupported revision fields: " + ", ".join(unknown_revision_fields) + "."
            )
        if "revision" not in normalized and "rev" in normalized:
            normalized["revision"] = normalized.pop("rev")
        if "by" not in normalized and "author" in normalized:
            normalized["by"] = normalized.pop("author")
        else:
            normalized.pop("author", None)
        revisions.append(
            {key: normalized.get(key, "") for key in ("revision", "date", "description", "by")}
        )

    raw_bom = _value(req, "bom", False)
    if raw_bom in (False, None):
        bom = False
    elif raw_bom is True or (isinstance(raw_bom, str) and raw_bom.strip().lower() == "auto"):
        bom = True
    else:
        raise ValueError("bom must be true, false, or 'auto'; BOM rows and quantities come from assembly children.")

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
    return _NormalizedRequest(
        raw_sheet,
        views,
        dimensions,
        style,
        title_block,
        notes,
        tuple(revisions),
        bom,
        formats,
    )


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


def _scale_label(scale: float) -> str:
    if scale >= 1:
        return f"{scale:g}:1"
    return f"1:{1 / scale:g}"


def _place_views(
    projections: tuple[ProjectedView, ...],
    plan: PackedViews,
) -> dict[str, _PlacedView]:
    return {
        projection.name: _PlacedView(
            projection=projection,
            center=placement.center,
            bounds=placement.bounds,
            factor=placement.factor,
            cell=placement.cell,
        )
        for projection in projections
        if (placement := plan.placements[projection.name]) is not None
    }


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
        gap_half = text_width_mm(label, 2.8) / 2 + 1.2
        center = (bounds.xmin + bounds.xmax) / 2
        _dimension_line(
            entities,
            (bounds.xmin, y_dim),
            (bounds.xmax, y_dim),
            style,
            (center, y_dim) if bounds.width >= 2 * gap_half + 2.0 else None,
            gap_half,
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
        has_y_ordinates = any(
            dimension["kind"] == "hole_coordinate" and dimension["axis"] == "y"
            or dimension["kind"] == "hole_spacing" and dimension["axis"] == "y"
            for dimension in dimensions
        )
        if has_y_ordinates:
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
        else:
            x_dim = bounds.xmin - 14.0
            entities.append(line(bounds.xmin, bounds.ymin, x_dim - 1.0, bounds.ymin, "DIMENSIONS", 0.12))
            entities.append(line(bounds.xmin, bounds.ymax, x_dim - 1.0, bounds.ymax, "DIMENSIONS", 0.12))
            _dimension_line(entities, (x_dim, bounds.ymin), (x_dim, bounds.ymax), style)
            entities.append(
                text(
                    by_axis["y"]["display"],
                    x_dim - 2.2,
                    (bounds.ymin + bounds.ymax) / 2 - 1,
                    2.8,
                    "DIMENSIONS",
                    "right",
                    dimension_id=by_axis["y"]["id"],
                )
            )


def _arrowed_hole_leader(
    entities: list[Primitive],
    hole_center: tuple[float, float],
    radius: float,
    label_anchor: tuple[float, float],
) -> None:
    dx, dy = label_anchor[0] - hole_center[0], label_anchor[1] - hole_center[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return
    ux, uy = dx / length, dy / length
    tip = (hole_center[0] + radius * ux, hole_center[1] + radius * uy)
    elbow = (max(tip[0] + 4.0, label_anchor[0] - 8.0), label_anchor[1])
    entities.append(line(tip[0], tip[1], elbow[0], elbow[1], "DIMENSIONS", 0.12))
    entities.append(line(elbow[0], elbow[1], label_anchor[0], label_anchor[1], "DIMENSIONS", 0.12))

    arrow_length = 1.5
    normal = (-uy * 0.48, ux * 0.48)
    base = (tip[0] + ux * arrow_length, tip[1] + uy * arrow_length)
    entities.append(line(tip[0], tip[1], base[0] + normal[0], base[1] + normal[1], "DIMENSIONS", 0.12))
    entities.append(line(tip[0], tip[1], base[0] - normal[0], base[1] - normal[1], "DIMENSIONS", 0.12))


def _ordinate_count_prefix(dimension: dict[str, Any]) -> str:
    count = dimension.get("count") or 1
    return f"{count}X " if count > 1 else ""


def _draw_hole_dimensions(
    entities: list[Primitive],
    placed: dict[str, _PlacedView],
    dimensions: list[dict[str, Any]],
    holes: list[Hole],
    bounds: Bounds3D,
    style: str,
    view_geometry: Mapping[str, Sequence[Primitive]],
    sheet_width: float,
    sheet_height: float,
) -> set[str]:
    top = placed.get("top")
    if not top:
        return set()
    leader_ids: set[str] = set()
    diameters = [d for d in dimensions if d["kind"] == "hole_diameter"]
    for index, diameter in enumerate(diameters):
        selected = [hole for hole in holes if hole.index in diameter["hole_indices"]]
        if not selected:
            continue
        label = hole_callout_text(diameter)
        label_x = top.bounds.xmax + 25.0
        label_y = top.bounds.ymax - 5.0 - 8.0 * index
        hole = min(
            selected,
            key=lambda candidate: math.dist(
                _sheet_point(top, project_point((candidate.x, candidate.y, 0.0), top.projection)),
                (label_x, label_y),
            ),
        )
        center = _sheet_point(top, project_point((hole.x, hole.y, 0.0), top.projection))
        label_anchor = (label_x - 1.5, label_y)
        _arrowed_hole_leader(entities, center, hole.radius * top.factor, label_anchor)
        entities.append(text(label, label_x, label_y, 2.8, "DIMENSIONS", dimension_id=diameter["id"]))
        leader_ids.add(diameter["id"])

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

    # Ordinate labels use measured font advances to select non-overlapping rows.
    x_rows: list[list[tuple[float, float]]] = []
    x_stations: list[tuple[dict[str, Any], float, str, int]] = []
    for dimension in x_dimensions:
        group_holes = [hole for hole in holes if hole.index in dimension["hole_indices"]]
        if not group_holes:
            continue
        local_x = sum(hole.x for hole in group_holes) / len(group_holes)
        station_x = _sheet_point(top, project_point((local_x, bounds.ymin, 0.0), top_view))[0]
        label = f"{_ordinate_count_prefix(dimension)}X{dimension['display']}"
        half_width = text_width_mm(label, 2.6) / 2 + 1.0
        row = next(
            (
                index
                for index, stations in enumerate(x_rows)
                if all(abs(station_x - other_x) >= half_width + other_half for other_x, other_half in stations)
            ),
            len(x_rows),
        )
        if row == len(x_rows):
            x_rows.append([])
        x_rows[row].append((station_x, half_width))
        x_stations.append((dimension, station_x, label, row))
    for dimension, station_x, label, row in x_stations:
        baseline_y = bottom - 8.0 - 4.5 * row
        entities.append(line(station_x, bottom, station_x, baseline_y - 1.0, "DIMENSIONS", 0.12))
        entities.append(line(station_x, baseline_y - 1.0, station_x, baseline_y + 1.0, "DIMENSIONS", 0.16))
        entities.append(text(label, station_x, baseline_y - 3.2, 2.6, "DIMENSIONS", "center", dimension_id=dimension["id"]))
    for row, stations in enumerate(x_rows):
        baseline_y = bottom - 8.0 - 4.5 * row
        entities.append(line(left, baseline_y, max([left, *(station for station, _ in stations)]), baseline_y, "DIMENSIONS", 0.16))
        entities.append(circle((left, baseline_y), 0.6, "DIMENSIONS", 0.16))

    y_lanes: list[list[tuple[float, float]]] = []
    y_stations: list[tuple[dict[str, Any], float, str, int]] = []
    y_label_width = max(
        (text_width_mm(f"{_ordinate_count_prefix(d)}Y{d['display']}", 2.6) for d in y_dimensions),
        default=0.0,
    )
    lane_step = max(6.0, y_label_width + 3.0)
    for dimension in y_dimensions:
        group_holes = [hole for hole in holes if hole.index in dimension["hole_indices"]]
        if not group_holes:
            continue
        local_y = sum(hole.y for hole in group_holes) / len(group_holes)
        station_y = _sheet_point(top, project_point((bounds.xmin, local_y, 0.0), top_view))[1]
        label = f"{_ordinate_count_prefix(dimension)}Y{dimension['display']}"
        prototype = text(label, 0.0, station_y - 1.0, 2.6, "DIMENSIONS", "right")
        prototype_bounds = text_bounds(prototype)
        interval = (prototype_bounds.ymin, prototype_bounds.ymax)
        lane = next(
            (
                index
                for index, intervals in enumerate(y_lanes)
                if all(interval[0] >= other[1] + 0.7 or other[0] >= interval[1] + 0.7 for other in intervals)
            ),
            len(y_lanes),
        )
        if lane == len(y_lanes):
            y_lanes.append([])
        y_lanes[lane].append(interval)
        y_stations.append((dimension, station_y, label, lane))
    for dimension, station_y, label, lane in y_stations:
        baseline_x = left - 13.0 - lane * lane_step
        entities.append(line(left, station_y, baseline_x - 1.0, station_y, "DIMENSIONS", 0.12))
        entities.append(line(baseline_x - 1.0, station_y, baseline_x + 1.0, station_y, "DIMENSIONS", 0.16))
        candidates = [
            (baseline_x - 2.2, "right"),
            (baseline_x + 2.2, "left"),
        ]
        for offset in range(10, 42, 2):
            candidates.extend(((baseline_x - offset, "right"), (baseline_x + offset, "left")))
        selected: Primitive | None = None
        for anchor_x, align in candidates:
            candidate = text(
                label,
                anchor_x,
                station_y - 1.0,
                2.6,
                "DIMENSIONS",
                align,
                dimension_id=dimension["id"],
            )
            candidate_bounds = text_bounds(candidate)
            if candidate_bounds is None or (
                candidate_bounds.xmin < MARGIN_MM - 1e-7
                or candidate_bounds.ymin < MARGIN_MM - 1e-7
                or candidate_bounds.xmax > sheet_width - MARGIN_MM + 1e-7
                or candidate_bounds.ymax > sheet_height - MARGIN_MM + 1e-7
            ):
                continue
            if text_view_collisions([candidate], view_geometry):
                continue
            combined = [*entities, candidate]
            candidate_index = len(combined) - 1
            if any(candidate_index in pair for pair in text_text_collisions(combined)):
                continue
            selected = candidate
            break
        if selected is None:
            selected = text(
                label,
                baseline_x - 2.2,
                station_y - 1.0,
                2.6,
                "DIMENSIONS",
                "right",
                dimension_id=dimension["id"],
            )
        entities.append(selected)
    for lane, intervals in enumerate(y_lanes):
        station_ys = [station_y for _, station_y, _, assigned_lane in y_stations if assigned_lane == lane]
        baseline_x = left - 13.0 - lane * lane_step
        if station_ys:
            entities.append(line(baseline_x, min(station_ys), baseline_x, max(station_ys), "DIMENSIONS", 0.16))
            entities.append(circle((baseline_x, top.bounds.ymin), 0.6, "DIMENSIONS", 0.16))

    x_spacings = [d for d in dimensions if d["kind"] == "hole_spacing" and d["axis"] == "x"]
    x_coordinate_lanes = max((row for _, _, _, row in x_stations), default=-1) + 1
    spacing_y_start = 18.0 + 4.5 * max(0, x_coordinate_lanes - 1) if x_dimensions else 8.0
    for index, dimension in enumerate(x_spacings):
        source = dimension["source"]
        first, last = source["from_coordinate"], source["to_coordinate"]
        a = _sheet_point(top, project_point((first, bounds.ymin, 0.0), top_view))
        b = _sheet_point(top, project_point((last, bounds.ymin, 0.0), top_view))
        station_y = bottom - spacing_y_start - 8.0 * index
        entities.extend(
            [
                line(a[0], bottom, a[0], station_y - 1.0, "DIMENSIONS"),
                line(b[0], bottom, b[0], station_y - 1.0, "DIMENSIONS"),
            ]
        )
        _dimension_line(entities, (a[0], station_y), (b[0], station_y), style)
        entities.append(
            text(dimension["display"], (a[0] + b[0]) / 2, station_y + 1.5, 2.8, "DIMENSIONS", "center", dimension_id=dimension["id"])
        )

    y_spacings = [d for d in dimensions if d["kind"] == "hole_spacing" and d["axis"] == "y"]
    y_coordinate_lanes = max((lane for _, _, _, lane in y_stations), default=-1) + 1
    spacing_x_start = 18.0 + y_coordinate_lanes * lane_step if y_dimensions else 8.0
    for index, dimension in enumerate(y_spacings):
        source = dimension["source"]
        first, last = source["from_coordinate"], source["to_coordinate"]
        a = _sheet_point(top, project_point((bounds.xmin, first, 0.0), top_view))
        b = _sheet_point(top, project_point((bounds.xmin, last, 0.0), top_view))
        station_x = left - spacing_x_start - 8.0 * index
        entities.extend(
            [
                line(left, a[1], station_x - 1.0, a[1], "DIMENSIONS"),
                line(left, b[1], station_x - 1.0, b[1], "DIMENSIONS"),
            ]
        )
        _dimension_line(entities, (station_x, a[1]), (station_x, b[1]), style)
        entities.append(
            text(dimension["display"], station_x - 2.0, (a[1] + b[1]) / 2, 2.8, "DIMENSIONS", "right", dimension_id=dimension["id"])
        )
    return leader_ids


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
    view_geometry: dict[str, list[Primitive]],
    sheet_width: float,
    sheet_height: float,
) -> None:
    labels = {view.name: view.label for view in request.views}
    placed_text: list[Rect] = []
    for name, item in placed.items():
        label = labels.get(name) or name.upper()
        if item.projection.metadata.get("kind") in {"section", "detail"}:
            magnification = item.projection.metadata.get("scale", 1.0)
            label += f"  x{magnification:g} SHEET SCALE"
        value = str(label).upper()
        candidates = (
            (item.center[0], item.bounds.ymax + 5.0, "center"),
            (item.center[0], item.bounds.ymin - 4.0, "center"),
            (item.bounds.xmin - 4.0, item.center[1], "right"),
            (item.bounds.xmax + 4.0, item.center[1], "left"),
            (item.bounds.xmin - 4.0, item.bounds.ymax + 5.0, "right"),
            (item.bounds.xmax + 4.0, item.bounds.ymax + 5.0, "left"),
            (item.bounds.xmin - 4.0, item.bounds.ymin - 4.0, "right"),
            (item.bounds.xmax + 4.0, item.bounds.ymin - 4.0, "left"),
        )
        selected: Primitive | None = None
        for x, y, align in candidates:
            candidate = text(value, x, y, 2.6, "ANNOTATION", align)
            candidate_bounds = text_bounds(candidate)
            if candidate_bounds is None:
                continue
            if (
                candidate_bounds.xmin < MARGIN_MM - 1e-7
                or candidate_bounds.ymin < MARGIN_MM - 1e-7
                or candidate_bounds.xmax > sheet_width - MARGIN_MM + 1e-7
                or candidate_bounds.ymax > sheet_height - MARGIN_MM + 1e-7
            ):
                continue
            if any(
                primitive_intersects_rect(geometry, candidate_bounds)
                for other_name, geometry_items in view_geometry.items()
                if other_name != name
                for geometry in geometry_items
            ):
                continue
            existing_text_bounds = [
                bounds
                for entity in entities
                if entity.kind == "text"
                and (bounds := text_bounds(entity)) is not None
            ]
            if any(
                candidate_bounds.intersects(bounds, 0.15)
                for bounds in (*existing_text_bounds, *placed_text)
            ):
                continue
            selected = candidate
            placed_text.append(candidate_bounds)
            break
        if selected is None:
            selected = text(value, item.center[0], item.bounds.ymax + 5.0, 2.6, "ANNOTATION", "center")
            candidate_bounds = text_bounds(selected)
            if candidate_bounds is not None:
                placed_text.append(candidate_bounds)
        entities.append(selected)


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
    bounds = text_bounds(entity)
    if bounds is None:
        return None
    return (bounds.xmin, bounds.ymin, bounds.xmax, bounds.ymax)


def _drawing_checks(
    entities: list[Primitive],
    dimensions: list[dict[str, Any]],
    placed: dict[str, _PlacedView],
    sheet_width: float,
    sheet_height: float,
    auxiliary: AuxiliaryLayout,
    plan: PackedViews,
    view_geometry: dict[str, list[Primitive]],
    hole_leader_ids: set[str],
    bom: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

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

    add_check(
        "layout_fit",
        "pass" if plan.fits else "fail",
        "Projected view geometry fits without overlap at the selected physical scale."
        if plan.fits
        else "Projected view geometry does not fit the usable sheet area without overlap.",
        None if plan.fits else f"scale={_scale_label(plan.scale)}; area={plan.area.as_list()}",
    )

    geometry_overflow = drawing_geometry_margin_overflow(entities, sheet_width, sheet_height)
    add_check(
        "sheet_margins",
        "fail" if geometry_overflow else "pass",
        "Drawing geometry stays within the sheet margins."
        if not geometry_overflow
        else "One or more drawing strokes extend beyond the sheet margins.",
        str(len(geometry_overflow)) if geometry_overflow else None,
    )
    text_overflow = text_margin_overflow(entities, sheet_width, sheet_height)
    add_check(
        "text_margins",
        "fail" if text_overflow else "pass",
        "Measured text glyphs stay within the sheet margins."
        if not text_overflow
        else "One or more measured text glyph bounds extend beyond the sheet margins.",
        str(len(text_overflow)) if text_overflow else None,
    )

    title_bounds = auxiliary.title_bounds
    title_fits = (
        title_bounds.xmin >= MARGIN_MM - 1e-7
        and title_bounds.ymin >= MARGIN_MM - 1e-7
        and title_bounds.xmax <= sheet_width - MARGIN_MM + 1e-7
        and title_bounds.ymax <= sheet_height - MARGIN_MM + 1e-7
    )
    add_check(
        "title_block_bounds",
        "pass" if title_fits else "fail",
        "Title block fits inside the sheet frame." if title_fits else "Title block exceeds the sheet frame.",
        None if title_fits else str(title_bounds.as_list()),
    )
    title_text_overflow = [
        index
        for index, entity in enumerate(entities)
        if entity.kind == "text"
        and entity.layer == "TITLE"
        and (bounds := text_bounds(entity)) is not None
        and not title_bounds.contains(bounds)
    ]
    add_check(
        "title_block_text_overflow",
        "fail" if title_text_overflow else "pass",
        "Every title block field fits its measured text bounds."
        if not title_text_overflow
        else "One or more title block fields exceed the title block bounds.",
        str(len(title_text_overflow)) if title_text_overflow else None,
    )

    if auxiliary.schedule_bounds is None:
        add_check("schedule_bounds", "pass", "No notes or schedule tables were requested.")
        schedule_text_overflow: list[int] = []
    else:
        schedule_bounds = auxiliary.schedule_bounds
        schedule_fits = (
            schedule_bounds.xmin >= MARGIN_MM - 1e-7
            and schedule_bounds.ymin >= MARGIN_MM - 1e-7
            and schedule_bounds.xmax <= sheet_width - MARGIN_MM + 1e-7
            and schedule_bounds.ymax <= sheet_height - MARGIN_MM + 1e-7
        )
        add_check(
            "schedule_bounds",
            "pass" if schedule_fits else "fail",
            "Notes and schedule tables fit inside the sheet frame."
            if schedule_fits
            else "Notes or schedule tables exceed the sheet frame.",
            None if schedule_fits else str(schedule_bounds.as_list()),
        )
        schedule_text_overflow = [
            index
            for index, entity in enumerate(entities)
            if entity.kind == "text"
            and entity.layer == "SCHEDULE"
            and (bounds := text_bounds(entity)) is not None
            and not schedule_bounds.contains(bounds)
        ]
    add_check(
        "schedule_text_overflow",
        "fail" if schedule_text_overflow else "pass",
        "Every note and schedule cell fits its measured text bounds."
        if not schedule_text_overflow
        else "One or more note or schedule cells exceed their panel bounds.",
        str(len(schedule_text_overflow)) if schedule_text_overflow else None,
    )

    view_names = list(placed)
    view_collisions = [
        (view_names[index], view_names[other_index])
        for index, first_name in enumerate(view_names)
        for other_index in range(index + 1, len(view_names))
        if bounds_collide(placed[first_name].bounds, placed[view_names[other_index]].bounds)
    ]
    add_check(
        "view_view_collisions",
        "fail" if view_collisions else "pass",
        "Projected view bounds do not overlap."
        if not view_collisions
        else "Projected view bounds overlap.",
        ", ".join(f"{first}/{second}" for first, second in view_collisions) if view_collisions else None,
    )
    auxiliary_collisions = [
        name
        for name, view in placed.items()
        if bounds_collide(view.bounds, title_bounds)
        or (auxiliary.schedule_bounds is not None and bounds_collide(view.bounds, auxiliary.schedule_bounds))
    ]
    add_check(
        "view_auxiliary_collisions",
        "fail" if auxiliary_collisions else "pass",
        "Projected views do not overlap the title block or schedules."
        if not auxiliary_collisions
        else "A projected view overlaps the title block or schedule panel.",
        ", ".join(auxiliary_collisions) if auxiliary_collisions else None,
    )

    displayed_ids = {entity.dimension_id for entity in entities if entity.kind == "text" and entity.dimension_id}
    missing = [dimension["id"] for dimension in dimensions if dimension["id"] not in displayed_ids]
    add_check(
        "dimension_coverage",
        "fail" if missing else "pass",
        "Requested dimensions missing from the sheet."
        if missing
        else "Every reported dimension appears on the sheet.",
        ", ".join(missing) if missing else None,
    )
    source_ok = all(
        isinstance(dimension.get("source"), dict)
        and math.isfinite(float(dimension.get("value", float("nan"))))
        for dimension in dimensions
    )
    add_check(
        "dimension_sources",
        "pass" if source_ok else "fail",
        "Every reported dimension has a numeric value and geometry provenance."
        if source_ok
        else "A dimension is missing geometry provenance or a finite measured value.",
    )
    expected_leaders = {dimension["id"] for dimension in dimensions if dimension["kind"] == "hole_diameter"}
    missing_leaders = sorted(expected_leaders - hole_leader_ids)
    add_check(
        "hole_leader_coverage",
        "fail" if missing_leaders else "pass",
        "Every displayed hole diameter callout has an arrowed leader."
        if not missing_leaders
        else "One or more displayed hole diameter callouts have no arrowed leader.",
        ", ".join(missing_leaders) if missing_leaders else None,
    )

    collisions = text_text_collisions(entities)
    add_check(
        "dimension_text_collisions",
        "fail" if collisions else "pass",
        "Measured text bounds do not overlap."
        if not collisions
        else "Measured text bounds overlap.",
        str(len(collisions)) if collisions else None,
    )
    text_geometry_collisions = text_view_collisions(entities, view_geometry)
    add_check(
        "text_view_geometry_collisions",
        "fail" if text_geometry_collisions else "pass",
        "Text glyph bounds stay clear of projected view geometry."
        if not text_geometry_collisions
        else "One or more text glyph bounds intersect projected view geometry.",
        ", ".join(f"text[{index}] {value!r}/{name}" for index, name, value in text_geometry_collisions)
        if text_geometry_collisions
        else None,
    )
    small_text = [
        index for index, entity in enumerate(entities)
        if entity.kind == "text" and entity.height < 2.5 - 1e-8
    ]
    add_check(
        "minimum_text_height",
        "fail" if small_text else "pass",
        "All drawing text is at least 2.5 mm high."
        if not small_text
        else "One or more drawing text entities are below 2.5 mm high.",
        str(len(small_text)) if small_text else None,
    )
    invalid_bom_rows = [
        str(row.get("item", index + 1))
        for index, row in enumerate(bom)
        if isinstance(row.get("quantity"), bool)
        or not isinstance(row.get("quantity"), int)
        or row["quantity"] <= 0
    ]
    add_check(
        "bom_quantities",
        "fail" if invalid_bom_rows else "pass",
        "BOM quantities are positive counts of exposed assembly child instances."
        if not invalid_bom_rows
        else "A BOM quantity is missing or is not a positive assembly child count.",
        ", ".join(invalid_bom_rows) if invalid_bom_rows else None,
    )
    return checks


def make_drawing(ctx: Any, req: Any, out: str | Path) -> DrawingResult:
    """Project a structural model context onto a composed drawing sheet."""
    request = _normalize_request(req)
    shape = context_shape(ctx)
    units = context_units(ctx)
    bounds = shape_bounds(shape)
    if min(bounds.size) < -1e-9 or max(bounds.size) <= 1e-9:
        raise ValueError("The supplied shape has an empty or invalid bounding box.")
    holes = through_holes(shape, bounds)
    dimensions = _make_dimensions(request.dimensions, bounds, holes, units)
    bom = bom_from_context(ctx) if request.bom else []

    from .views import project_drawing_view
    registered = {view.name:view for view in request.views}
    resolved = []
    for view in request.views:
        parent_name = view.parent or "front"
        parent = registered.get(parent_name)
        if parent is None and parent_name in _STANDARD_VIEWS:
            parent = DrawingView(parent_name)
        if view.kind == "detail" and parent is not None:
            if parent.kind != "orthographic":
                raise ValueError("A detail parent must be an orthographic view.")
            default_direction, default_up = _STANDARD_VIEWS.get(parent.name, (None,None))
            direction = parent.direction or default_direction
            up = parent.up or default_up
            if view.direction is not None and math.dist(_unit(view.direction), _unit(direction)) > 1e-8:
                raise ValueError("Detail direction must match its parent view.")
            if view.up is not None and up is not None and math.dist(_unit(view.up), _unit(up)) > 1e-8:
                raise ValueError("Detail up vector must match its parent view.")
            view = replace(view, direction=direction, up=up)
        resolved.append(view)
    request = replace(request, views=tuple(resolved))
    projections = tuple(project_drawing_view(shape, view) for view in request.views)
    page_width, page_height = SHEET_SIZES_MM[request.sheet]
    unit_factor = UNIT_TO_MM[units]
    scale_label = "1:1"
    auxiliary: AuxiliaryLayout | None = None
    plan: PackedViews | None = None
    for _ in range(12):
        auxiliary = compose_auxiliary_layout(
            page_width,
            page_height,
            title_block=request.title_block,
            notes=request.notes,
            revisions=request.revisions,
            bom=bom,
            context=ctx,
            units=units,
            sheet=request.sheet,
            style=request.style,
            scale_label=scale_label,
        )
        plan = pack_views(
            projections,
            page_width,
            page_height,
            request.style,
            unit_factor,
            dimensions,
            auxiliary.bottom_keepout_mm,
        )
        actual_scale_label = _scale_label(plan.scale)
        if actual_scale_label == scale_label:
            break
        scale_label = actual_scale_label
    assert auxiliary is not None and plan is not None
    placed = _place_views(projections, plan)

    entities: list[Primitive] = []
    _sheet_frame(entities, page_width, page_height)
    view_geometry: dict[str, list[Primitive]] = {}
    for name, view in placed.items():
        first_entity = len(entities)
        _emit_view_geometry(entities, view)
        view_geometry[name] = entities[first_entity:]
    marker_start = len(entities)
    _view_markers(entities, shape, placed, request)
    marker_geometry = [entity for entity in entities[marker_start:] if entity.kind != "text"]
    if marker_geometry:
        view_geometry["_markers"] = marker_geometry
    _view_label_entities(entities, placed, request, view_geometry, page_width, page_height)
    _draw_overall_dimensions(entities, placed, dimensions, request.style)
    hole_leader_ids = _draw_hole_dimensions(
        entities,
        placed,
        dimensions,
        holes,
        bounds,
        request.style,
        view_geometry,
        page_width,
        page_height,
    )
    entities.extend(auxiliary.entities)
    checks = _drawing_checks(
        entities,
        dimensions,
        placed,
        page_width,
        page_height,
        auxiliary,
        plan,
        view_geometry,
        hole_leader_ids,
        bom,
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
            "cell_bounds_mm": view.cell.as_list(),
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
    scale_text = _scale_label(plan.scale)
    format_summary = ", ".join(
        (*[format_name for format_name in request.formats if format_name != "png"], "png preview")
    )
    extras = []
    if request.notes:
        extras.append(f"{len(request.notes)} notes")
    if request.revisions:
        extras.append(f"{len(request.revisions)} revisions")
    if request.bom:
        extras.append(f"{len(bom)} BOM rows from assembly children")
    extra_summary = f"; {', '.join(extras)}" if extras else ""
    summary = (
        f"{request.sheet} {request.style.upper()} sheet at {scale_text}; "
        f"{len(placed)} views, {len(dimensions)} geometry-derived dimensions{extra_summary}; "
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
            "scale": plan.scale,
            "scale_label": scale_text,
            "margin_mm": MARGIN_MM,
            "title_block_bounds_mm": auxiliary.title_bounds.as_list(),
            "schedule_bounds_mm": auxiliary.schedule_bounds.as_list() if auxiliary.schedule_bounds else None,
            "view_area_mm": plan.area.as_list(),
            "layout_fits": plan.fits,
            "hole_count": len(holes),
            "bom_item_count": len(bom),
        },
        views=view_results,
        ok=all_passed,
        summary=summary,
        notes=list(request.notes),
        revisions=[dict(row) for row in request.revisions],
        bom=[dict(row) for row in bom],
    )
