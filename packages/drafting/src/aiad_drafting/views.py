"""OCCT-backed orthographic, section, and circular detail projections.

The returned :class:`ProjectedView` uses the same local coordinates and
``Curve`` entities as ``geometry.project_view``. It can therefore go through
the existing drawing-sheet placement and export code without a second view
representation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math
from typing import Any

from .geometry import (
    Bounds3D,
    Curve,
    ProjectedBounds,
    ProjectedView,
    _STANDARD_VIEWS,
    _cross,
    _dot,
    _unit,
    context_value,
    project_view,
    shape_bounds,
    wrapped_shape,
)
from .types import DrawingView


_SECTION_PLANES = {
    "XY": {
        "point_axis": 2,
        "normal": (0.0, 0.0, 1.0),
        "direction": (0.0, 0.0, 1.0),
        "up": (0.0, 1.0, 0.0),
        "default_parent": "front",
    },
    "XZ": {
        "point_axis": 1,
        "normal": (0.0, 1.0, 0.0),
        "direction": (0.0, -1.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        "default_parent": "top",
    },
    "YZ": {
        "point_axis": 0,
        "normal": (1.0, 0.0, 0.0),
        "direction": (1.0, 0.0, 0.0),
        "up": (0.0, 0.0, 1.0),
        "default_parent": "front",
    },
}

_SECTION_FRAMES = {
    plane: (data["direction"], data["up"])
    for plane, data in _SECTION_PLANES.items()
}


@dataclass(frozen=True)
class SectionPlaneTrace:
    """A section plane and its edge-on trace in a parent orthographic view.

    ``curve`` is ``None`` when the parent view looks along the section plane
    normal or when the plane does not cross the projected model bounds.
    ``point`` and ``normal`` are in model coordinates and ``offset`` is in
    model units.
    """

    plane: str
    offset: float
    point: tuple[float, float, float]
    normal: tuple[float, float, float]
    parent: str
    curve: Curve | None
    reason: str | None = None


def _name(view: Any, default: str = "front") -> str:
    return str(context_value(view, "name", default) or default).strip().lower()


def _number(value: Any, field_name: str, default: float) -> float:
    try:
        result = float(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Drawing view {field_name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"Drawing view {field_name} must be a finite number.")
    return result


def _unit_pair(value: Any, field_name: str) -> tuple[float, float]:
    if value is None:
        raise ValueError(f"Detail view requires a 2D {field_name}.")
    try:
        pair = tuple(float(component) for component in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Detail view {field_name} must have two finite numbers.") from exc
    if len(pair) != 2 or not all(math.isfinite(component) for component in pair):
        raise ValueError(f"Detail view {field_name} must have two finite numbers.")
    return pair


def _frame(
    name: str,
    direction: Any = None,
    up_hint: Any = None,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    normalized_name = {"isometric": "iso"}.get(name.strip().lower(), name.strip().lower())
    default_direction, default_up = _STANDARD_VIEWS.get(normalized_name, (None, None))
    if direction is None:
        if default_direction is None:
            raise ValueError(
                f"Custom drawing view {name!r} requires a direction vector."
            )
        direction = default_direction
    if up_hint is None:
        up_hint = default_up or (0.0, 0.0, 1.0)
    try:
        direction_raw = tuple(float(value) for value in direction)
        up_raw = tuple(float(value) for value in up_hint)
    except (TypeError, ValueError) as exc:
        raise ValueError("Drawing view direction and up must be finite 3D vectors.") from exc
    if (
        len(direction_raw) != 3
        or len(up_raw) != 3
        or not all(math.isfinite(value) for value in (*direction_raw, *up_raw))
    ):
        raise ValueError("Drawing view direction and up must have three coordinates.")
    try:
        direction3 = _unit(direction_raw)
        up3 = _unit(up_raw)
    except ValueError as exc:
        raise ValueError("Drawing view direction and up cannot be zero vectors.") from exc
    right_raw = _cross(up3, direction3)
    if math.sqrt(_dot(right_raw, right_raw)) <= 1e-8:
        fallback = (0.0, 1.0, 0.0) if abs(direction3[1]) < 0.9 else (1.0, 0.0, 0.0)
        right_raw = _cross(fallback, direction3)
    right = _unit(right_raw)
    up = _unit(_cross(direction3, right))
    return direction3, right, up


def _drawing_view(value: DrawingView | Mapping[str, Any] | Any) -> DrawingView:
    if isinstance(value, DrawingView):
        return value
    return DrawingView(
        name=str(context_value(value, "name", context_value(value, "view", "front"))),
        direction=(
            tuple(context_value(value, "direction"))
            if context_value(value, "direction") is not None
            else None
        ),
        up=tuple(context_value(value, "up")) if context_value(value, "up") is not None else None,
        label=context_value(value, "label"),
        hidden_lines=bool(context_value(value, "hidden_lines", True)),
        kind=str(context_value(value, "kind", "orthographic")),
        plane=str(context_value(value, "plane", "XY")),
        offset=context_value(value, "offset", 0.0),
        center=(tuple(context_value(value, "center")) if context_value(value, "center") is not None else None),
        radius=context_value(value, "radius"),
        scale=context_value(
            value,
            "scale",
            context_value(value, "scale_multiplier", 1.0),
        ),
        parent=context_value(value, "parent"),
    )


def _bounds_from_curves(curves: tuple[Curve, ...] | list[Curve]) -> ProjectedBounds:
    points: list[tuple[float, float]] = []
    for curve in curves:
        if curve.kind in {"line", "polyline"}:
            points.extend(curve.points)
        elif curve.kind == "circle":
            cx, cy = curve.center
            radius = curve.radius
            points.extend(
                (
                    (cx - radius, cy - radius),
                    (cx + radius, cy + radius),
                )
            )
        elif curve.kind == "arc":
            cx, cy = curve.center
            radius = curve.radius
            start = math.radians(curve.start_angle)
            end = math.radians(curve.end_angle)
            sweep = _arc_sweep(start, end, curve.ccw)
            angles = [start, start + sweep]
            for candidate in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
                if _angle_on_sweep(candidate, start, sweep):
                    angles.append(candidate)
            points.extend(
                (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
                for angle in angles
            )
    if not points:
        raise ValueError("OCCT produced no projected edges for the requested view.")
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return ProjectedBounds(min(xs), min(ys), max(xs), max(ys))


def _arc_sweep(start: float, end: float, ccw: bool) -> float:
    if ccw:
        return (end - start) % (2 * math.pi)
    return -((start - end) % (2 * math.pi))


def _angle_on_sweep(angle: float, start: float, sweep: float, tolerance: float = 1e-10) -> bool:
    if sweep >= 0:
        distance = (angle - start) % (2 * math.pi)
        return distance <= sweep + tolerance
    distance = (start - angle) % (2 * math.pi)
    return distance <= -sweep + tolerance


def _scale_curve(curve: Curve, pivot: tuple[float, float], factor: float) -> Curve:
    px, py = pivot
    points = tuple(
        (px + (x - px) * factor, py + (y - py) * factor)
        for x, y in curve.points
    )
    center = None
    if curve.center is not None:
        center = (
            px + (curve.center[0] - px) * factor,
            py + (curve.center[1] - py) * factor,
        )
    radius = curve.radius * factor if curve.radius is not None else None
    return replace(curve, points=points, center=center, radius=radius)


def _scale_projection(
    projection: ProjectedView,
    factor: float,
    pivot: tuple[float, float] | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> ProjectedView:
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("Drawing view scale must be a positive finite multiplier.")
    if pivot is None:
        pivot = projection.bounds.center
    curves = tuple(_scale_curve(curve, pivot, factor) for curve in projection.curves)
    if factor == 1.0:
        bounds = projection.bounds
    else:
        bounds = ProjectedBounds(
            pivot[0] + (projection.bounds.xmin - pivot[0]) * factor,
            pivot[1] + (projection.bounds.ymin - pivot[1]) * factor,
            pivot[0] + (projection.bounds.xmax - pivot[0]) * factor,
            pivot[1] + (projection.bounds.ymax - pivot[1]) * factor,
        )
    merged_metadata = dict(projection.metadata)
    for key in ("boundary_curves", "hatch_curves"):
        metadata_curves = merged_metadata.get(key)
        if metadata_curves is not None:
            merged_metadata[key] = tuple(
                _scale_curve(curve, pivot, factor) for curve in metadata_curves
            )
    if metadata:
        merged_metadata.update(metadata)
    return replace(projection, curves=curves, bounds=bounds, metadata=merged_metadata)


def _circle_contains(point: tuple[float, float], center: tuple[float, float], radius: float) -> bool:
    return math.dist(point, center) <= radius + max(radius, 1.0) * 1e-10


def _clip_segment_to_circle(
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
    radius: float,
) -> list[Curve]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    fx, fy = start[0] - center[0], start[1] - center[1]
    a = dx * dx + dy * dy
    if a <= 1e-24:
        return []
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - radius * radius
    discriminant = b * b - 4.0 * a * c
    parameters = [0.0, 1.0]
    if discriminant >= -1e-12:
        root = math.sqrt(max(0.0, discriminant))
        for candidate in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)):
            if 1e-12 < candidate < 1.0 - 1e-12:
                parameters.append(candidate)
    parameters.sort()
    clipped: list[Curve] = []
    for lower, upper in zip(parameters, parameters[1:]):
        middle = (lower + upper) / 2.0
        midpoint = (start[0] + dx * middle, start[1] + dy * middle)
        if not _circle_contains(midpoint, center, radius):
            continue
        first = (start[0] + dx * lower, start[1] + dy * lower)
        last = (start[0] + dx * upper, start[1] + dy * upper)
        if math.dist(first, last) > max(radius, 1.0) * 1e-11:
            clipped.append(Curve("line", False, points=(first, last)))
    return clipped


def _circle_intersection_angles(
    curve_center: tuple[float, float],
    curve_radius: float,
    clip_center: tuple[float, float],
    clip_radius: float,
) -> tuple[float, ...]:
    dx = clip_center[0] - curve_center[0]
    dy = clip_center[1] - curve_center[1]
    distance = math.hypot(dx, dy)
    tolerance = max(curve_radius, clip_radius, 1.0) * 1e-10
    if distance <= tolerance:
        return ()
    if distance > curve_radius + clip_radius + tolerance:
        return ()
    if distance < abs(curve_radius - clip_radius) - tolerance:
        return ()
    cosine = (
        curve_radius * curve_radius + distance * distance - clip_radius * clip_radius
    ) / (2.0 * distance * curve_radius)
    if cosine < -1.0 - 1e-10 or cosine > 1.0 + 1e-10:
        return ()
    half_angle = math.acos(min(1.0, max(-1.0, cosine)))
    direction = math.atan2(dy, dx)
    first = (direction - half_angle) % (2 * math.pi)
    second = (direction + half_angle) % (2 * math.pi)
    if math.isclose(first, second, abs_tol=1e-10):
        return (first,)
    return (first, second)


def _clip_circular_curve(
    curve: Curve,
    clip_center: tuple[float, float],
    clip_radius: float,
) -> list[Curve]:
    center = curve.center
    radius = curve.radius
    if center is None or radius is None or radius <= 0:
        return []
    tolerance = max(radius, clip_radius, 1.0) * 1e-10
    if math.dist(center, clip_center) <= tolerance and abs(radius - clip_radius) <= tolerance:
        # Keep coincident model edges as selected geometry. Deduplication
        # merges the crop boundary later, and an exact crop stays nonempty.
        return [curve]
    if math.dist(center, clip_center) + radius <= clip_radius + tolerance:
        return [curve]

    is_circle = curve.kind == "circle"
    if is_circle:
        start = 0.0
        sweep = 2 * math.pi
    else:
        start = math.radians(curve.start_angle)
        sweep = _arc_sweep(start, math.radians(curve.end_angle), curve.ccw)

    intersections = _circle_intersection_angles(center, radius, clip_center, clip_radius)
    breaks = [0.0, 1.0]
    if intersections:
        for angle in intersections:
            if sweep > 0:
                delta = (angle - start) % (2 * math.pi)
            else:
                delta = (start - angle) % (2 * math.pi)
            parameter = delta / abs(sweep)
            if 1e-11 < parameter < 1.0 - 1e-11:
                breaks.append(parameter)
    breaks = sorted(set(breaks))
    pieces: list[Curve] = []
    for lower, upper in zip(breaks, breaks[1:]):
        if upper - lower <= 1e-12:
            continue
        middle_angle = start + sweep * (lower + upper) / 2.0
        middle = (
            center[0] + radius * math.cos(middle_angle),
            center[1] + radius * math.sin(middle_angle),
        )
        if not _circle_contains(middle, clip_center, clip_radius):
            continue
        if lower <= 1e-12 and upper >= 1.0 - 1e-12:
            pieces.append(curve)
            continue
        piece_start = (start + sweep * lower) % (2 * math.pi)
        piece_end = (start + sweep * upper) % (2 * math.pi)
        if is_circle and upper - lower >= 1.0 - 1e-12:
            pieces.append(
                Curve("circle", curve.hidden, center=center, radius=radius)
            )
        else:
            pieces.append(
                Curve(
                    "arc",
                    curve.hidden,
                    center=center,
                    radius=radius,
                    start_angle=math.degrees(piece_start),
                    end_angle=math.degrees(piece_end),
                    ccw=sweep > 0,
                )
            )
    return pieces


def _clip_curve_to_circle(
    curve: Curve,
    center: tuple[float, float],
    radius: float,
) -> list[Curve]:
    if curve.kind == "line":
        if len(curve.points) < 2:
            return []
        return [
            replace(piece, hidden=curve.hidden)
            for piece in _clip_segment_to_circle(curve.points[0], curve.points[-1], center, radius)
        ]
    if curve.kind == "polyline":
        clipped: list[Curve] = []
        for start, end in zip(curve.points, curve.points[1:]):
            clipped.extend(
                replace(piece, hidden=curve.hidden)
                for piece in _clip_segment_to_circle(start, end, center, radius)
            )
        return clipped
    if curve.kind in {"circle", "arc"}:
        return _clip_circular_curve(curve, center, radius)
    return []


def _curve_key(curve: Curve) -> tuple[Any, ...]:
    round_value = lambda value: round(float(value), 7)
    if curve.kind in {"circle", "arc"}:
        center = (round_value(curve.center[0]), round_value(curve.center[1]))
        radius = round_value(curve.radius)
        if curve.kind == "circle":
            return ("circle", center, radius)
        sweep = _arc_sweep(
            math.radians(curve.start_angle), math.radians(curve.end_angle), curve.ccw
        )
        return (
            "arc",
            center,
            radius,
            round_value(curve.start_angle % 360),
            round_value(curve.end_angle % 360),
            round_value(sweep),
        )
    points = tuple((round_value(x), round_value(y)) for x, y in curve.points)
    if curve.kind == "line":
        points = tuple(sorted(points))
    else:
        points = min(points, tuple(reversed(points)))
    return (curve.kind, points)


def _deduplicate_curves(curves: list[Curve] | tuple[Curve, ...]) -> tuple[Curve, ...]:
    unique: dict[tuple[Any, ...], Curve] = {}
    for curve in curves:
        key = _curve_key(curve)
        previous = unique.get(key)
        if previous is None or (previous.hidden and not curve.hidden):
            unique[key] = curve
    return tuple(sorted(unique.values(), key=lambda curve: (_curve_key(curve), curve.hidden)))


def _projected_edges_to_curves(
    shape: Any,
    right: tuple[float, float, float],
    up: tuple[float, float, float],
    direction: tuple[float, float, float],
) -> list[Curve]:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    def project(point: Any) -> tuple[float, float]:
        xyz = (float(point.X()), float(point.Y()), float(point.Z()))
        return (_dot(xyz, right), _dot(xyz, up))

    curves: list[Curve] = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        adaptor = BRepAdaptor_Curve(edge)
        first, last = adaptor.FirstParameter(), adaptor.LastParameter()
        if not math.isfinite(first) or not math.isfinite(last) or last - first <= 1e-12:
            explorer.Next()
            continue
        curve_type = adaptor.GetType()
        if curve_type == GeomAbs_Line:
            start, end = project(adaptor.Value(first)), project(adaptor.Value(last))
            if math.dist(start, end) > 1e-9:
                curves.append(Curve("line", False, points=(start, end)))
            explorer.Next()
            continue
        if curve_type == GeomAbs_Circle:
            circle = adaptor.Circle()
            center3 = circle.Location()
            center = project(center3)
            radius = float(circle.Radius())
            span = abs(last - first)
            if span >= 2 * math.pi - 1e-7:
                curves.append(Curve("circle", False, center=center, radius=radius))
            else:
                start = project(adaptor.Value(first))
                end = project(adaptor.Value(last))
                start_angle = math.degrees(
                    math.atan2(start[1] - center[1], start[0] - center[0])
                ) % 360
                end_angle = math.degrees(
                    math.atan2(end[1] - center[1], end[0] - center[0])
                ) % 360
                axis = circle.Axis().Direction()
                axis_direction = (float(axis.X()), float(axis.Y()), float(axis.Z()))
                curves.append(
                    Curve(
                        "arc",
                        False,
                        center=center,
                        radius=radius,
                        start_angle=start_angle,
                        end_angle=end_angle,
                        ccw=_dot(axis_direction, direction) >= 0,
                    )
                )
            explorer.Next()
            continue

        # General section curves (for example an ellipse cut from a cylinder)
        # have no exact SVG/DXF primitive in Curve, so retain their shape as a
        # sampled polyline. Lines and circles stay analytic above.
        steps = 48
        points = tuple(
            project(adaptor.Value(first + (last - first) * index / steps))
            for index in range(steps + 1)
        )
        if len(points) >= 2:
            curves.append(Curve("polyline", False, points=points))
        explorer.Next()
    return curves


def _section_location(
    view: DrawingView,
) -> tuple[
    str,
    float,
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    plane = str(view.plane).strip().upper()
    if plane not in _SECTION_PLANES:
        raise ValueError("Section plane must be XY, XZ, or YZ.")
    data = _SECTION_PLANES[plane]
    offset = _number(view.offset, "section offset", 0.0)
    point_list = [0.0, 0.0, 0.0]
    point_list[data["point_axis"]] = offset
    point = tuple(point_list)
    direction, right, up = _frame(plane, *_SECTION_FRAMES[plane])
    normal = data["normal"]
    return plane, offset, point, normal, direction, right, up


def _section_cap_faces(
    shape: Any,
    point: tuple[float, float, float],
    direction: tuple[float, float, float],
    right: tuple[float, float, float],
    up: tuple[float, float, float],
    bounds: Bounds3D,
) -> tuple[Any, ...]:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt

    shape_corners = [
        (x, y, z)
        for x in (bounds.xmin, bounds.xmax)
        for y in (bounds.ymin, bounds.ymax)
        for z in (bounds.zmin, bounds.zmax)
    ]
    u_values = [_dot(tuple(c - p for c, p in zip(corner, point)), right) for corner in shape_corners]
    v_values = [_dot(tuple(c - p for c, p in zip(corner, point)), up) for corner in shape_corners]
    margin = max(max(bounds.size), 1.0) * 0.1 + 1.0
    plane = gp_Pln(gp_Ax3(gp_Pnt(*point), gp_Dir(*direction), gp_Dir(*right)))
    plane_face = BRepBuilderAPI_MakeFace(
        plane,
        min(u_values) - margin,
        max(u_values) + margin,
        min(v_values) - margin,
        max(v_values) + margin,
    )
    if not plane_face.IsDone():
        raise ValueError("OCCT could not construct the requested section plane.")
    diagonal = math.sqrt(sum(size * size for size in bounds.size))
    reference = gp_Pnt(
        point[0] + direction[0] * (diagonal + margin + 1.0),
        point[1] + direction[1] * (diagonal + margin + 1.0),
        point[2] + direction[2] * (diagonal + margin + 1.0),
    )
    half_space = BRepPrimAPI_MakeHalfSpace(plane_face.Face(), reference).Solid()
    common = BRepAlgoAPI_Common(wrapped_shape(shape), half_space)
    if not common.IsDone() or common.Shape().IsNull():
        raise ValueError("OCCT failed to intersect the shape with the section half-space.")

    tolerance = max(max(bounds.size), 1.0) * 1e-7
    caps = []
    explorer = TopExp_Explorer(common.Shape(), TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane:
            face_plane = surface.Plane()
            location = face_plane.Location()
            axis = face_plane.Axis().Direction()
            face_normal = (float(axis.X()), float(axis.Y()), float(axis.Z()))
            face_point = (float(location.X()), float(location.Y()), float(location.Z()))
            coplanar = abs(abs(_dot(face_normal, direction)) - 1.0) <= 1e-7
            on_requested_plane = abs(_dot(tuple(a - b for a, b in zip(face_point, point)), direction)) <= tolerance
            if coplanar and on_requested_plane:
                caps.append(face)
        explorer.Next()
    return tuple(caps)


def _hatch_curves(
    cap_faces: tuple[Any, ...],
    boundary_curves: tuple[Curve, ...],
    point: tuple[float, float, float],
    right: tuple[float, float, float],
    up: tuple[float, float, float],
    bounds: ProjectedBounds,
) -> tuple[Curve, ...]:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt

    width, height = bounds.width, bounds.height
    diagonal = math.hypot(width, height)
    if diagonal <= 1e-10:
        return ()
    spacing = max(diagonal / 28.0, max(width, height) * 0.035, 1e-4)
    angle = math.pi / 4.0
    along = (math.cos(angle), math.sin(angle))
    across = (-along[1], along[0])
    corners = (
        (bounds.xmin, bounds.ymin),
        (bounds.xmin, bounds.ymax),
        (bounds.xmax, bounds.ymin),
        (bounds.xmax, bounds.ymax),
    )
    distances = [x * across[0] + y * across[1] for x, y in corners]
    low, high = min(distances), max(distances)
    count = min(160, max(1, int(math.ceil((high - low) / spacing))))
    center_distance = (low + high) / 2.0
    length = diagonal * 2.0 + spacing
    center_xy = bounds.center
    hatch_curves: list[Curve] = []

    for index in range(count + 1):
        distance = low + (high - low) * index / count
        center_line = (
            center_xy[0] + across[0] * (distance - center_distance),
            center_xy[1] + across[1] * (distance - center_distance),
        )
        start2 = (
            center_line[0] - along[0] * length,
            center_line[1] - along[1] * length,
        )
        end2 = (
            center_line[0] + along[0] * length,
            center_line[1] + along[1] * length,
        )

        def in_plane(coords: tuple[float, float]) -> tuple[float, float, float]:
            return (
                point[0] + right[0] * coords[0] + up[0] * coords[1],
                point[1] + right[1] * coords[0] + up[1] * coords[1],
                point[2] + right[2] * coords[0] + up[2] * coords[1],
            )

        start3, end3 = in_plane(start2), in_plane(end2)
        edge_builder = BRepBuilderAPI_MakeEdge(gp_Pnt(*start3), gp_Pnt(*end3))
        if not edge_builder.IsDone():
            continue
        line_edge = edge_builder.Edge()
        for face in cap_faces:
            clipped = BRepAlgoAPI_Common(face, line_edge)
            if not clipped.IsDone() or clipped.Shape().IsNull():
                continue
            explorer = TopExp_Explorer(clipped.Shape(), TopAbs_EDGE)
            while explorer.More():
                edge = TopoDS.Edge(explorer.Current())
                curve = _projected_edges_to_curves(
                    edge, right, up, tuple(_cross(right, up))
                )
                hatch_curves.extend(
                    item for item in curve if item.kind == "line"
                )
                explorer.Next()

    # Keep the parameter in the signature explicit: the cap boundaries define
    # where hatching may exist, while Common(face, line) performs the actual
    # material clipping, including interior holes.
    del boundary_curves
    return _deduplicate_curves(hatch_curves)


def _project_section(shape: Any, view: DrawingView) -> ProjectedView:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    multiplier = _number(view.scale, "scale", 1.0)
    if multiplier <= 0:
        raise ValueError("Drawing view scale must be a positive finite multiplier.")
    plane_name, offset, point, normal, direction, right, up = _section_location(view)
    bounds3d = shape_bounds(shape)
    if max(bounds3d.size) <= 1e-9:
        raise ValueError("The supplied shape has an empty or invalid bounding box.")
    gp_plane = _make_section_plane(point, direction, right)

    # This is the true OCCT plane/shape intersection. The half-space Boolean
    # below supplies the closed region used to clip hatching; it shares this
    # exact gp_Pln and does not approximate the cut with a thin slab.
    section = BRepAlgoAPI_Section(wrapped_shape(shape), gp_plane, True)
    if not section.IsDone():
        raise ValueError(
            f"OCCT failed to section the shape with plane {plane_name} at offset {offset:g}."
        )
    edge_explorer = TopExp_Explorer(section.Shape(), TopAbs_EDGE)
    has_intersection_edge = edge_explorer.More()
    cap_faces = _section_cap_faces(
        shape, point, direction, right, up, bounds3d
    )
    if not cap_faces:
        if not has_intersection_edge:
            raise ValueError(
                f"Section plane {plane_name} at offset {offset:g} does not intersect the shape."
            )
        raise ValueError(
            f"Section plane {plane_name} at offset {offset:g} touches the shape but encloses no material region."
        )

    boundary_curves: list[Curve] = []
    for face in cap_faces:
        face_edges = TopExp_Explorer(face, TopAbs_EDGE)
        while face_edges.More():
            boundary_curves.extend(
                _projected_edges_to_curves(
                    face_edges.Current(), right, up, direction
                )
            )
            face_edges.Next()
    boundaries = _deduplicate_curves(boundary_curves)
    if not boundaries:
        raise ValueError(
            f"OCCT produced no closed material boundary for section plane {plane_name} at offset {offset:g}."
        )
    bounds2d = _bounds_from_curves(boundaries)
    hatches = _hatch_curves(cap_faces, boundaries, point, right, up, bounds2d)
    curves = _deduplicate_curves([*boundaries, *hatches])
    base = ProjectedView(
        name=_name(view),
        direction=direction,
        right=right,
        up=up,
        curves=curves,
        bounds=_bounds_from_curves(curves),
        visible_count=len(boundaries),
        hidden_count=0,
        metadata={
            "kind": "section",
            "plane": plane_name,
            "offset": offset,
            "cut_plane": {
                "point": point,
                "normal": normal,
                "parent": view.parent or _SECTION_PLANES[plane_name]["default_parent"],
            },
            "boundary_curve_count": len(boundaries),
            "hatch_curve_count": len(hatches),
            "section_region_count": len(cap_faces),
            "boundary_curves": boundaries,
            "hatch_curves": hatches,
        },
    )
    return _scale_projection(base, multiplier, metadata={"scale": multiplier})


def _make_section_plane(
    point: tuple[float, float, float],
    direction: tuple[float, float, float],
    right: tuple[float, float, float],
) -> Any:
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt

    return gp_Pln(gp_Ax3(gp_Pnt(*point), gp_Dir(*direction), gp_Dir(*right)))


def _section_trace_curve(
    bounds: ProjectedBounds,
    normal: tuple[float, float, float],
    offset: float,
    right: tuple[float, float, float],
    up: tuple[float, float, float],
) -> Curve | None:
    a, b = _dot(normal, right), _dot(normal, up)
    if math.hypot(a, b) <= 1e-10:
        return None
    points: list[tuple[float, float]] = []
    if abs(b) > 1e-10:
        for x in (bounds.xmin, bounds.xmax):
            y = (offset - a * x) / b
            if bounds.ymin - 1e-8 <= y <= bounds.ymax + 1e-8:
                points.append((x, min(bounds.ymax, max(bounds.ymin, y))))
    if abs(a) > 1e-10:
        for y in (bounds.ymin, bounds.ymax):
            x = (offset - b * y) / a
            if bounds.xmin - 1e-8 <= x <= bounds.xmax + 1e-8:
                points.append((min(bounds.xmax, max(bounds.xmin, x)), y))
    distinct: list[tuple[float, float]] = []
    for candidate in points:
        if all(math.dist(candidate, prior) > 1e-8 for prior in distinct):
            distinct.append(candidate)
    if len(distinct) < 2:
        return None
    first, last = max(
        ((a, b) for index, a in enumerate(distinct) for b in distinct[index + 1:]),
        key=lambda pair: math.dist(*pair),
    )
    if math.dist(first, last) <= 1e-8:
        return None
    return Curve("line", False, points=(first, last))


def section_plane_trace(shape: Any, view: DrawingView | Mapping[str, Any]) -> SectionPlaneTrace:
    """Return the section plane and its line in the selected parent view.

    The trace is a line only when the parent view sees the section plane
    edge-on, as a conventional section marker does. The section projection
    also carries its cut plane, region count, and hatch count in ``metadata``.
    """

    normalized = _drawing_view(view)
    if normalized.kind.strip().lower() != "section":
        raise ValueError("section_plane_trace requires a DrawingView with kind='section'.")
    plane, offset, point, normal, _, _, _ = _section_location(normalized)
    parent = str(normalized.parent or _SECTION_PLANES[plane]["default_parent"]).strip().lower()
    parent_view = DrawingView(
        name=parent,
        direction=normalized.direction,
        up=normalized.up,
        hidden_lines=False,
    )
    direction, right, up = _frame(parent, parent_view.direction, parent_view.up)
    if abs(_dot(normal, direction)) > 1e-8:
        return SectionPlaneTrace(
            plane,
            offset,
            point,
            normal,
            parent,
            None,
            "parent view looks along the section plane normal",
        )
    try:
        parent_projection = project_view(shape, parent_view)
    except ValueError as exc:
        return SectionPlaneTrace(plane, offset, point, normal, parent, None, str(exc))
    curve = _section_trace_curve(parent_projection.bounds, normal, offset, right, up)
    reason = None if curve is not None else "section plane trace is outside the parent view bounds"
    return SectionPlaneTrace(plane, offset, point, normal, parent, curve, reason)


def project_drawing_view(shape: Any, view: DrawingView) -> ProjectedView:
    """Project a shape as an orthographic, OCCT section, or clipped detail view.

    Orthographic hidden lines come from the existing exact OCCT HLR pipeline.
    Section cuts use a true ``BRepAlgoAPI_Section`` plus an exact half-space
    intersection to create closed material regions and clip hatch strokes
    around holes. Detail views crop the parent projection to a circle before
    applying the optional scale multiplier.
    """

    normalized = _drawing_view(view)
    kind = str(normalized.kind or "orthographic").strip().lower()
    if kind not in {"orthographic", "section", "detail"}:
        raise ValueError("Drawing view kind must be orthographic, section, or detail.")

    if kind == "section":
        return _project_section(shape, normalized)

    if kind == "detail":
        parent = str(normalized.parent or "front").strip().lower()
        center = _unit_pair(normalized.center, "center")
        radius = _number(normalized.radius, "radius", 0.0)
        if radius <= 0:
            raise ValueError("Detail view radius must be greater than zero.")
        multiplier = _number(normalized.scale, "scale", 1.0)
        if multiplier <= 0:
            raise ValueError("Drawing view scale must be a positive finite multiplier.")
        parent_view = DrawingView(
            name=parent,
            direction=normalized.direction,
            up=normalized.up,
            hidden_lines=normalized.hidden_lines,
        )
        _frame(parent, parent_view.direction, parent_view.up)
        parent_projection = project_view(shape, parent_view)
        selected: list[Curve] = []
        for curve in parent_projection.curves:
            selected.extend(_clip_curve_to_circle(curve, center, radius))
        if not selected:
            raise ValueError("Detail crop selected no projected curves inside its circle.")
        selected_curves = _deduplicate_curves(selected)
        selected.append(Curve("circle", False, center=center, radius=radius))
        clipped = _deduplicate_curves(selected)
        base = replace(
            parent_projection,
            name=_name(normalized),
            curves=clipped,
            bounds=ProjectedBounds(
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ),
            visible_count=sum(not curve.hidden for curve in selected_curves),
            hidden_count=sum(curve.hidden for curve in selected_curves),
            metadata={
                "kind": "detail",
                "parent": parent,
                "center": center,
                "radius": radius,
                "scale": multiplier,
            },
        )
        return _scale_projection(base, multiplier, center)

    multiplier = _number(normalized.scale, "scale", 1.0)
    if multiplier <= 0:
        raise ValueError("Drawing view scale must be a positive finite multiplier.")
    projection = project_view(shape, normalized)
    return _scale_projection(
        projection,
        multiplier,
        metadata={"kind": "orthographic", "scale": multiplier},
    )


__all__ = ["SectionPlaneTrace", "project_drawing_view", "section_plane_trace"]
