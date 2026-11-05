"""Structural context access and exact OCCT hidden-line projection."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from collections.abc import Mapping
from typing import Any


UNIT_TO_MM = {"mm": 1.0, "in": 25.4}
GEOMETRY_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Bounds3D:
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float

    @property
    def size(self) -> tuple[float, float, float]:
        return (
            self.xmax - self.xmin,
            self.ymax - self.ymin,
            self.zmax - self.zmin,
        )


@dataclass(frozen=True)
class Hole:
    index: int
    x: float
    y: float
    radius: float
    face_indices: tuple[int, ...]
    z_span: tuple[float, float]
    counterbore_diameter: float | None = None
    counterbore_depth: float | None = None


@dataclass(frozen=True)
class Curve:
    kind: str
    hidden: bool
    points: tuple[tuple[float, float], ...] = ()
    center: tuple[float, float] | None = None
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    ccw: bool = True
    layer: str | None = None


@dataclass(frozen=True)
class ProjectedBounds:
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

    @property
    def center(self) -> tuple[float, float]:
        return ((self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2)


@dataclass(frozen=True)
class ProjectedView:
    name: str
    direction: tuple[float, float, float]
    right: tuple[float, float, float]
    up: tuple[float, float, float]
    curves: tuple[Curve, ...]
    bounds: ProjectedBounds
    visible_count: int
    hidden_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)


def context_value(ctx: Any, name: str, default: Any = None) -> Any:
    if isinstance(ctx, Mapping):
        return ctx.get(name, default)
    return getattr(ctx, name, default)


def context_shape(ctx: Any) -> Any:
    for name in ("shape", "part", "solid", "model"):
        candidate = context_value(ctx, name)
        if candidate is not None:
            return candidate
    raise ValueError("Drawing context must expose a build123d shape.")


def context_units(ctx: Any) -> str:
    raw = context_value(ctx, "units", "mm") or "mm"
    if hasattr(raw, "value"):
        raw = raw.value
    units = str(raw).strip().lower()
    aliases = {"millimeter": "mm", "millimeters": "mm", "inch": "in", "inches": "in"}
    units = aliases.get(units, units)
    if units not in UNIT_TO_MM:
        raise ValueError(f"Drawing units must be mm or in, got {raw!r}.")
    return units


def wrapped_shape(shape: Any) -> Any:
    return getattr(shape, "wrapped", shape)


def shape_bounds(shape: Any) -> Bounds3D:
    bbox_method = getattr(shape, "bounding_box", None)
    if callable(bbox_method):
        bbox = bbox_method()
        low = tuple(float(value) for value in bbox.min)
        high = tuple(float(value) for value in bbox.max)
        return Bounds3D(low[0], low[1], low[2], high[0], high[1], high[2])

    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bbox = Bnd_Box()
    BRepBndLib.Add_s(wrapped_shape(shape), bbox)
    # OCP 8 exposes Bnd_Box::Get() as an unregistered C++ Limits type on
    # Python 3.12. CornerMin/CornerMax remain wrapped gp_Pnt values.
    low, high = bbox.CornerMin(), bbox.CornerMax()
    xmin, ymin, zmin = low.X(), low.Y(), low.Z()
    xmax, ymax, zmax = high.X(), high.Y(), high.Z()
    return Bounds3D(xmin, ymin, zmin, xmax, ymax, zmax)


def through_holes(shape: Any, bounds: Bounds3D) -> list[Hole]:
    """Find Z bores whose full cylindrical clearance is free through the model.

    A kernel common with an inset bore cylinder distinguishes through holes
    from blind holes and counterbores, without requiring unrelated bosses to
    share the plate faces.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_REVERSED

    faces_method = getattr(shape, "faces", None)
    if not callable(faces_method):
        return []

    tolerance = max(max(bounds.size), 1.0) * 1e-7
    candidates: dict[tuple[int, int, int], dict[str, Any]] = {}
    for face_index, face in enumerate(faces_method()):
        face_shape = getattr(face, "wrapped", face)
        if face_shape.Orientation() != TopAbs_REVERSED:
            continue
        adaptor = BRepAdaptor_Surface(face_shape)
        if adaptor.GetType() != GeomAbs_Cylinder:
            continue

        cylinder = adaptor.Cylinder()
        axis = cylinder.Axis()
        direction = axis.Direction()
        if abs(abs(direction.Z()) - 1.0) > 1e-6:
            continue

        origin = axis.Location()
        first_v = adaptor.FirstVParameter()
        last_v = adaptor.LastVParameter()
        z_values = (
            origin.Z() + direction.Z() * first_v,
            origin.Z() + direction.Z() * last_v,
        )
        z_low, z_high = min(z_values), max(z_values)
        if z_high - z_low <= tolerance:
            continue

        radius = float(cylinder.Radius())
        x, y = float(origin.X()), float(origin.Y())
        if (
            x - radius <= bounds.xmin + tolerance
            or x + radius >= bounds.xmax - tolerance
            or y - radius <= bounds.ymin + tolerance
            or y + radius >= bounds.ymax - tolerance
        ):
            continue

        key = (
            round(x / max(tolerance, 1e-12)),
            round(y / max(tolerance, 1e-12)),
            round(radius / max(tolerance, 1e-12)),
        )
        previous = candidates.setdefault(
            key,
            {"x": x, "y": y, "radius": radius, "faces": [], "z": (z_low, z_high)},
        )
        previous["faces"].append(face_index)
        previous["z"] = (
            min(previous["z"][0], z_low),
            max(previous["z"][1], z_high),
        )

    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    from .features import measure_cylinders
    cylindrical_surfaces=measure_cylinders(shape)
    through = []
    for candidate in candidates.values():
        matching=[feature for feature in cylindrical_surfaces if feature.interior
                  and abs(feature.axis[2]-1)<1e-7
                  and math.dist(feature.axis_origin[:2],(candidate["x"],candidate["y"]))<tolerance
                  and abs(feature.radius-candidate["radius"])<tolerance]
        if not matching or not all(feature.full_circle for feature in matching):
            continue
        radius = candidate["radius"] - 2 * tolerance
        if radius <= tolerance:
            continue
        probe = BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(candidate["x"], candidate["y"], bounds.zmin - tolerance), gp_Dir(0, 0, 1)),
            radius, bounds.size[2] + 2 * tolerance,
        ).Shape()
        common = BRepAlgoAPI_Common(wrapped_shape(shape), probe)
        common.Build()
        if not common.IsDone():
            raise ValueError("The kernel could not verify a cylindrical hole clearance.")
        if not TopExp_Explorer(common.Shape(), TopAbs_SOLID).More():
            through.append(candidate)
    ordered = sorted(through, key=lambda hole: (hole["y"], hole["x"], hole["radius"]))
    return [
        Hole(
            index=index,
            x=hole["x"],
            y=hole["y"],
            radius=hole["radius"],
            face_indices=tuple(hole["faces"]),
            z_span=hole["z"],
            counterbore_diameter=next((2.0 * feature.radius for feature in cylindrical_surfaces
                if feature.interior and feature.full_circle and abs(feature.axis[2] - 1.0) < 1e-7
                and math.dist(feature.axis_origin[:2], (hole["x"], hole["y"])) < tolerance
                and feature.radius > hole["radius"] + tolerance
                and min(abs(feature.station_min - hole["z"][1]), abs(feature.station_max - hole["z"][0])) < tolerance * 10), None),
            counterbore_depth=next((feature.length for feature in cylindrical_surfaces
                if feature.interior and feature.full_circle and abs(feature.axis[2] - 1.0) < 1e-7
                and math.dist(feature.axis_origin[:2], (hole["x"], hole["y"])) < tolerance
                and feature.radius > hole["radius"] + tolerance
                and min(abs(feature.station_min - hole["z"][1]), abs(feature.station_max - hole["z"][0])) < tolerance * 10), None),
        )
        for index, hole in enumerate(ordered)
    ]


_STANDARD_VIEWS = {
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "iso": ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0)),
    "isometric": ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0)),
}


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(_dot(vector, vector))
    if length <= 1e-12:
        raise ValueError("A drawing view direction or up vector cannot be zero.")
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _projected_xy(
    point: Any,
    right: tuple[float, float, float],
    up: tuple[float, float, float],
) -> tuple[float, float]:
    xyz = (float(point.X()), float(point.Y()), float(point.Z()))
    return (_dot(xyz, right), _dot(xyz, up))


def project_point(
    point: tuple[float, float, float],
    view: ProjectedView,
) -> tuple[float, float]:
    return (_dot(point, view.right), _dot(point, view.up))


def _curve_from_edge(edge: Any, hidden: bool) -> Curve | None:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import (
        GeomAbs_Circle,
        GeomAbs_Line,
    )

    adaptor = BRepAdaptor_Curve(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    if not math.isfinite(first) or not math.isfinite(last) or last - first <= 1e-12:
        return None

    curve_type = adaptor.GetType()
    if curve_type == GeomAbs_Line:
        start = adaptor.Value(first)
        end = adaptor.Value(last)
        a = (float(start.X()), float(start.Y()))
        b = (float(end.X()), float(end.Y()))
        if math.dist(a, b) <= 1e-9:
            return None
        return Curve("line", hidden, points=(a, b))

    if curve_type == GeomAbs_Circle:
        circle = adaptor.Circle()
        center3 = circle.Location()
        center = (float(center3.X()), float(center3.Y()))
        radius = float(circle.Radius())
        span = abs(last - first)
        if span >= (2 * math.pi - 1e-7):
            return Curve("circle", hidden, center=center, radius=radius)
        start = adaptor.Value(first)
        end = adaptor.Value(last)
        start_angle = math.degrees(
            math.atan2(start.Y() - center[1], start.X() - center[0])
        ) % 360
        end_angle = math.degrees(
            math.atan2(end.Y() - center[1], end.X() - center[0])
        ) % 360
        axis_z = circle.Axis().Direction().Z()
        return Curve(
            "arc",
            hidden,
            center=center,
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
            ccw=axis_z >= 0,
        )

    steps = 16
    if curve_type == GeomAbs_Circle:
        steps = max(8, min(64, int(abs(last - first) / (2 * math.pi) * 32)))
    points3 = [adaptor.Value(first + (last - first) * i / steps) for i in range(steps + 1)]
    points = tuple(
        (float(point.X()), float(point.Y()))
        for point in points3
    )
    if len(points) < 2:
        return None

    # OCCT can represent an edge-on projected circle as a very flat spline.
    # Emit a line when its sampled points are collinear within model tolerance.
    first_point, last_point = points[0], points[-1]
    dx, dy = last_point[0] - first_point[0], last_point[1] - first_point[1]
    length = math.hypot(dx, dy)
    if length > 1e-12:
        max_distance = max(
            abs(dx * (point[1] - first_point[1]) - dy * (point[0] - first_point[0])) / length
            for point in points
        )
        if max_distance <= 1e-5:
            if math.dist(first_point, last_point) <= 1e-9:
                return None
            return Curve("line", hidden, points=(first_point, last_point))
    return Curve("polyline", hidden, points=points)


def _curve_key(curve: Curve) -> tuple[Any, ...]:
    rounded = lambda value: round(float(value), 7)
    if curve.kind == "circle":
        return ("circle", rounded(curve.center[0]), rounded(curve.center[1]), rounded(curve.radius))
    if curve.kind == "arc":
        start = (rounded(curve.start_angle % 360), rounded(curve.end_angle % 360))
        if not curve.ccw:
            start = tuple(reversed(start))
        return (
            "arc",
            rounded(curve.center[0]),
            rounded(curve.center[1]),
            rounded(curve.radius),
            start,
        )
    points = tuple((rounded(x), rounded(y)) for x, y in curve.points)
    if curve.kind == "line":
        points = tuple(sorted(points))
    else:
        reverse = tuple(reversed(points))
        points = min(points, reverse)
    return (curve.kind, points)


def _extract_curves(output: Any, hidden: bool) -> list[Curve]:
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    method_names = (
        ("HCompound", "Rg1LineHCompound", "RgNLineHCompound", "OutLineHCompound")
        if hidden
        else ("VCompound", "Rg1LineVCompound", "RgNLineVCompound", "OutLineVCompound")
    )
    curves: list[Curve] = []
    for name in method_names:
        compound = getattr(output, name)()
        explorer = TopExp_Explorer(compound, TopAbs_EDGE)
        while explorer.More():
            edge = TopoDS.Edge(explorer.Current())
            curve = _curve_from_edge(edge, hidden)
            if curve is not None:
                curves.append(curve)
            explorer.Next()
    return curves


def _curve_points(curve: Curve) -> list[tuple[float, float]]:
    if curve.kind in ("line", "polyline"):
        return list(curve.points)
    if curve.kind == "circle":
        cx, cy = curve.center
        r = curve.radius
        return [(cx + r * math.cos(i * math.pi / 16), cy + r * math.sin(i * math.pi / 16)) for i in range(32)]
    cx, cy = curve.center
    radius = curve.radius
    start, end = curve.start_angle, curve.end_angle
    if curve.ccw:
        sweep = (end - start) % 360
    else:
        sweep = -((start - end) % 360)
    steps = max(4, min(64, int(abs(sweep) / 6) + 1))
    return [
        (
            cx + radius * math.cos(math.radians(start + sweep * index / steps)),
            cy + radius * math.sin(math.radians(start + sweep * index / steps)),
        )
        for index in range(steps + 1)
    ]


def _bounds_of_curves(curves: list[Curve]) -> ProjectedBounds:
    points = [point for curve in curves for point in _curve_points(curve)]
    if not points:
        raise ValueError("OCCT produced no projected edges for the requested view.")
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return ProjectedBounds(min(xs), min(ys), max(xs), max(ys))


def project_view(shape: Any, view: Any) -> ProjectedView:
    name = str(getattr(view, "name", "front")).strip().lower()
    name = {"isometric": "iso"}.get(name, name)
    default_direction, default_up = _STANDARD_VIEWS.get(
        name, ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0))
    )
    direction = _unit(tuple(float(value) for value in (getattr(view, "direction", None) or default_direction)))
    up_hint = _unit(tuple(float(value) for value in (getattr(view, "up", None) or default_up)))
    right_raw = _cross(up_hint, direction)
    if math.sqrt(_dot(right_raw, right_raw)) <= 1e-8:
        fallback = (0.0, 1.0, 0.0) if abs(direction[1]) < 0.9 else (1.0, 0.0, 0.0)
        right_raw = _cross(fallback, direction)
    right = _unit(right_raw)
    up = _unit(_cross(direction, right))

    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    algorithm = HLRBRep_Algo()
    algorithm.Add(wrapped_shape(shape))
    projector = HLRAlgo_Projector(
        gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*direction), gp_Dir(*right))
    )
    algorithm.Projector(projector)
    algorithm.Update()
    algorithm.Hide()
    output = HLRBRep_HLRToShape(algorithm)
    curves = _extract_curves(output, hidden=False)
    if bool(getattr(view, "hidden_lines", True)):
        curves.extend(_extract_curves(output, hidden=True))

    unique: dict[tuple[Any, ...], Curve] = {}
    for curve in curves:
        key = _curve_key(curve)
        previous = unique.get(key)
        if previous is None or (previous.hidden and not curve.hidden):
            unique[key] = curve
    final_curves = tuple(sorted(unique.values(), key=lambda curve: (_curve_key(curve), curve.hidden)))
    bounds = _bounds_of_curves(list(final_curves))
    return ProjectedView(
        name=name,
        direction=direction,
        right=right,
        up=up,
        curves=final_curves,
        bounds=bounds,
        visible_count=sum(not curve.hidden for curve in final_curves),
        hidden_count=sum(curve.hidden for curve in final_curves),
    )
