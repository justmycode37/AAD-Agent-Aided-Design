"""Independent, host-side original-request checks for the S5 pilot.

This module reads saved BRep, native Sheet2D JSON, SVG and final-response text.
It never imports ``aad_quality``, executes model source, consumes AAD's returned
measurements, or treats a model finish receipt as evidence.  The artifact checks
are deliberately bounded; unsupported evidence is recorded as unsupported.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree


MM_PER_INCH = 25.4
_SVG_NS = "http://www.w3.org/2000/svg"
_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_SUPPORTED = "pass"
_FAILED = "fail"
_UNSUPPORTED = "unsupported"


class UnsupportedEvidence(RuntimeError):
    """Raised when an artifact format or geometric feature is not audited."""


class MalformedArtifact(ValueError):
    """Raised when the submitted artifact cannot represent valid evidence."""


@dataclass(frozen=True)
class CheckObservation:
    check_id: str
    check_type: str
    status: str
    coverage: str
    reason: str
    expected: Any = None
    actual: Any = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StageAudit:
    task_id: str
    stage_id: str
    expected_outcome: str
    declared_coverage: str
    effective_coverage: str
    verdict: str
    checks: tuple[CheckObservation, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "stage_id": self.stage_id,
            "expected_outcome": self.expected_outcome,
            "declared_coverage": self.declared_coverage,
            "effective_coverage": self.effective_coverage,
            "verdict": self.verdict,
            "checks": [item.as_dict() for item in self.checks],
        }


@dataclass(frozen=True)
class TaskAudit:
    task_id: str
    verdict: str
    coverage: str
    stages: tuple[StageAudit, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verdict": self.verdict,
            "coverage": self.coverage,
            "stages": [item.as_dict() for item in self.stages],
        }


@dataclass(frozen=True)
class _Cylinder:
    radius: float
    axis: tuple[float, float, float]
    line_point: tuple[float, float, float]

    @property
    def centerline_point(self) -> tuple[float, float, float]:
        along = _dot(self.line_point, self.axis)
        return _sub(self.line_point, _scale(self.axis, along))


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b, strict=True))


def _sub(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return tuple(float(x) - float(y) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def _add(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return tuple(float(x) + float(y) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def _scale(a: Sequence[float], value: float) -> tuple[float, float, float]:
    return tuple(float(x) * float(value) for x in a)  # type: ignore[return-value]


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Sequence[float]) -> tuple[float, float, float]:
    length = _norm(a)
    if length <= 1e-12:
        raise MalformedArtifact("zero-length direction vector")
    return _scale(a, 1 / length)


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (
        float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
        float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
        float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]),
    )


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise MalformedArtifact(f"{label} must be a finite number")
    return float(value)


def _in_range(value: float, bounds: Sequence[float]) -> bool:
    return len(bounds) == 2 and float(bounds[0]) - 1e-7 <= value <= float(bounds[1]) + 1e-7


def _close(value: float, target: float, tolerance: float) -> bool:
    return abs(value - target) <= tolerance + 1e-7


class BRepAudit:
    """Exact, read-only OCCT measurements of one saved BRep file."""

    def __init__(self, path: Path):
        if not path.is_file():
            raise MalformedArtifact("BRep file is missing")
        if path.stat().st_size <= 0:
            raise MalformedArtifact("BRep file is empty")
        try:
            from build123d import import_brep
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            from OCP.BRepBndLib import BRepBndLib
            from OCP.BRepClass3d import BRepClass3d_SolidClassifier
            from OCP.Bnd import Bnd_Box
            from OCP.GeomAbs import GeomAbs_Cylinder
            from OCP.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_ON, TopAbs_SOLID
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS
            from OCP.gp import gp_Pnt
        except ImportError as error:
            raise UnsupportedEvidence("build123d/OCCT is unavailable in the audit environment") from error

        try:
            self.shape = import_brep(str(path))
            if self.shape is None or not hasattr(self.shape, "wrapped"):
                raise ValueError("BRep importer returned no topological shape")
            box = Bnd_Box()
            BRepBndLib.AddOptimal_s(self.shape.wrapped, box, False, False)
            lower, upper = box.CornerMin(), box.CornerMax()
            self.bounds = (
                (float(lower.X()), float(upper.X())),
                (float(lower.Y()), float(upper.Y())),
                (float(lower.Z()), float(upper.Z())),
            )
            self.size = tuple(hi - lo for lo, hi in self.bounds)

            self.solids = []
            explorer = TopExp_Explorer(self.shape.wrapped, TopAbs_SOLID)
            while explorer.More():
                self.solids.append(explorer.Current())
                explorer.Next()
            if not self.solids:
                raise ValueError("BRep contains no solids")

            cylinders: list[_Cylinder] = []
            for solid in self.solids:
                faces = TopExp_Explorer(solid, TopAbs_FACE)
                while faces.More():
                    face = TopoDS.Face(faces.Current())
                    surface = BRepAdaptor_Surface(face, True)
                    if surface.GetType() == GeomAbs_Cylinder:
                        cylinder = surface.Cylinder()
                        axis = cylinder.Axis()
                        point = axis.Location()
                        direction = axis.Direction()
                        cylinders.append(_Cylinder(
                            radius=float(cylinder.Radius()),
                            axis=_unit((direction.X(), direction.Y(), direction.Z())),
                            line_point=(float(point.X()), float(point.Y()), float(point.Z())),
                        ))
                    faces.Next()
            self.cylinders = tuple(cylinders)
            self._classifier = BRepClass3d_SolidClassifier
            self._gp_point = gp_Pnt
            self._topabs_in = TopAbs_IN
            self._topabs_on = TopAbs_ON
        except UnsupportedEvidence:
            raise
        except Exception as error:
            raise MalformedArtifact(f"BRep import or topology measurement failed: {type(error).__name__}: {error}") from error

    def point_state(self, point: Sequence[float], tolerance: float = 1e-7) -> str:
        states = [
            self._classifier(solid, self._gp_point(*map(float, point)), tolerance).State()
            for solid in self.solids
        ]
        if any(state == self._topabs_in for state in states):
            return "solid"
        if any(state == self._topabs_on for state in states):
            return "boundary"
        return "void"

    def component_bounds(self) -> list[tuple[tuple[float, float], ...]]:
        try:
            from OCP.BRepBndLib import BRepBndLib
            from OCP.Bnd import Bnd_Box
        except ImportError as error:
            raise UnsupportedEvidence("OCCT bounds API is unavailable") from error
        result = []
        for solid in self.solids:
            box = Bnd_Box()
            BRepBndLib.AddOptimal_s(solid, box, False, False)
            lower, upper = box.CornerMin(), box.CornerMax()
            result.append((
                (float(lower.X()), float(upper.X())),
                (float(lower.Y()), float(upper.Y())),
                (float(lower.Z()), float(upper.Z())),
            ))
        return result

    def cylinders_on_axis(
        self,
        axis: Sequence[float],
        center: Sequence[float],
        *,
        radius: float | None = None,
        radius_tolerance: float = 0.1,
        axis_tolerance: float = 0.15,
    ) -> list[_Cylinder]:
        expected_axis = _unit(axis)
        matching = []
        for cylinder in self.cylinders:
            if abs(_dot(cylinder.axis, expected_axis)) < 1.0 - 1e-5:
                continue
            offset = _sub(cylinder.centerline_point, center)
            perpendicular = _sub(offset, _scale(expected_axis, _dot(offset, expected_axis)))
            if _norm(perpendicular) > axis_tolerance:
                continue
            if radius is not None and abs(cylinder.radius - radius) > radius_tolerance:
                continue
            matching.append(cylinder)
        return matching


def _point_at_axis(center: Sequence[float], axis: Sequence[float], offset: float) -> tuple[float, float, float]:
    return _add(center, _scale(axis, offset))


def _vector_perpendicular(axis: Sequence[float]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    direction = _unit(axis)
    seed = (1.0, 0.0, 0.0) if abs(direction[0]) < 0.8 else (0.0, 1.0, 0.0)
    first = _unit(_sub(seed, _scale(direction, _dot(seed, direction))))
    return first, _unit(_cross(direction, first))


def _point_material(brep: BRepAudit, point: Sequence[float], expected: str) -> tuple[bool, str]:
    actual = brep.point_state(point)
    return actual == expected, actual


def _check_bbox(check: Mapping[str, Any], brep: BRepAudit) -> tuple[bool, Any]:
    expected = check["ranges"]
    actual = list(brep.size)
    return all(_in_range(value, bounds) for value, bounds in zip(actual, expected, strict=True)), actual


def _check_solids(check: Mapping[str, Any], brep: BRepAudit) -> tuple[bool, int]:
    return len(brep.solids) == check["equals"], len(brep.solids)


def _check_point(check: Mapping[str, Any], brep: BRepAudit) -> tuple[bool, str]:
    actual = brep.point_state(check["point_mm"])
    return actual == check["equals"], actual


def _check_bores(check: Mapping[str, Any], brep: BRepAudit) -> tuple[bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    all_pass = True
    for index, bore in enumerate(check["bores"]):
        axis = _unit(bore["axis_mm"])
        center = tuple(float(item) for item in bore["center_mm"])
        diameter_bounds = bore["diameter_mm"]
        radius_bounds = [float(value) / 2 for value in diameter_bounds]
        radius = sum(radius_bounds) / 2
        tolerance = float(bore.get("center_tolerance_mm", 0.1))
        candidates = [
            item for item in brep.cylinders_on_axis(
                axis, center, radius=radius,
                radius_tolerance=max(radius - radius_bounds[0], radius_bounds[1] - radius) + 1e-5,
                axis_tolerance=tolerance,
            )
        ]
        if not candidates:
            results.append({"index": index, "status": "fail", "reason": "no matching cylindrical surface"})
            all_pass = False
            continue

        depth = sum(float(value) for value in bore["depth_mm"]) / 2
        offsets = [((i / 10) - 0.5) * depth for i in range(1, 10)]
        centerline = [brep.point_state(_point_at_axis(center, axis, offset)) for offset in offsets]
        if any(value != "void" for value in centerline):
            results.append({"index": index, "status": "fail", "reason": "requested bore axis is not void through its stated depth", "centerline": centerline})
            all_pass = False
            continue

        if bore["through"]:
            # A cylindrical wall plus an uninterrupted void centerline is a
            # bounded through-bore witness; it does not certify the whole part.
            result = {"index": index, "status": "pass", "matched_radii_mm": sorted({round(c.radius, 6) for c in candidates})}
        else:
            # For a blind bore, probe between the target wall and any smaller
            # coaxial bore at its floor. A through cut at this radius must fail.
            first, second = _vector_perpendicular(axis)
            radial = radius * 0.72
            lower_inside = _point_at_axis(center, axis, -depth / 2 + max(0.03, tolerance / 2))
            lower_outside = _point_at_axis(center, axis, -depth / 2 - max(0.03, tolerance / 2))
            inside_states = [
                brep.point_state(_add(lower_inside, _scale(direction, radial)))
                for direction in (first, _scale(first, -1), second, _scale(second, -1))
            ]
            outside_states = [
                brep.point_state(_add(lower_outside, _scale(direction, radial)))
                for direction in (first, _scale(first, -1), second, _scale(second, -1))
            ]
            if not any(state == "void" for state in inside_states) or not any(state == "solid" for state in outside_states):
                results.append({"index": index, "status": "fail", "reason": "blind-bore floor depth is not independently visible", "floor_inside": inside_states, "floor_outside": outside_states})
                all_pass = False
                continue
            result = {"index": index, "status": "pass", "matched_radii_mm": sorted({round(c.radius, 6) for c in candidates}), "floor_outside": outside_states}
        results.append(result)
    return all_pass, results


def _check_component_layout(check: Mapping[str, Any], brep: BRepAudit) -> tuple[bool, Any]:
    expected = [part["ranges_mm"] for part in check["parts"]]
    actual = [list(map(list, item)) for item in brep.component_bounds()]
    tolerance = float(check["tolerance_mm"])
    if len(expected) != len(actual):
        return False, {"actual_component_count": len(actual), "components": actual}

    order = sorted(range(len(expected)), key=lambda i: tuple(expected[i][axis][0] for axis in range(3)))

    def assign(position: int, remaining: set[int]) -> bool:
        if position == len(order):
            return True
        target = expected[order[position]]
        for candidate in sorted(remaining):
            measured = actual[candidate]
            if all(
                abs(float(target[axis][bound]) - float(measured[axis][bound])) <= tolerance + 1e-7
                for axis in range(3)
                for bound in range(2)
            ) and assign(position + 1, remaining - {candidate}):
                return True
        return False

    return assign(0, set(range(len(actual)))), {"expected": expected, "actual": actual}


def _ring_runs(values: Sequence[bool]) -> list[tuple[float, float]]:
    """Return (center angle, width angle) for circular runs of solid samples."""
    count = len(values)
    if not count or all(values) or not any(values):
        return []
    step = 2 * math.pi / count
    start_false = next(index for index, value in enumerate(values) if not value)
    runs: list[tuple[float, float]] = []
    current_start: int | None = None
    current_length = 0
    for offset in range(1, count + 1):
        index = (start_false + offset) % count
        if values[index]:
            if current_start is None:
                current_start = index
                current_length = 1
            else:
                current_length += 1
        elif current_start is not None:
            center_index = (current_start + (current_length - 1) / 2) % count
            runs.append((center_index * step, current_length * step))
            current_start = None
            current_length = 0
    if current_start is not None:
        center_index = (current_start + (current_length - 1) / 2) % count
        runs.append((center_index * step, current_length * step))
    return runs


def _angular_distance(a: float, b: float) -> float:
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def _check_rotational_pattern(check: Mapping[str, Any], brep: BRepAudit) -> tuple[bool, Any]:
    axis = _unit(check["axis_mm"])
    center = tuple(float(item) for item in check["center_mm"])
    radii = check["pitch_radius_mm"]
    radius = sum(float(item) for item in radii) / 2
    tolerance = float(check["tolerance_mm"])
    first, second = _vector_perpendicular(axis)
    expected_angles = []
    for point in check["feature_centers_mm"]:
        offset = _sub(point, center)
        radial = _sub(offset, _scale(axis, _dot(offset, axis)))
        observed_radius = _norm(radial)
        if not _in_range(observed_radius, radii):
            return False, {"reason": "declared feature witness is outside its pitch-radius bounds", "radius_mm": observed_radius}
        expected_angles.append(math.atan2(_dot(radial, second), _dot(radial, first)) % (2 * math.pi))
        if brep.point_state(_point_at_axis(_add(center, radial), axis, _dot(offset, axis))) != "solid":
            return False, {"reason": "a requested pattern witness is not material", "point_mm": list(point)}

    samples = 720
    occupied = []
    for index in range(samples):
        angle = 2 * math.pi * index / samples
        radial = _add(_scale(first, radius * math.cos(angle)), _scale(second, radius * math.sin(angle)))
        occupied.append(brep.point_state(_add(center, radial)) in {"solid", "boundary"})
    runs = _ring_runs(occupied)
    expected_count = int(check["count"])
    angular_tolerance = max(math.radians(1.0), 2 * math.asin(min(0.99, tolerance / max(radius, 1e-9))) + math.radians(0.5))
    if len(runs) != expected_count:
        return False, {"expected_count": expected_count, "measured_count": len(runs), "ring_radius_mm": radius, "runs": runs}
    unused = list(range(len(runs)))
    for expected_angle in expected_angles:
        if not unused:
            return False, {"reason": "pattern features cannot be matched one-to-one", "runs": runs}
        best = min(unused, key=lambda index: _angular_distance(expected_angle, runs[index][0]))
        distance = _angular_distance(expected_angle, runs[best][0])
        if distance > angular_tolerance:
            return False, {"reason": "pattern feature angular position is outside tolerance", "angle_error_rad": distance, "runs": runs}
        unused.remove(best)
    return True, {"count": len(runs), "ring_radius_mm": radius, "angular_centers_rad": [item[0] for item in runs]}


def _shape_corners(bounds: Sequence[Sequence[float]]) -> list[tuple[float, float, float]]:
    return [
        (x, y, z)
        for x in bounds[0]
        for y in bounds[1]
        for z in bounds[2]
    ]


def _line_runs(brep: BRepAudit, axis: Sequence[float], base: Sequence[float], low: float, high: float) -> list[tuple[float, float]]:
    length = high - low
    samples = max(2, math.ceil(length / 0.25))
    if samples > 6000:
        raise UnsupportedEvidence("linear-pattern scan exceeds the 6000-point safety bound")
    values = [low + length * index / samples for index in range(samples + 1)]
    states = [
        brep.point_state(_add(base, _scale(axis, value))) in {"solid", "boundary"}
        for value in values
    ]

    def boundary(left: float, right: float, left_solid: bool) -> float:
        for _ in range(24):
            middle = (left + right) / 2
            inside = brep.point_state(_add(base, _scale(axis, middle))) in {"solid", "boundary"}
            if inside == left_solid:
                left = middle
            else:
                right = middle
        return (left + right) / 2

    intervals: list[tuple[float, float]] = []
    current_start = low if states[0] else None
    for index in range(samples):
        if states[index] == states[index + 1]:
            continue
        transition = boundary(values[index], values[index + 1], states[index])
        if states[index]:
            intervals.append((float(current_start if current_start is not None else low), transition))
            current_start = None
        else:
            current_start = transition
    if current_start is not None:
        intervals.append((current_start, high))
    return [(start, end) for start, end in intervals if end - start > 1e-3]


def _check_linear_pattern(check: Mapping[str, Any], brep: BRepAudit) -> tuple[bool, Any]:
    if check.get("feature_kind") != "solid":
        raise UnsupportedEvidence("only solid linear patterns are implemented")
    axis = _unit(check["axis_mm"])
    first_center = tuple(float(item) for item in check["feature_centers_mm"][0])
    base = _sub(first_center, _scale(axis, _dot(first_center, axis)))
    corners = _shape_corners(brep.bounds)
    projected = [_dot(corner, axis) for corner in corners]
    actual = _line_runs(brep, axis, base, min(projected), max(projected))
    expected_centers = sorted(_dot(point, axis) for point in check["feature_centers_mm"])
    width_range = check["feature_width_mm"]
    tolerance = float(check["tolerance_mm"])
    actual_centers = [(low + high) / 2 for low, high in actual]
    actual_widths = [high - low for low, high in actual]
    if len(actual) != len(expected_centers):
        return False, {"expected_count": len(expected_centers), "measured_count": len(actual), "intervals_mm": actual}
    assignments = sorted(zip(expected_centers, actual_centers, actual_widths, strict=True))
    for expected, measured, width in assignments:
        if abs(expected - measured) > tolerance + 1e-6 or not _in_range(width, width_range):
            return False, {"expected_centers_mm": expected_centers, "measured_centers_mm": actual_centers, "measured_widths_mm": actual_widths}
    return True, {"centers_mm": actual_centers, "widths_mm": actual_widths}


def _sheet_entities(sheet_path: Path) -> tuple[dict[str, tuple[float, float]], list[dict[str, Any]], set[str], float]:
    if not sheet_path.is_file():
        raise MalformedArtifact("native 2D sheet JSON is missing")
    if sheet_path.stat().st_size <= 0 or sheet_path.stat().st_size > 32 * 1024 * 1024:
        raise MalformedArtifact("native 2D sheet JSON is empty or exceeds the 32 MiB audit limit")
    try:
        raw = json.loads(sheet_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MalformedArtifact(f"native 2D sheet JSON cannot be parsed: {error}") from error
    if not isinstance(raw, Mapping) or not isinstance(raw.get("points"), Mapping) or not isinstance(raw.get("entities"), Mapping):
        raise MalformedArtifact("native 2D sheet needs point and entity mappings")
    units = str(raw.get("units", "")).strip().casefold()
    if units not in {"mm", "in"}:
        raise MalformedArtifact("native 2D sheet units must be mm or in")
    if len(raw["points"]) > 50000 or len(raw["entities"]) > 50000:
        raise MalformedArtifact("native 2D sheet exceeds the 50000-point/entity audit limit")
    factor = MM_PER_INCH if units == "in" else 1.0
    points: dict[str, tuple[float, float]] = {}
    for name, item in raw["points"].items():
        if not isinstance(name, str) or not isinstance(item, Mapping):
            raise MalformedArtifact("native 2D point record is malformed")
        point = (
            _finite(item.get("x"), f"point {name}.x") * factor,
            _finite(item.get("y"), f"point {name}.y") * factor,
        )
        if any(abs(coordinate) > 1_000_000 for coordinate in point):
            raise MalformedArtifact(f"point {name!r} exceeds the 1000000 mm audit bound")
        points[name] = point
    entities = []
    for name, item in raw["entities"].items():
        if not isinstance(name, str) or not isinstance(item, Mapping):
            raise MalformedArtifact("native 2D entity record is malformed")
        names = item.get("points", [])
        if not isinstance(names, list) or any(point_name not in points for point_name in names):
            raise MalformedArtifact(f"native 2D entity {name!r} refers to missing points")
        entities.append({
            **dict(item),
            "name": name,
            "kind": str(item.get("kind", "")).casefold(),
            "layer": str(item.get("layer", "cut")),
            "coords_mm": [points[point_name] for point_name in names],
            "radius_mm": _finite(item["radius"], f"entity {name}.radius") * factor if item.get("radius") is not None else None,
        })
    layers_raw = raw.get("layers", {})
    if not isinstance(layers_raw, Mapping):
        raise MalformedArtifact("native 2D layers must be an object")
    cut_layers = {
        str(name).casefold()
        for name, metadata in layers_raw.items()
        if (isinstance(metadata, Mapping) and str(metadata.get("role", "")).casefold() == "cut")
        or str(name).casefold() == "cut"
    }
    return points, entities, cut_layers, factor


def _arc_point(center: Sequence[float], radius: float, degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    return (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))


def _angle_in_arc(angle: float, start: float, end: float, clockwise: bool) -> bool:
    full = 360.0
    if clockwise:
        sweep = (start - end) % full
        offset = (start - angle) % full
    else:
        sweep = (end - start) % full
        offset = (angle - start) % full
    return offset <= sweep + 1e-7


def _entity_segments(entity: Mapping[str, Any]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    kind = entity["kind"]
    points = entity["coords_mm"]
    if kind == "line":
        if len(points) != 2:
            raise MalformedArtifact("line entity needs two points")
        return [(points[0], points[1])]
    if kind in {"polyline", "rectangle"}:
        if len(points) < 2:
            raise MalformedArtifact(f"{kind} entity has too few points")
        pairs = list(zip(points, points[1:]))
        if entity.get("closed") or kind == "rectangle":
            pairs.append((points[-1], points[0]))
        return pairs
    if kind == "arc":
        if len(points) != 1 or entity.get("radius_mm") is None:
            raise MalformedArtifact("arc entity has incomplete center/radius data")
        start = float(entity.get("start_angle", 0))
        end = float(entity.get("end_angle", 0))
        clockwise = bool((entity.get("metadata") or {}).get("clockwise", False))
        return [(_arc_point(points[0], entity["radius_mm"], start), _arc_point(points[0], entity["radius_mm"], end))]
    if kind in {"circle", "text"}:
        return []
    if kind == "ellipse":
        raise UnsupportedEvidence("elliptical 2D contours are not implemented in this pilot oracle")
    if kind == "bezier":
        raise UnsupportedEvidence("Bezier 2D contours are not implemented in this pilot oracle")
    raise UnsupportedEvidence(f"native 2D entity kind {kind!r} is not implemented")


def _entity_bounds(entity: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    kind = entity["kind"]
    points = list(entity["coords_mm"])
    if kind == "text":
        return None
    if kind == "circle":
        if len(points) != 1 or entity.get("radius_mm") is None:
            raise MalformedArtifact("circle entity has incomplete center/radius data")
        x, y = points[0]
        radius = float(entity["radius_mm"])
        return (x - radius, y - radius, x + radius, y + radius)
    if kind == "arc":
        if len(points) != 1 or entity.get("radius_mm") is None:
            raise MalformedArtifact("arc entity has incomplete center/radius data")
        center, radius = points[0], float(entity["radius_mm"])
        start, end = float(entity.get("start_angle", 0)), float(entity.get("end_angle", 0))
        clockwise = bool((entity.get("metadata") or {}).get("clockwise", False))
        candidates = [start, end, *(angle for angle in (0, 90, 180, 270) if _angle_in_arc(angle, start, end, clockwise))]
        values = [_arc_point(center, radius, angle) for angle in candidates]
        return (min(p[0] for p in values), min(p[1] for p in values), max(p[0] for p in values), max(p[1] for p in values))
    if kind == "ellipse":
        raise UnsupportedEvidence("elliptical 2D extents are not implemented in this pilot oracle")
    if kind == "bezier":
        raise UnsupportedEvidence("Bezier 2D extents are not implemented in this pilot oracle")
    if not points:
        return None
    return (min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points))


def _profile_contours(entities: Sequence[Mapping[str, Any]]) -> tuple[int, bool, list[tuple[tuple[float, float], tuple[float, float]]]]:
    edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    circles = 0
    for entity in entities:
        if entity["kind"] == "circle":
            circles += 1
        edges.extend(_entity_segments(entity))
    endpoint_parent = list(range(2 * len(edges)))

    def find_endpoint(index: int) -> int:
        while endpoint_parent[index] != index:
            endpoint_parent[index] = endpoint_parent[endpoint_parent[index]]
            index = endpoint_parent[index]
        return index

    def union_endpoints(left: int, right: int) -> None:
        root_left, root_right = find_endpoint(left), find_endpoint(right)
        if root_left != root_right:
            endpoint_parent[root_right] = root_left

    # Cluster near-identical endpoints through a spatial hash. The quadratic
    # version made a malformed but size-bounded sheet an avoidable DoS risk.
    cell_size = 1e-6
    buckets: dict[tuple[int, int], list[int]] = {}
    for endpoint_index, point in enumerate(point for edge in edges for point in edge):
        cell = (math.floor(point[0] / cell_size), math.floor(point[1] / cell_size))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for previous in buckets.get((cell[0] + dx, cell[1] + dy), ()):
                    prior_point = edges[previous // 2][previous % 2]
                    if math.dist(point, prior_point) <= cell_size:
                        union_endpoints(endpoint_index, previous)
        buckets.setdefault(cell, []).append(endpoint_index)

    edge_parent = list(range(len(edges)))

    def find_edge(index: int) -> int:
        while edge_parent[index] != index:
            edge_parent[index] = edge_parent[edge_parent[index]]
            index = edge_parent[index]
        return index

    def union_edges(left: int, right: int) -> None:
        root_left, root_right = find_edge(left), find_edge(right)
        if root_left != root_right:
            edge_parent[root_right] = root_left

    degree: dict[int, int] = {}
    incident_edges: dict[int, list[int]] = {}
    for edge_index in range(len(edges)):
        first = find_endpoint(2 * edge_index)
        second = find_endpoint(2 * edge_index + 1)
        degree[first] = degree.get(first, 0) + 1
        degree[second] = degree.get(second, 0) + 1
        incident_edges.setdefault(first, []).append(edge_index)
        incident_edges.setdefault(second, []).append(edge_index)
    for touching in incident_edges.values():
        for edge_index in touching[1:]:
            union_edges(touching[0], edge_index)

    components: set[int] = {find_edge(edge_index) for edge_index in range(len(edges))}
    all_closed = all(value == 2 for value in degree.values())
    return circles + len(components), all_closed, edges


def _segments_match(expected: Sequence[Mapping[str, Any]], actual: Sequence[tuple[tuple[float, float], tuple[float, float]]]) -> tuple[bool, list[dict[str, Any]]]:
    remaining = set(range(len(actual)))
    details = []
    ok = True
    for item in expected:
        start, end = tuple(item["start_mm"]), tuple(item["end_mm"])
        tolerance = float(item["tolerance_mm"])
        matches = [
            index for index in remaining
            if (
                math.dist(actual[index][0], start) <= tolerance + 1e-7
                and math.dist(actual[index][1], end) <= tolerance + 1e-7
            ) or (
                math.dist(actual[index][0], end) <= tolerance + 1e-7
                and math.dist(actual[index][1], start) <= tolerance + 1e-7
            )
        ]
        if not matches:
            ok = False
            details.append({"expected": [start, end], "matched": False})
        else:
            remaining.remove(matches[0])
            details.append({"expected": [start, end], "matched": True, "actual": actual[matches[0]]})
    return ok, details


def _circles_match(expected: Sequence[Mapping[str, Any]], actual: Sequence[Mapping[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    remaining = set(range(len(actual)))
    details = []
    ok = True
    for item in expected:
        center, diameter = item["center_mm"], item["diameter_mm"]
        candidates = [
            index for index in remaining
            if math.dist(actual[index]["center_mm"], center) <= 0.1 + 1e-7
            and _in_range(actual[index]["diameter_mm"], diameter)
        ]
        if not candidates:
            ok = False
            details.append({"expected": item, "matched": False})
        else:
            index = candidates[0]
            remaining.remove(index)
            details.append({"expected": item, "matched": True, "actual": actual[index]})
    return ok, details


def _check_dxf_profile(check: Mapping[str, Any], sheet_path: Path) -> tuple[bool, Any]:
    _, all_entities, cut_layer_names, _ = _sheet_entities(sheet_path)
    named_cut_layers = [str(name) for name in check.get("cut_layers", [])]
    expected_layers = {name.casefold() for name in named_cut_layers}
    actual_cut = [entity for entity in all_entities if entity["layer"].casefold() in expected_layers]
    if expected_layers and (not actual_cut or not expected_layers.issubset(cut_layer_names)):
        return False, {"expected_cut_layers": named_cut_layers, "actual_cut_layers": sorted(cut_layer_names)}

    entities = [entity for entity in actual_cut if entity["kind"] != "text"]
    if not entities:
        return False, {"reason": "no geometric entities on requested cut layers"}

    contour_count, all_closed, segments = _profile_contours(entities)
    actual_circles = [
        {
            "center_mm": list(entity["coords_mm"][0]),
            "diameter_mm": 2 * float(entity["radius_mm"]),
        }
        for entity in entities
        if entity["kind"] == "circle"
    ]
    bounds = [_entity_bounds(entity) for entity in entities]
    if any(item is None for item in bounds):
        raise UnsupportedEvidence("profile contains non-geometric cut entities")
    xmin = min(item[0] for item in bounds if item is not None)
    ymin = min(item[1] for item in bounds if item is not None)
    xmax = max(item[2] for item in bounds if item is not None)
    ymax = max(item[3] for item in bounds if item is not None)
    measured_extents = [xmax - xmin, ymax - ymin]

    checks: dict[str, Any] = {
        "cut_layers": {"expected": named_cut_layers, "actual": sorted(cut_layer_names)},
        "contour_count": {"expected": check.get("contour_count"), "actual": contour_count},
        "closed_contours": {"expected": check.get("closed_contours"), "actual": all_closed},
        "circles": None,
        "extents_mm": {"expected": check.get("extents_mm"), "actual": measured_extents},
        "segments": None,
    }
    ok = True
    if "contour_count" in check:
        ok = ok and contour_count == check["contour_count"]
    if "closed_contours" in check:
        ok = ok and (all_closed is check["closed_contours"])
    if "extents_mm" in check:
        ok = ok and all(_in_range(value, expected) for value, expected in zip(measured_extents, check["extents_mm"], strict=True))
    if "circles" in check:
        circle_ok, circle_details = _circles_match(check["circles"], actual_circles)
        checks["circles"] = circle_details
        ok = ok and circle_ok
    if "segments" in check:
        segment_ok, segment_details = _segments_match(check["segments"], segments)
        checks["segments"] = segment_details
        ok = ok and segment_ok
    return ok, checks


_TOPIC_RULES: dict[str, tuple[tuple[str, ...], ...]] = {
    "pipe standard or measured outside diameter": (
        ("pipe standard", "standard number", "nps", "nominal pipe size", "pipe schedule", "outside diameter", "outside dia", "outer diameter", "pipe od"),
    ),
    "mating-port dimensions and insertion depth": (
        ("mating port", "port", "mating interface", "interface"),
        ("dimensions", "dimension", "size", "diameter", "bore"),
        ("insertion depth", "insertion", "engagement", "depth"),
    ),
    "pressure and temperature range": (
        ("pressure",),
        ("temperature", "thermal"),
        ("range", "operating condition", "operating envelope"),
    ),
    "seal type and permitted leakage": (
        ("seal", "gasket", "o-ring", "oring"),
        ("leakage", "leak", "leak rate", "permitted leak"),
    ),
    "material, process, units, and tolerances": (
        ("material",),
        ("process", "manufacturing method", "manufacture"),
        ("units", "millimetres", "millimeters", "inches"),
        ("tolerances", "tolerance"),
    ),
    "board length, width, thickness, and hole pattern": (
        ("board", "pcb", "circuit board"),
        ("length",),
        ("width",),
        ("thickness",),
        ("hole pattern", "mounting holes", "mounting pattern", "hole spacing"),
    ),
    "connector locations and clearance envelopes": (
        ("connector", "connectors", "port"),
        ("location", "locations", "position", "positions"),
        ("clearance", "envelope", "keepout"),
    ),
    "lid or open-top preference and sealing requirement": (
        ("lid", "cover", "open top", "open-top"),
        ("seal", "sealing", "waterproof", "gasket"),
    ),
    "material, process, and wall limits": (
        ("material",),
        ("process", "manufacturing method", "manufacture"),
        ("wall", "wall thickness", "thickness limit"),
    ),
    "units and tolerances": (
        ("units", "millimetres", "millimeters", "inches"),
        ("tolerances", "tolerance"),
    ),
}


def _has_term(text: str, term: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    candidate = re.sub(r"[^a-z0-9]+", " ", term.casefold()).strip()
    return bool(candidate) and f" {candidate} " in f" {normalized} "


def _mentions_number(text: str, target: float, tolerance: float = 1e-6) -> bool:
    return any(abs(float(value) - target) <= tolerance for value in _NUMBER.findall(text))


def _check_clarification(check: Mapping[str, Any], text: str) -> tuple[bool, Any]:
    response = text.strip()
    if not response:
        return False, {"reason": "no user-facing clarification or infeasibility response was captured"}
    missing_results = []
    for item in check.get("missing", []):
        groups = _TOPIC_RULES.get(item)
        if groups is None:
            raise UnsupportedEvidence(f"clarification topic {item!r} has no reviewed semantic matcher")
        group_results = []
        for group in groups:
            group_results.append(any(_has_term(response, term) for term in group))
        missing_results.append({"topic": item, "pass": all(group_results), "concepts": group_results})

    conflict_results = []
    for item in check.get("conflicts", []):
        if "cartridge" not in item.casefold() or "span" not in item.casefold():
            raise UnsupportedEvidence(f"conflict topic {item!r} has no reviewed semantic matcher")
        lower_value = _mentions_number(response, 109.9)
        upper_value = _mentions_number(response, 105.1)
        incompatibility = any(
            _has_term(response, phrase)
            for phrase in ("exceeds", "greater than", "larger than", "longer than", "cannot fit", "won't fit", "will not fit", "infeasible", "incompatible")
        )
        conflict_results.append({
            "topic": item,
            "pass": lower_value and upper_value and incompatibility,
            "lower_bound_mentioned": lower_value,
            "upper_bound_mentioned": upper_value,
            "conflict_explained": incompatibility,
        })
    details = {"missing": missing_results, "conflicts": conflict_results}
    return all(item["pass"] for item in (*missing_results, *conflict_results)), details


_DRAWING_DIMENSIONS: dict[str, dict[str, str]] = {
    "drawing_flange_004": {
        "outside_diameter": "outside_diameter",
        "thickness": "thickness",
        "center_bore": "center_bore",
        "bolt_hole_diameter": "bolt_hole_diameter",
        "bolt_circle": "bolt_circle",
    },
    "drawing_clevis_002": {
        "overall_width": "overall_width",
        "overall_depth": "overall_depth",
        "overall_height": "overall_height",
        "pin_diameter": "pin_diameter",
        "pin_center_height": "pin_center_height",
    },
}


def _dedup_cylinders(cylinders: Iterable[_Cylinder], *, tolerance: float = 1e-4) -> list[_Cylinder]:
    result: list[_Cylinder] = []
    for item in cylinders:
        if any(
            abs(old.radius - item.radius) <= tolerance
            and abs(_dot(old.axis, item.axis)) >= 1.0 - 1e-5
            and math.dist(old.centerline_point, item.centerline_point) <= tolerance
            for old in result
        ):
            continue
        result.append(item)
    return result


def _axis_void_across_bounds(
    brep: BRepAudit, axis: Sequence[float], centerline_point: Sequence[float]
) -> bool:
    direction = _unit(axis)
    center = tuple(float(value) for value in centerline_point)
    corners = _shape_corners(brep.bounds)
    projection = [_dot(_sub(corner, center), direction) for corner in corners]
    low, high = min(projection), max(projection)
    if high - low <= 1e-8:
        return False
    inset = min(0.02, (high - low) / 1000)
    samples = 21
    return all(
        brep.point_state(_point_at_axis(center, direction, low + inset + (high - low - 2 * inset) * index / (samples - 1))) == "void"
        for index in range(samples)
    )


def _flange_bolt_holes(brep: BRepAudit, diameter: float, circle_diameter: float, tolerance: float) -> list[_Cylinder]:
    z_axis = (0.0, 0.0, 1.0)
    expected_radius = circle_diameter / 2
    holes = _dedup_cylinders(
        item for item in brep.cylinders
        if abs(_dot(item.axis, z_axis)) >= 1.0 - 1e-5
        and abs(item.radius - diameter / 2) <= tolerance / 2 + 1e-7
        and abs(_norm(item.centerline_point[:2]) - expected_radius) <= tolerance + 1e-7
    )
    if len(holes) != 6:
        return []
    angles = sorted(math.atan2(item.centerline_point[1], item.centerline_point[0]) % (2 * math.pi) for item in holes)
    gaps = [((angles[(index + 1) % 6] - angles[index]) % (2 * math.pi)) for index in range(6)]
    if not all(abs(gap - math.pi / 3) <= math.radians(1.0) for gap in gaps):
        return []
    z_mid = sum(brep.bounds[2]) / 2
    for hole in holes:
        x, y, _ = hole.centerline_point
        if not _axis_void_across_bounds(brep, z_axis, (x, y, z_mid)):
            return []
        if brep.point_state((x + hole.radius * 0.95, y, z_mid)) != "void":
            return []
        if brep.point_state((x + hole.radius * 1.05, y, z_mid)) != "solid":
            return []
    return holes


def _clevis_pin_holes(brep: BRepAudit, diameter: float, center_height: float, tolerance: float) -> tuple[list[_Cylinder], dict[str, Any]]:
    axis = (1.0, 0.0, 0.0)
    holes = _dedup_cylinders(
        item for item in brep.cylinders
        if abs(_dot(item.axis, axis)) >= 1.0 - 1e-5
        and abs(item.centerline_point[1]) <= 0.15
        and abs(item.centerline_point[2] - center_height) <= tolerance + 1e-7
        and abs(item.radius - diameter / 2) <= tolerance / 2 + 1e-7
    )
    if not holes:
        return [], {"expected_pin_holes": 2, "measured_coaxial_pin_axes": 0}

    # The two ear cuts share one geometric axis, so OCCT may represent their
    # cylindrical faces as one or several faces. Count the two physical ears
    # with independent material/void probes instead of counting surface faces.
    width = brep.size[0]
    half_width = width / 2
    ear_thickness = 6.0
    ear_center = half_width - ear_thickness / 2
    depth_mid = sum(brep.bounds[1]) / 2
    outer_wall_y = depth_mid + 10.0
    probe_z = center_height
    ear_results = []
    for sign in (-1.0, 1.0):
        center_x = sign * ear_center
        boundary_inside_x = sign * (half_width - ear_thickness - 0.1)
        gap_outside_x = sign * (half_width - ear_thickness + 0.1)
        ear_material = brep.point_state((center_x, outer_wall_y, probe_z)) == "solid"
        pin_axis_void = brep.point_state((center_x, depth_mid, probe_z)) == "void"
        hole_void = brep.point_state((center_x, depth_mid + diameter / 2 - 0.1, probe_z)) == "void"
        hole_wall = brep.point_state((center_x, depth_mid + diameter / 2 + 0.1, probe_z)) == "solid"
        thickness_inside = brep.point_state((boundary_inside_x, outer_wall_y, probe_z)) == "solid"
        gap_outside = brep.point_state((gap_outside_x, outer_wall_y, probe_z)) == "void"
        ear_results.append({
            "ear_material": ear_material,
            "pin_axis_void": pin_axis_void,
            "hole_radius_void": hole_void,
            "hole_outer_wall_material": hole_wall,
            "six_mm_ear_inside_material": thickness_inside,
            "inner_gap_void": gap_outside,
        })
    if any(not all(row.values()) for row in ear_results):
        return [], {"expected_pin_holes": 2, "measured_coaxial_pin_axes": len(holes), "ear_witnesses": ear_results}
    if not all(_axis_void_across_bounds(brep, axis, item.centerline_point) for item in holes):
        return [], {"reason": "pin-hole centerline is not open across the model width", "ear_witnesses": ear_results}
    measured_height = sum(item.centerline_point[2] - brep.bounds[2][0] for item in holes) / len(holes)
    if not _close(measured_height, center_height, tolerance):
        return [], {"measured_center_height_mm": measured_height, "ear_witnesses": ear_results}
    return holes, {"coaxial_axis_count": len(holes), "physical_ear_count": 2, "ear_witnesses": ear_results}


def _drawing_geometry_value(task_id: str, dimension_id: str, expected: float, tolerance: float, brep: BRepAudit) -> tuple[bool, Any]:
    box = list(brep.size)
    if task_id == "drawing_flange_004":
        z_axis = (0.0, 0.0, 1.0)
        if dimension_id == "outside_diameter":
            surface = brep.cylinders_on_axis(z_axis, (0.0, 0.0, 0.0), radius=expected / 2, radius_tolerance=tolerance / 2, axis_tolerance=tolerance)
            actual = 2 * max((item.radius for item in surface), default=0.0)
            z_mid = sum(brep.bounds[2]) / 2
            outside_witness = (
                brep.point_state((expected / 2 - 0.1, 0.0, z_mid)) == "solid"
                and brep.point_state((expected / 2 + 0.1, 0.0, z_mid)) == "void"
            )
            return bool(surface) and _close(actual, expected, tolerance) and outside_witness, {"diameter_mm": actual, "outer_wall_witness": outside_witness}
        if dimension_id == "thickness":
            actual = box[2]
            return _close(actual, expected, tolerance), actual
        if dimension_id == "center_bore":
            surface = brep.cylinders_on_axis(z_axis, (0.0, 0.0, 0.0), radius=expected / 2, radius_tolerance=tolerance / 2, axis_tolerance=tolerance)
            actual = 2 * max((item.radius for item in surface), default=0.0)
            z_mid = sum(brep.bounds[2]) / 2
            bore_witness = (
                _axis_void_across_bounds(brep, z_axis, (0.0, 0.0, z_mid))
                and brep.point_state((expected / 2 - 0.1, 0.0, z_mid)) == "void"
                and brep.point_state((expected / 2 + 0.1, 0.0, z_mid)) == "solid"
            )
            return bool(surface) and _close(actual, expected, tolerance) and bore_witness, {"diameter_mm": actual, "centerline_open": bore_witness}
        if dimension_id in {"bolt_hole_diameter", "bolt_circle"}:
            bolt_circle = 60.0 if dimension_id == "bolt_hole_diameter" else expected
            hole_diameter = expected if dimension_id == "bolt_hole_diameter" else 8.0
            holes = _flange_bolt_holes(brep, hole_diameter, bolt_circle, tolerance)
            if len(holes) != 6:
                return False, {"expected_bolt_holes": 6, "measured_bolt_holes": len(holes)}
            if dimension_id == "bolt_hole_diameter":
                actual = 2 * sum(item.radius for item in holes) / len(holes)
                return _close(actual, expected, tolerance), {"diameter_mm": actual, "hole_count": len(holes)}
            actual = 2 * sum(_norm(item.centerline_point[:2]) for item in holes) / len(holes)
            return _close(actual, expected, tolerance), {"bolt_circle_mm": actual, "hole_count": len(holes), "six_holes_equally_spaced": True}
    elif task_id == "drawing_clevis_002":
        if dimension_id == "overall_width":
            actual = box[0]
            return _close(actual, expected, tolerance), actual
        if dimension_id == "overall_depth":
            actual = box[1]
            return _close(actual, expected, tolerance), actual
        if dimension_id == "overall_height":
            actual = box[2]
            return _close(actual, expected, tolerance), actual
        if dimension_id in {"pin_diameter", "pin_center_height"}:
            height = expected if dimension_id == "pin_center_height" else 30.0
            holes, witnesses = _clevis_pin_holes(brep, 10.0 if dimension_id == "pin_center_height" else expected, height, tolerance)
            if not holes:
                return False, witnesses
            if dimension_id == "pin_diameter":
                actual = 2 * sum(item.radius for item in holes) / len(holes)
                return _close(actual, expected, tolerance), {"diameter_mm": actual, **witnesses}
            actual = sum(item.centerline_point[2] - brep.bounds[2][0] for item in holes) / len(holes)
            aligned = max(item.centerline_point[2] for item in holes) - min(item.centerline_point[2] for item in holes) <= tolerance + 1e-7
            return _close(actual, expected, tolerance) and aligned, {"center_height_mm": actual, "aligned": aligned, **witnesses}
    raise UnsupportedEvidence(f"drawing dimension {task_id}/{dimension_id} has no reviewed geometry mapping")


@dataclass(frozen=True)
class _SvgBox:
    layer: str
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True)
class _SvgText:
    layer: str
    text: str
    x: float
    y: float


def _svg_box(element: ElementTree.Element) -> tuple[float, float, float, float] | None:
    tag = element.tag.rsplit("}", 1)[-1].casefold()
    try:
        if tag == "line":
            points = [(float(element.attrib["x1"]), float(element.attrib["y1"])), (float(element.attrib["x2"]), float(element.attrib["y2"]))]
        elif tag in {"polyline", "polygon"}:
            points = [tuple(map(float, pair.split(","))) for pair in element.attrib.get("points", "").split()]
        elif tag == "circle":
            cx, cy, radius = (float(element.attrib[key]) for key in ("cx", "cy", "r"))
            return (cx - radius, cy - radius, cx + radius, cy + radius)
        elif tag == "rect":
            x, y, width, height = (float(element.attrib.get(key, "0")) for key in ("x", "y", "width", "height"))
            return (x, y, x + width, y + height)
        elif tag == "path":
            path_data = element.attrib.get("d", "")
            numbers = [float(item) for item in _NUMBER.findall(path_data)]
            if not numbers:
                return None
            commands = [command.upper() for command in re.findall(r"[A-Za-z]", path_data)]
            if commands == ["M", "A"] and len(numbers) == 9:
                return _svg_arc_bounds(numbers)
            raise UnsupportedEvidence("drawing SVG contains a path command outside the reviewed M/A arc subset")
        else:
            return None
    except (KeyError, ValueError, OverflowError):
        raise MalformedArtifact(f"SVG {tag} geometry is malformed")
    if not points:
        return None
    if any(not math.isfinite(value) for point in points for value in point):
        raise MalformedArtifact("SVG geometry contains non-finite coordinates")
    return (min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points))


def _svg_arc_bounds(numbers: Sequence[float]) -> tuple[float, float, float, float]:
    x1, y1, rx, ry, rotation, large_arc, sweep, x2, y2 = map(float, numbers)
    rx, ry = abs(rx), abs(ry)
    if rx <= 1e-12 or ry <= 1e-12 or large_arc not in {0.0, 1.0} or sweep not in {0.0, 1.0}:
        raise MalformedArtifact("SVG arc has invalid radii or flags")
    if math.dist((x1, y1), (x2, y2)) <= 1e-12:
        return (x1 - rx, y1 - ry, x1 + rx, y1 + ry)

    phi = math.radians(rotation)
    cosine, sine = math.cos(phi), math.sin(phi)
    half_dx, half_dy = (x1 - x2) / 2, (y1 - y2) / 2
    x_prime = cosine * half_dx + sine * half_dy
    y_prime = -sine * half_dx + cosine * half_dy
    scale = (x_prime / rx) ** 2 + (y_prime / ry) ** 2
    if scale > 1:
        factor = math.sqrt(scale)
        rx, ry = rx * factor, ry * factor
    numerator = max(0.0, rx * rx * ry * ry - rx * rx * y_prime * y_prime - ry * ry * x_prime * x_prime)
    denominator = rx * rx * y_prime * y_prime + ry * ry * x_prime * x_prime
    coefficient = 0.0 if denominator <= 1e-18 else math.sqrt(numerator / denominator)
    if large_arc == sweep:
        coefficient = -coefficient
    center_x_prime = coefficient * (rx * y_prime / ry)
    center_y_prime = coefficient * (-ry * x_prime / rx)
    center_x = cosine * center_x_prime - sine * center_y_prime + (x1 + x2) / 2
    center_y = sine * center_x_prime + cosine * center_y_prime + (y1 + y2) / 2
    start_angle = math.atan2((y_prime - center_y_prime) / ry, (x_prime - center_x_prime) / rx)
    end_angle = math.atan2((-y_prime - center_y_prime) / ry, (-x_prime - center_x_prime) / rx)
    delta = (end_angle - start_angle) % (2 * math.pi)
    if sweep == 0:
        delta -= 2 * math.pi

    def on_sweep(angle: float) -> bool:
        distance = (angle - start_angle) % (2 * math.pi) if delta >= 0 else (start_angle - angle) % (2 * math.pi)
        return distance <= abs(delta) + 1e-8

    candidates = [start_angle, end_angle]
    x_extreme = math.atan2(-ry * sine, rx * cosine)
    y_extreme = math.atan2(ry * cosine, rx * sine)
    for angle in (x_extreme, x_extreme + math.pi, y_extreme, y_extreme + math.pi):
        if on_sweep(angle):
            candidates.append(angle)
    points = [
        (
            center_x + cosine * rx * math.cos(angle) - sine * ry * math.sin(angle),
            center_y + sine * rx * math.cos(angle) + cosine * ry * math.sin(angle),
        )
        for angle in candidates
    ]
    return (min(item[0] for item in points), min(item[1] for item in points), max(item[0] for item in points), max(item[1] for item in points))


def _svg_transform_operations(transform: str) -> list[tuple[str, tuple[float, ...]]]:
    operations = []
    pattern = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
    consumed = []
    for match in pattern.finditer(transform):
        consumed.append(match.span())
        name = match.group(1).casefold()
        values = tuple(float(value) for value in _NUMBER.findall(match.group(2)))
        if name == "translate" and len(values) in {1, 2}:
            operations.append((name, (values[0], values[1] if len(values) == 2 else 0.0)))
        elif name == "scale" and len(values) in {1, 2}:
            operations.append((name, (values[0], values[1] if len(values) == 2 else values[0])))
        elif name == "matrix" and len(values) == 6:
            operations.append((name, values))
        else:
            raise UnsupportedEvidence(f"drawing SVG transform {name!r} is outside the reviewed affine subset")
    leftover = list(transform)
    for start, end in reversed(consumed):
        leftover[start:end] = " " * (end - start)
    if not operations or "".join(leftover).strip():
        raise UnsupportedEvidence("drawing SVG transform syntax is outside the reviewed affine subset")
    return operations


def _transform_svg_point(
    point: tuple[float, float], ancestors: Sequence[ElementTree.Element]
) -> tuple[float, float]:
    x, y = point
    for ancestor in ancestors:
        transform = ancestor.attrib.get("transform")
        if not transform:
            continue
        operations = _svg_transform_operations(transform)
        for name, values in reversed(operations):
            if name == "translate":
                x, y = x + values[0], y + values[1]
            elif name == "scale":
                x, y = x * values[0], y * values[1]
            else:
                a, b, c, d, e, f = values
                x, y = a * x + c * y + e, b * x + d * y + f
    if not math.isfinite(x) or not math.isfinite(y):
        raise MalformedArtifact("SVG transform produced non-finite geometry")
    return x, y


def _transform_svg_box(
    bounds: Sequence[float], ancestors: Sequence[ElementTree.Element]
) -> tuple[float, float, float, float]:
    points = [
        _transform_svg_point((x, y), ancestors)
        for x in (float(bounds[0]), float(bounds[2]))
        for y in (float(bounds[1]), float(bounds[3]))
    ]
    return (min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points))


def _read_svg(path: Path) -> tuple[list[_SvgBox], list[_SvgText]]:
    if not path.is_file():
        raise MalformedArtifact("drawing SVG is missing")
    if path.stat().st_size <= 0 or path.stat().st_size > 32 * 1024 * 1024:
        raise MalformedArtifact("drawing SVG is empty or exceeds the 32 MiB audit limit")
    try:
        root = ElementTree.fromstring(path.read_bytes())
    except (OSError, ElementTree.ParseError) as error:
        raise MalformedArtifact(f"drawing SVG cannot be parsed: {error}") from error
    if root.tag != f"{{{_SVG_NS}}}svg":
        raise MalformedArtifact("drawing artifact is not an SVG document")
    viewbox = [float(value) for value in _NUMBER.findall(root.attrib.get("viewBox", ""))]
    if len(viewbox) != 4 or viewbox[2] <= 0 or viewbox[3] <= 0:
        raise MalformedArtifact("drawing SVG needs a finite, positive physical viewBox")
    parent = {child: ancestor for ancestor in root.iter() for child in ancestor}
    boxes = []
    labels = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        layer = str(element.attrib.get("data-layer", "")).upper()
        if tag == "text":
            try:
                x, y = float(element.attrib.get("x", "nan")), float(element.attrib.get("y", "nan"))
            except ValueError as error:
                raise MalformedArtifact("SVG text position is malformed") from error
            if not math.isfinite(x) or not math.isfinite(y):
                raise MalformedArtifact("SVG text position is non-finite")
            labels.append(_SvgText(layer, "".join(element.itertext()).strip(), x, y))
        elif layer in {"VISIBLE", "HIDDEN"}:
            bounds = _svg_box(element)
            if bounds is not None:
                ancestors = []
                current = parent.get(element)
                while current is not None:
                    ancestors.append(current)
                    current = parent.get(current)
                boxes.append(_SvgBox(layer, _transform_svg_box(bounds, ancestors)))
    return boxes, labels


def _merge_svg_boxes(boxes: Sequence[_SvgBox], *, gap: float = 1.0) -> list[tuple[float, float, float, float]]:
    parent = list(range(len(boxes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a

    for left in range(len(boxes)):
        ax0, ay0, ax1, ay1 = boxes[left].bounds
        for right in range(left + 1, len(boxes)):
            bx0, by0, bx1, by1 = boxes[right].bounds
            dx = max(ax0 - bx1, bx0 - ax1, 0.0)
            dy = max(ay0 - by1, by0 - ay1, 0.0)
            if math.hypot(dx, dy) <= gap:
                union(left, right)
    grouped: dict[int, list[_SvgBox]] = {}
    for index, item in enumerate(boxes):
        grouped.setdefault(find(index), []).append(item)
    result = []
    for group in grouped.values():
        result.append((
            min(item.bounds[0] for item in group),
            min(item.bounds[1] for item in group),
            max(item.bounds[2] for item in group),
            max(item.bounds[3] for item in group),
        ))
    return result


def _distance_to_box(x: float, y: float, box: Sequence[float]) -> float:
    dx = max(float(box[0]) - x, 0.0, x - float(box[2]))
    dy = max(float(box[1]) - y, 0.0, y - float(box[3]))
    return math.hypot(dx, dy)


def _projection_dimensions(task_id: str, view: str, brep: BRepAudit) -> tuple[float, float]:
    x, y, z = brep.size
    if view == "front":
        return x, z
    if view == "top":
        return x, y
    if view == "right":
        return y, z
    raise UnsupportedEvidence(f"drawing view {view!r} has no reviewed projection mapping for {task_id}")


def _check_svg_views(task_id: str, required: Sequence[str], brep: BRepAudit, svg_path: Path) -> tuple[bool, Any]:
    boxes, labels = _read_svg(svg_path)
    clusters = _merge_svg_boxes(boxes)
    if not clusters:
        return False, {"reason": "SVG has no visible or hidden projected geometry"}
    matches = []
    view_centers: dict[str, tuple[float, float]] = {}
    used_clusters: set[int] = set()
    for view in required:
        candidates = [
            label for label in labels
            if label.layer == "ANNOTATION"
            and re.search(rf"\b{re.escape(view)}\b", label.text, flags=re.IGNORECASE)
        ]
        if not candidates:
            return False, {"view": view, "reason": "required named view label is absent"}
        best = None
        for label in candidates:
            for index, cluster in enumerate(clusters):
                if index in used_clusters:
                    continue
                distance = _distance_to_box(label.x, label.y, cluster)
                if best is None or distance < best[0]:
                    best = (distance, index, label)
        if best is None or best[0] > 15.0:
            return False, {"view": view, "reason": "view label is not adjacent to a projected geometry group", "distance_mm": best[0] if best else None}
        distance, index, label = best
        used_clusters.add(index)
        cluster = clusters[index]
        measured = (cluster[2] - cluster[0], cluster[3] - cluster[1])
        expected = _projection_dimensions(task_id, view, brep)
        if min(*measured, *expected) <= 1e-8:
            return False, {"view": view, "reason": "projected or source extent is degenerate"}
        scale_x, scale_y = measured[0] / expected[0], measured[1] / expected[1]
        if not 0.01 <= scale_x <= 100 or not 0.01 <= scale_y <= 100 or abs(scale_x - scale_y) > 0.08 * max(scale_x, scale_y) + 0.01:
            return False, {"view": view, "reason": "projected geometry has the wrong orthographic aspect", "measured_sheet_mm": measured, "expected_model_mm": expected, "axis_scales": [scale_x, scale_y]}
        view_centers[view] = ((cluster[0] + cluster[2]) / 2, (cluster[1] + cluster[3]) / 2)
        matches.append({"view": view, "label": label.text, "projected_mm": measured, "source_mm": expected, "sheet_scale": (scale_x + scale_y) / 2, "sheet_center_mm": view_centers[view]})
    if {"front", "top", "right"}.issubset(set(required)):
        front, top, right = view_centers["front"], view_centers["top"], view_centers["right"]
        if top[1] >= front[1] - 1.0 or right[0] <= front[0] + 1.0:
            return False, {"reason": "named views do not follow third-angle placement", "front_center_mm": front, "top_center_mm": top, "right_center_mm": right}
    return True, matches


def _label_has_dimension(label: _SvgText, dimension_id: str, expected: float) -> bool:
    if label.layer not in {"DIMENSIONS", "ANNOTATION", "NOTES", "SCHEDULE"}:
        return False
    values = [float(value) for value in _NUMBER.findall(label.text)]
    if not any(abs(value - expected) <= max(0.01, abs(expected) * 1e-4) for value in values):
        return False
    if dimension_id in {"outside_diameter", "center_bore", "bolt_hole_diameter", "pin_diameter"}:
        return any(symbol in label.text.casefold() for symbol in ("ø", "⌀", "diameter", "dia."))
    if dimension_id == "bolt_circle":
        return bool(re.search(r"\b(?:pcd|pitch\s+circle|bolt\s+circle)\b", label.text, flags=re.IGNORECASE))
    return True


def _check_drawing_dimensions(check: Mapping[str, Any], task_id: str, brep: BRepAudit, svg_path: Path) -> tuple[bool, Any]:
    if task_id not in _DRAWING_DIMENSIONS:
        raise UnsupportedEvidence(f"drawing task {task_id!r} has no reviewed original-request mapping")
    _, labels = _read_svg(svg_path)
    dimension_details = []
    all_pass = True
    all_supported = True
    for dimension in check["dimensions"]:
        dimension_id = dimension["id"]
        if dimension_id not in _DRAWING_DIMENSIONS[task_id]:
            all_supported = False
            dimension_details.append({"id": dimension_id, "status": _UNSUPPORTED, "reason": "dimension has no geometry mapping"})
            continue
        expected = float(dimension["value_mm"])
        tolerance = float(dimension["tolerance_mm"])
        geometry_ok, measured = _drawing_geometry_value(task_id, dimension_id, expected, tolerance, brep)
        label_ok = any(_label_has_dimension(label, dimension_id, expected) for label in labels)
        passed = geometry_ok and label_ok
        all_pass = all_pass and passed
        dimension_details.append({
            "id": dimension_id,
            "status": _SUPPORTED if passed else _FAILED,
            "geometry_pass": geometry_ok,
            "svg_label_pass": label_ok,
            "expected_mm": expected,
            "measured_geometry": measured,
        })
    views_ok, views = _check_svg_views(task_id, check["required_views"], brep, svg_path)
    all_pass = all_pass and views_ok
    details = {"dimensions": dimension_details, "required_views": views, "views_pass": views_ok}
    if not all_supported:
        raise UnsupportedEvidence(f"at least one requested drawing dimension lacks an implemented measurement")
    return all_pass, details


def _evaluate_check(
    task_id: str,
    stage: Mapping[str, Any],
    check: Mapping[str, Any],
    *,
    brep: BRepAudit | None,
    sheet_path: Path | None,
    svg_path: Path | None,
    response_text: str,
) -> CheckObservation:
    check_id, check_type = str(check["id"]), str(check["type"])
    coverage = str(stage["oracle"]["coverage"])
    try:
        if check_type == "clarification":
            passed, actual = _check_clarification(check, response_text)
            return CheckObservation(check_id, check_type, _SUPPORTED if passed else _FAILED, coverage,
                                    "all reviewed missing/conflicting topics were addressed" if passed else "one or more required topics were absent from the user-facing response",
                                    {key: check[key] for key in ("missing", "conflicts") if key in check}, actual)
        if check_type == "dxf_profile":
            if sheet_path is None:
                raise MalformedArtifact("native 2D sheet artifact was not captured")
            passed, actual = _check_dxf_profile(check, sheet_path)
            return CheckObservation(check_id, check_type, _SUPPORTED if passed else _FAILED, coverage,
                                    "native 2D cut geometry matches every implemented profile witness" if passed else "native 2D cut geometry failed one or more profile witnesses",
                                    {key: value for key, value in check.items() if key not in {"id", "type"}}, actual)
        if check_type == "drawing_dimensions":
            if brep is None or svg_path is None:
                raise MalformedArtifact("drawing audit requires both a saved BRep and drawing SVG")
            passed, actual = _check_drawing_dimensions(check, task_id, brep, svg_path)
            return CheckObservation(check_id, check_type, _SUPPORTED if passed else _FAILED, coverage,
                                    "geometry-derived values and projected named views match the requested drawing witnesses" if passed else "geometry or drawing-sheet evidence failed one or more requested witnesses",
                                    {key: value for key, value in check.items() if key not in {"id", "type"}}, actual)
        if brep is None:
            raise MalformedArtifact("saved BRep artifact was not captured")
        if check_type == "bbox_mm":
            passed, actual = _check_bbox(check, brep)
        elif check_type == "solids":
            passed, actual = _check_solids(check, brep)
        elif check_type == "point_material":
            passed, actual = _check_point(check, brep)
        elif check_type == "cylindrical_bores":
            passed, actual = _check_bores(check, brep)
        elif check_type == "component_layout":
            passed, actual = _check_component_layout(check, brep)
        elif check_type == "linear_pattern":
            passed, actual = _check_linear_pattern(check, brep)
        elif check_type == "rotational_pattern":
            passed, actual = _check_rotational_pattern(check, brep)
        else:
            raise UnsupportedEvidence(f"check type {check_type!r} has no independent pilot oracle")
        return CheckObservation(check_id, check_type, _SUPPORTED if passed else _FAILED, coverage,
                                "independent artifact measurement matches the request witness" if passed else "independent artifact measurement did not match the request witness",
                                {key: value for key, value in check.items() if key not in {"id", "type"}}, actual)
    except UnsupportedEvidence as error:
        return CheckObservation(check_id, check_type, _UNSUPPORTED, coverage, str(error), check, None)
    except (MalformedArtifact, OSError, ValueError, TypeError, ArithmeticError) as error:
        return CheckObservation(check_id, check_type, _FAILED, coverage, str(error), check, None)
    except Exception as error:
        # An unexpected oracle-side exception is not allowed to certify output.
        return CheckObservation(check_id, check_type, _UNSUPPORTED, coverage,
                                f"oracle evaluation could not complete: {type(error).__name__}: {error}", check, None)


def evaluate_stage(
    task_id: str,
    stage: Mapping[str, Any],
    *,
    brep_path: Path | None = None,
    sheet_json_path: Path | None = None,
    drawing_svg_path: Path | None = None,
    response_text: str = "",
) -> StageAudit:
    """Audit every declared check and account for every unsupported result."""
    outcome = str(stage["expected_outcome"])
    coverage = str(stage["oracle"]["coverage"])
    brep: BRepAudit | None = None
    if any(check["type"] != "clarification" and check["type"] != "dxf_profile" for check in stage["oracle"]["checks"]):
        if brep_path is None:
            # The missing artifact is a known delivery failure, not an unsupported oracle.
            brep_error: Exception | None = MalformedArtifact("expected BRep artifact was not captured")
        else:
            try:
                brep = BRepAudit(Path(brep_path))
                brep_error = None
            except (UnsupportedEvidence, MalformedArtifact) as error:
                brep_error = error
    else:
        brep_error = None

    observations = []
    for check in stage["oracle"]["checks"]:
        if check["type"] != "clarification" and check["type"] != "dxf_profile" and brep is None and brep_error is not None:
            status = _UNSUPPORTED if isinstance(brep_error, UnsupportedEvidence) else _FAILED
            observations.append(CheckObservation(
                str(check["id"]), str(check["type"]), status, coverage, str(brep_error), check, None
            ))
            continue
        observations.append(_evaluate_check(
            task_id,
            stage,
            check,
            brep=brep,
            sheet_path=Path(sheet_json_path) if sheet_json_path else None,
            svg_path=Path(drawing_svg_path) if drawing_svg_path else None,
            response_text=response_text,
        ))

    if any(item.status == _FAILED for item in observations):
        verdict = "bad"
    elif observations and all(item.status == _SUPPORTED for item in observations) and coverage == "complete":
        verdict = "good"
    else:
        verdict = "indeterminate"
    effective_coverage = "complete" if coverage == "complete" and all(item.status != _UNSUPPORTED for item in observations) else "bounded"
    return StageAudit(
        task_id=task_id,
        stage_id=str(stage["id"]),
        expected_outcome=outcome,
        declared_coverage=coverage,
        effective_coverage=effective_coverage,
        verdict=verdict,
        checks=tuple(observations),
    )


def evaluate_task(task: Mapping[str, Any], stage_evidence: Mapping[str, Mapping[str, Any]]) -> TaskAudit:
    """Evaluate all task stages; callers must provide one evidence row per stage."""
    stages = []
    for stage in task["stages"]:
        evidence = stage_evidence.get(stage["id"], {})
        stages.append(evaluate_stage(
            str(task["id"]), stage,
            brep_path=Path(evidence["brep_path"]) if evidence.get("brep_path") else None,
            sheet_json_path=Path(evidence["sheet_json_path"]) if evidence.get("sheet_json_path") else None,
            drawing_svg_path=Path(evidence["drawing_svg_path"]) if evidence.get("drawing_svg_path") else None,
            response_text=str(evidence.get("response_text", "")),
        ))
    checks = [check for stage in stages for check in stage.checks]
    if any(check.status == _FAILED for check in checks):
        verdict = "bad"
    elif checks and all(check.status == _SUPPORTED for check in checks) and all(stage.effective_coverage == "complete" for stage in stages):
        verdict = "good"
    else:
        verdict = "indeterminate"
    coverage = "complete" if all(stage.effective_coverage == "complete" for stage in stages) else "bounded"
    return TaskAudit(str(task["id"]), verdict, coverage, tuple(stages))


def request_pass_counts(audits: Iterable[TaskAudit]) -> dict[str, Any]:
    """Count every declared check; unsupported and missing evidence never pass."""
    checks = [check for audit in audits for stage in audit.stages for check in stage.checks]
    passed = sum(check.status == _SUPPORTED for check in checks)
    failed = sum(check.status == _FAILED for check in checks)
    unsupported = sum(check.status == _UNSUPPORTED for check in checks)
    denominator = len(checks)
    return {
        "passed": passed,
        "failed": failed,
        "unsupported": unsupported,
        "denominator": denominator,
        "rate": passed / denominator if denominator else None,
        "unsupported_are_nonpasses": True,
    }


__all__ = [
    "BRepAudit",
    "CheckObservation",
    "StageAudit",
    "TaskAudit",
    "UnsupportedEvidence",
    "evaluate_stage",
    "evaluate_task",
    "request_pass_counts",
]
