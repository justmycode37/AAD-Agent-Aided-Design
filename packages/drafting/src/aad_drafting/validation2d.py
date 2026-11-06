"""Native 2D cut-geometry validation and measurement evidence.

This module deliberately imports no CAD kernel at import time. OCCT-backed
geometry is loaded only when :func:`checks` or :func:`measurement_evidence`
is asked to inspect a sheet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
from numbers import Real
from typing import Any


_JOIN_TOLERANCE = 1e-9
_ANGLE_TOLERANCE_DEG = 1e-8


def _raise_if_memory_exhaustion(exc: BaseException) -> None:
    from aad.engine.memory import is_memory_exhaustion

    if is_memory_exhaustion(exc):
        raise exc


@dataclass
class _NativeEdge:
    native: Any
    record: dict[str, Any]
    source: str
    source_kind: str
    index: int
    native_type: str
    start: tuple[float, float]
    end: tuple[float, float]
    start_node: int = -1
    end_node: int = -1
    component: int = -1
    length: float = 0.0


@dataclass
class _Contour:
    identifier: str
    edge_ids: list[int]
    wire: Any
    face: Any
    sources: list[str]
    closed: bool
    valid: bool
    area: float | None
    perimeter: float
    bounds: list[float] | None
    depth: int | None = None
    parent: str | None = None
    issues: list[str] | None = None


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _json_text(value: Any) -> str:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _check(
    identifier: str,
    level: str,
    status: str,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
    heuristic: bool | None = None,
    assumptions: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": identifier,
        "suite": "2d",
        "level": level,
        "status": status,
        "message": message,
    }
    if expected is not None:
        result["expected"] = expected
    if actual is not None:
        result["actual"] = actual
    if heuristic is not None:
        result["heuristic"] = heuristic
    if assumptions is not None:
        result["assumptions"] = assumptions
    return result


def _units(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip().lower()
    return result if result in {"mm", "in"} else None


def _numeric_tolerance(edges: list[_NativeEdge]) -> float:
    magnitude = 1.0
    for edge in edges:
        for x, y in (edge.start, edge.end):
            magnitude = max(magnitude, abs(x), abs(y))
        record = edge.record
        center = record.get("center")
        if center is not None:
            magnitude = max(magnitude, abs(float(center[0])), abs(float(center[1])))
        radius = record.get("radius")
        if radius is not None:
            magnitude = max(magnitude, abs(float(radius)))
    return max(_JOIN_TOLERANCE, math.ulp(magnitude) * 16.0)


def _point3(value: Any) -> tuple[float, float, float]:
    return (float(value.X), float(value.Y), float(value.Z))


def _native_edge_record(edge: Any, source: str, source_kind: str, index: int, edge_record: Any) -> _NativeEdge:
    record = edge_record(edge)
    kind = str(record["kind"])
    if kind == "circle":
        cx, cy = float(record["center"][0]), float(record["center"][1])
        radius = float(record["radius"])
        # A full circle is a closed OCCT edge. Its seam is a topological
        # self-connection; it is not inferred from a rendered approximation.
        start = end = (cx + radius, cy)
    else:
        start3 = _point3(edge.start_point())
        end3 = _point3(edge.end_point())
        start, end = (start3[0], start3[1]), (end3[0], end3[1])
    length = float(edge.length)
    if not math.isfinite(length) or length <= 0.0:
        raise ValueError("OCCT returned a zero-length or non-finite cut edge.")
    native_type = str(edge.geom_type.name).upper()
    return _NativeEdge(
        native=edge,
        record=record,
        source=source,
        source_kind=source_kind,
        index=index,
        native_type=native_type,
        start=start,
        end=end,
        length=length,
    )


def _same_point(a: tuple[float, float], b: tuple[float, float], tolerance: float) -> bool:
    return math.dist(a, b) <= tolerance


def _cluster_endpoints(edges: list[_NativeEdge], tolerance: float) -> tuple[list[list[int]], list[int]]:
    nodes: list[tuple[float, float]] = []
    endpoint_nodes: list[int] = []
    for edge in edges:
        for point in (edge.start, edge.end):
            node_id = next(
                (index for index, existing in enumerate(nodes) if _same_point(existing, point, tolerance)),
                None,
            )
            if node_id is None:
                node_id = len(nodes)
                nodes.append(point)
            endpoint_nodes.append(node_id)
    incidences: list[list[int]] = [[] for _ in nodes]
    for edge_id in range(len(edges)):
        start_node, end_node = endpoint_nodes[2 * edge_id : 2 * edge_id + 2]
        edges[edge_id].start_node = start_node
        edges[edge_id].end_node = end_node
        incidences[start_node].append(edge_id)
        incidences[end_node].append(edge_id)
    return incidences, endpoint_nodes


def _edge_components(edges: list[_NativeEdge], incidences: list[list[int]]) -> list[list[int]]:
    unseen = set(range(len(edges)))
    components: list[list[int]] = []
    while unseen:
        first = min(unseen)
        stack = [first]
        unseen.remove(first)
        component: list[int] = []
        while stack:
            edge_id = stack.pop()
            component.append(edge_id)
            edge = edges[edge_id]
            for node_id in {edge.start_node, edge.end_node}:
                for neighbor in incidences[node_id]:
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        stack.append(neighbor)
        component.sort()
        component_id = len(components)
        for edge_id in component:
            edges[edge_id].component = component_id
        components.append(component)
    return components


def _point_on_arc(point: tuple[float, float], curve: Mapping[str, Any], tolerance: float) -> bool:
    cx, cy = float(curve["center"][0]), float(curve["center"][1])
    radius = float(curve["radius"])
    if abs(math.dist(point, (cx, cy)) - radius) > tolerance:
        return False
    if curve["kind"] == "circle":
        return True
    angle = math.degrees(math.atan2(point[1] - cy, point[0] - cx)) % 360.0
    start = float(curve["start_angle"]) % 360.0
    end = float(curve["end_angle"]) % 360.0
    clockwise = bool(curve["clockwise"])
    sweep = (start - end) % 360.0 if clockwise else (end - start) % 360.0
    delta = (start - angle) % 360.0 if clockwise else (angle - start) % 360.0
    angle_tolerance = min(
        1e-3,
        max(_ANGLE_TOLERANCE_DEG, math.degrees(tolerance / max(radius, tolerance))),
    )
    return delta <= sweep + angle_tolerance or abs(delta - 360.0) <= angle_tolerance


def _line_line_intersections(
    first: Mapping[str, Any], second: Mapping[str, Any], tolerance: float
) -> tuple[str, list[tuple[float, float]]]:
    p = (float(first["start"][0]), float(first["start"][1]))
    p2 = (float(first["end"][0]), float(first["end"][1]))
    q = (float(second["start"][0]), float(second["start"][1]))
    q2 = (float(second["end"][0]), float(second["end"][1]))
    rx, ry = p2[0] - p[0], p2[1] - p[1]
    sx, sy = q2[0] - q[0], q2[1] - q[1]
    rlen, slen = math.hypot(rx, ry), math.hypot(sx, sy)
    denom = rx * sy - ry * sx
    qpx, qpy = q[0] - p[0], q[1] - p[1]
    cross_tolerance = tolerance * max(rlen, slen, 1.0)
    if abs(denom) > cross_tolerance:
        t = (qpx * sy - qpy * sx) / denom
        u = (qpx * ry - qpy * rx) / denom
        t_tol = tolerance / max(rlen, tolerance)
        u_tol = tolerance / max(slen, tolerance)
        if -t_tol <= t <= 1.0 + t_tol and -u_tol <= u <= 1.0 + u_tol:
            point = (p[0] + t * rx, p[1] + t * ry)
            endpoint = (
                min(abs(t), abs(t - 1.0)) <= t_tol
                or min(abs(u), abs(u - 1.0)) <= u_tol
            )
            return ("touch" if endpoint else "cross", [point])
        return ("none", [])
    if abs(qpx * ry - qpy * rx) > cross_tolerance:
        return ("none", [])
    if rlen <= tolerance or slen <= tolerance:
        return ("touch", [p]) if _same_point(p, q, tolerance) else ("none", [])
    axis = 0 if abs(rx) >= abs(ry) else 1
    p0, p1 = sorted((p[axis], p2[axis]))
    q0, q1 = sorted((q[axis], q2[axis]))
    overlap = min(p1, q1) - max(p0, q0)
    if overlap < -tolerance:
        return ("none", [])
    if overlap > tolerance:
        return ("overlap", [])
    candidates = [point for point in (p, p2, q, q2) if _point_on_segment(point, p, p2, tolerance) and _point_on_segment(point, q, q2, tolerance)]
    return ("touch", candidates[:1]) if candidates else ("none", [])


def _point_on_segment(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float], tolerance: float
) -> bool:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= tolerance:
        return _same_point(point, start, tolerance)
    cross = (point[0] - start[0]) * dy - (point[1] - start[1]) * dx
    if abs(cross) > tolerance * max(length, 1.0):
        return False
    dot = (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    return -tolerance * length <= dot <= length * length + tolerance * length


def _line_curve_intersections(
    line: Mapping[str, Any], curve: Mapping[str, Any], tolerance: float
) -> tuple[str, list[tuple[float, float]]]:
    p = (float(line["start"][0]), float(line["start"][1]))
    q = (float(line["end"][0]), float(line["end"][1]))
    center = (float(curve["center"][0]), float(curve["center"][1]))
    radius = float(curve["radius"])
    dx, dy = q[0] - p[0], q[1] - p[1]
    length2 = dx * dx + dy * dy
    length = math.sqrt(length2)
    if length <= tolerance:
        return ("none", [])
    t_center = ((center[0] - p[0]) * dx + (center[1] - p[1]) * dy) / length2
    closest = (p[0] + t_center * dx, p[1] + t_center * dy)
    distance = math.dist(closest, center)
    radial_tolerance = tolerance * max(1.0, radius, distance)
    if distance > radius + radial_tolerance:
        return ("none", [])
    h2 = radius * radius - distance * distance
    if h2 < -radial_tolerance * max(radius, 1.0):
        return ("none", [])
    tangent = h2 <= radial_tolerance * max(radius, 1.0)
    offset = math.sqrt(max(0.0, h2)) / length
    parameters = [t_center] if tangent else [t_center - offset, t_center + offset]
    accepted: list[tuple[float, float]] = []
    t_tolerance = tolerance / length
    for t in parameters:
        if not -t_tolerance <= t <= 1.0 + t_tolerance:
            continue
        point = (p[0] + t * dx, p[1] + t * dy)
        if _point_on_arc(point, curve, tolerance):
            if not any(_same_point(point, old, tolerance) for old in accepted):
                accepted.append(point)
    if not accepted:
        return ("none", [])
    if tangent or any(
        min(math.dist(point, p), math.dist(point, q)) <= tolerance for point in accepted
    ):
        return ("touch", accepted)
    return ("cross", accepted)


def _arc_coverage(curve: Mapping[str, Any]) -> list[tuple[float, float]]:
    if curve["kind"] == "circle":
        return [(0.0, 360.0)]
    start = float(curve["start_angle"]) % 360.0
    end = float(curve["end_angle"]) % 360.0
    clockwise = bool(curve["clockwise"])
    sweep = (start - end) % 360.0 if clockwise else (end - start) % 360.0
    ccw_start = end if clockwise else start
    finish = ccw_start + sweep
    if finish <= 360.0:
        return [(ccw_start, finish)]
    return [(ccw_start, 360.0), (0.0, finish - 360.0)]


def _coincident_curve_relation(
    first: Mapping[str, Any], second: Mapping[str, Any], tolerance: float
) -> tuple[str, list[tuple[float, float]]]:
    radius = float(first["radius"])
    angle_tolerance = min(
        1e-3,
        max(_ANGLE_TOLERANCE_DEG, math.degrees(tolerance / max(radius, tolerance))),
    )
    overlap = 0.0
    for a0, a1 in _arc_coverage(first):
        for b0, b1 in _arc_coverage(second):
            overlap += max(0.0, min(a1, b1) - max(a0, b0))
    if overlap > angle_tolerance:
        return ("overlap", [])
    points: list[tuple[float, float]] = []
    for curve in (first, second):
        if curve["kind"] == "arc":
            cx, cy = float(curve["center"][0]), float(curve["center"][1])
            for angle in (float(curve["start_angle"]), float(curve["end_angle"])):
                radians = math.radians(angle)
                point = (cx + radius * math.cos(radians), cy + radius * math.sin(radians))
                if _point_on_arc(point, first, tolerance) and _point_on_arc(point, second, tolerance):
                    if not any(_same_point(point, old, tolerance) for old in points):
                        points.append(point)
    return ("touch", points) if points else ("none", [])


def _curve_curve_intersections(
    first: Mapping[str, Any], second: Mapping[str, Any], tolerance: float
) -> tuple[str, list[tuple[float, float]]]:
    c1 = (float(first["center"][0]), float(first["center"][1]))
    c2 = (float(second["center"][0]), float(second["center"][1]))
    r1, r2 = float(first["radius"]), float(second["radius"])
    distance = math.dist(c1, c2)
    if distance <= tolerance and abs(r1 - r2) <= tolerance:
        return _coincident_curve_relation(first, second, tolerance)
    if distance <= tolerance:
        return ("none", [])
    if distance > r1 + r2 + tolerance or distance < abs(r1 - r2) - tolerance:
        return ("none", [])
    along = (r1 * r1 - r2 * r2 + distance * distance) / (2.0 * distance)
    h2 = r1 * r1 - along * along
    radial_tolerance = tolerance * max(1.0, r1, r2, distance)
    if h2 < -radial_tolerance:
        return ("none", [])
    tangent = h2 <= radial_tolerance
    height = math.sqrt(max(0.0, h2))
    ux, uy = (c2[0] - c1[0]) / distance, (c2[1] - c1[1]) / distance
    base = (c1[0] + along * ux, c1[1] + along * uy)
    points = [base] if tangent else [
        (base[0] - height * uy, base[1] + height * ux),
        (base[0] + height * uy, base[1] - height * ux),
    ]
    accepted = [
        point for point in points
        if _point_on_arc(point, first, tolerance) and _point_on_arc(point, second, tolerance)
    ]
    if not accepted:
        return ("none", [])
    endpoint_tolerance = tolerance * 2.0
    is_endpoint = False
    for point in accepted:
        for curve in (first, second):
            if curve["kind"] == "arc":
                cx, cy = float(curve["center"][0]), float(curve["center"][1])
                for angle in (float(curve["start_angle"]), float(curve["end_angle"])):
                    radians = math.radians(angle)
                    endpoint = (cx + float(curve["radius"]) * math.cos(radians), cy + float(curve["radius"]) * math.sin(radians))
                    if _same_point(point, endpoint, endpoint_tolerance):
                        is_endpoint = True
    if tangent or is_endpoint:
        return ("touch", accepted)
    return ("cross", accepted)


def _intersections(
    first: Mapping[str, Any], second: Mapping[str, Any], tolerance: float
) -> tuple[str, list[tuple[float, float]]]:
    first_kind = str(first["kind"])
    second_kind = str(second["kind"])
    first_line, second_line = first_kind == "line", second_kind == "line"
    if first_line and second_line:
        return _line_line_intersections(first, second, tolerance)
    if first_line:
        return _line_curve_intersections(first, second, tolerance)
    if second_line:
        return _line_curve_intersections(second, first, tolerance)
    return _curve_curve_intersections(first, second, tolerance)


def _is_expected_joint(
    first: _NativeEdge,
    second: _NativeEdge,
    points: list[tuple[float, float]],
    node_degrees: list[int],
    tolerance: float,
) -> bool:
    if first.component != second.component or not points:
        return False
    shared_nodes = {
        first.start_node,
        first.end_node,
    }.intersection({second.start_node, second.end_node})
    if not shared_nodes:
        return False
    shared_points = []
    for node_id in shared_nodes:
        if node_degrees[node_id] != 2:
            continue
        if first.start_node == node_id:
            shared_points.append(first.start)
        else:
            shared_points.append(first.end)
    return bool(shared_points) and all(
        any(_same_point(point, shared_point, tolerance) for shared_point in shared_points)
        for point in points
    )


def _edge_bounds(edge: _NativeEdge, tolerance: float) -> tuple[float, float, float, float]:
    record = edge.record
    kind = str(record["kind"])
    if kind == "line":
        points = [edge.start, edge.end]
    else:
        cx, cy = float(record["center"][0]), float(record["center"][1])
        radius = float(record["radius"])
        if kind == "circle":
            points = [(cx - radius, cy), (cx + radius, cy), (cx, cy - radius), (cx, cy + radius)]
        else:
            points = [edge.start, edge.end]
            for angle in (0.0, 90.0, 180.0, 270.0):
                radians = math.radians(angle)
                point = (cx + radius * math.cos(radians), cy + radius * math.sin(radians))
                if _point_on_arc(point, record, tolerance):
                    points.append(point)
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _bounds_for_edges(edges: list[_NativeEdge], edge_ids: list[int], tolerance: float) -> list[float] | None:
    if not edge_ids:
        return None
    bounds = [_edge_bounds(edges[index], tolerance) for index in edge_ids]
    min_x = min(item[0] for item in bounds)
    min_y = min(item[1] for item in bounds)
    max_x = max(item[2] for item in bounds)
    max_y = max(item[3] for item in bounds)
    return [min_x, min_y, 0.0, max_x, max_y, 0.0]


def _sources(edges: list[_NativeEdge], edge_ids: list[int]) -> list[str]:
    return list(dict.fromkeys(edges[index].source for index in edge_ids))


def _edge_evidence(edge: _NativeEdge) -> dict[str, Any]:
    return {
        "entity": edge.source,
        "edge_index": edge.index,
        "native_type": edge.native_type,
        "analytic_type": {
            "line": "line_segment",
            "circle": "circle",
            "arc": "circular_arc",
        }[str(edge.record["kind"])],
        "geometry": edge.record,
        "length": edge.length,
    }


def _circular_loop(edges: list[_NativeEdge], edge_ids: list[int], tolerance: float) -> tuple[tuple[float, float], float] | None:
    if len(edge_ids) == 1 and edges[edge_ids[0]].record["kind"] == "circle":
        record = edges[edge_ids[0]].record
        return ((float(record["center"][0]), float(record["center"][1])), float(record["radius"]))
    if not edge_ids or any(edges[index].record["kind"] != "arc" for index in edge_ids):
        return None
    records = [edges[index].record for index in edge_ids]
    center = (float(records[0]["center"][0]), float(records[0]["center"][1]))
    radius = float(records[0]["radius"])
    if any(
        math.dist(center, (float(record["center"][0]), float(record["center"][1]))) > tolerance
        or abs(float(record["radius"]) - radius) > tolerance
        for record in records[1:]
    ):
        return None
    intervals = [interval for record in records for interval in _arc_coverage(record)]
    intervals.sort()
    merged: list[list[float]] = []
    angle_tolerance = min(
        1e-3,
        max(_ANGLE_TOLERANCE_DEG, math.degrees(tolerance / max(radius, tolerance))),
    )
    for start, end in intervals:
        if not merged or start > merged[-1][1] + angle_tolerance:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    covered = sum(end - start for start, end in merged)
    if abs(covered - 360.0) > angle_tolerance:
        return None
    return (center, radius)


def _context_sheet(ctx: Any) -> tuple[Any | None, str | None, str | None]:
    raw_sheet = _value(ctx, "sheet")
    context_units = _units(_value(ctx, "units"))
    if raw_sheet is None:
        return None, None, "ctx.sheet is missing; native 2D geometry is unavailable."
    if context_units is None:
        return None, None, "ctx.units must be 'mm' or 'in' to measure the sheet."
    try:
        # model2d has no kernel imports at module scope. Its native converter
        # imports build123d only when an entity is converted below.
        from .model2d import Sheet2D, _LATEST_SHEET

        if callable(getattr(raw_sheet, "to_dict", None)):
            data = raw_sheet.to_dict()
        elif isinstance(raw_sheet, Mapping):
            data = raw_sheet
        else:
            return None, context_units, "ctx.sheet must be a Sheet2D or its JSON-native to_dict mapping."
        required_keys = {"units", "points", "entities"}
        if not isinstance(data, Mapping) or not required_keys.issubset(data):
            missing = sorted(required_keys.difference(data)) if isinstance(data, Mapping) else sorted(required_keys)
            return None, context_units, f"ctx.sheet mapping is missing required to_dict field(s): {', '.join(missing)}."
        token = _LATEST_SHEET.set(_LATEST_SHEET.get())
        try:
            sheet = Sheet2D.from_dict(data)
        finally:
            _LATEST_SHEET.reset(token)
        # `from_dict` validates the geometry fields. Round-tripping also
        # enforces the public finite JSON-native contract for metadata,
        # history, constraints, and entity data.
        sheet.to_dict()
    except Exception as exc:
        _raise_if_memory_exhaustion(exc)
        return None, context_units, f"ctx.sheet is invalid: {type(exc).__name__}: {exc}"
    if context_units != sheet.units:
        return (
            None,
            context_units,
            f"ctx.units ({context_units}) does not match sheet.units ({sheet.units}).",
        )
    return sheet, context_units, None


def _role(sheet: Any, entity: Any) -> str:
    layer_name = str(entity.layer).strip().lower()
    metadata = sheet.layers.get(entity.layer, {})
    role = metadata.get("role") if isinstance(metadata, Mapping) else None
    if isinstance(role, str) and role.strip():
        return role.strip().lower()
    return layer_name


def _analyze(ctx: Any) -> dict[str, Any]:
    sheet, context_units, input_error = _context_sheet(ctx)
    analysis: dict[str, Any] = {
        "units": context_units,
        "sheet": sheet,
        "input_error": input_error,
        "cut_entities": [],
        "annotation_entities": [],
        "edges": [],
        "components": [],
        "contours": [],
        "open_cut_paths": [],
        "intersections": [],
        "self_intersections": [],
        "overlaps": [],
        "contour_intersections": [],
        "unsupported": [],
        "bounds": None,
        "bbox": None,
        "holes": [],
        "boundary_clearances": [],
        "clearance_complete": True,
        "no_cut_geometry": False,
        "tolerance": _JOIN_TOLERANCE,
        "status": {"state": "invalid", "complete": False, "message": input_error},
    }
    if input_error is not None or sheet is None:
        return analysis

    cut_entities = [entity for entity in sheet.entities.values() if _role(sheet, entity) == "cut" and entity.kind != "text"]
    annotations = [
        {"name": entity.name, "layer": entity.layer, "type": "text"}
        for entity in sheet.entities.values()
        if entity.kind == "text"
    ]
    analysis["annotation_entities"] = annotations
    if not cut_entities:
        analysis["no_cut_geometry"] = True
        analysis["status"] = {
            "state": "unavailable",
            "complete": False,
            "message": "No native cut-layer geometry is available; score, engrave, and text are not cut contours.",
        }
        return analysis

    from .model2d import _edge_record, _entity_to_native

    native_cut_records: list[dict[str, Any]] = []
    for entity in cut_entities:
        try:
            native = _entity_to_native(sheet, entity)
            if native.__class__.__name__ == "Wire":
                native_edges = list(native.edges())
                entity_native_type = "WIRE"
            else:
                native_edges = [native]
                entity_native_type = str(native.geom_type.name).upper()
            if not native_edges:
                raise ValueError("The entity produced no native boundary edges.")
            entity_edges: list[_NativeEdge] = []
            for edge_index, edge in enumerate(native_edges):
                entity_edges.append(
                    _native_edge_record(edge, entity.name, entity.kind, edge_index, _edge_record)
                )
            native_cut_records.append(
                {
                    "name": entity.name,
                    "layer": entity.layer,
                    "entity_type": entity.kind,
                    "native_type": entity_native_type,
                    "edges": [_edge_evidence(edge) for edge in entity_edges],
                }
            )
            analysis["edges"].extend(entity_edges)
        except Exception as exc:
            _raise_if_memory_exhaustion(exc)
            analysis["unsupported"].append(
                {
                    "entity": entity.name,
                    "stage": "native_geometry",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
    analysis["cut_entities"] = native_cut_records
    edges: list[_NativeEdge] = analysis["edges"]
    if not edges:
        analysis["status"] = {
            "state": "partial" if analysis["unsupported"] else "unavailable",
            "complete": False,
            "message": "Native cut geometry could not be measured.",
        }
        return analysis

    tolerance = _numeric_tolerance(edges)
    analysis["tolerance"] = tolerance
    all_bounds = [_edge_bounds(edge, tolerance) for edge in edges]
    min_x = min(bounds[0] for bounds in all_bounds)
    min_y = min(bounds[1] for bounds in all_bounds)
    max_x = max(bounds[2] for bounds in all_bounds)
    max_y = max(bounds[3] for bounds in all_bounds)
    bounds3 = [min_x, min_y, 0.0, max_x, max_y, 0.0]
    analysis["bounds"] = bounds3
    analysis["bbox"] = [max_x - min_x, max_y - min_y, 0.0]

    try:
        from build123d import Face, Wire

        incidences, _ = _cluster_endpoints(edges, tolerance)
        components = _edge_components(edges, incidences)
        node_degrees = [len(incident_edges) for incident_edges in incidences]
        analysis["components"] = components
        for component_index, edge_ids in enumerate(components):
            node_ids = {
                node_id
                for edge_id in edge_ids
                for node_id in (edges[edge_id].start_node, edges[edge_id].end_node)
            }
            degrees = [node_degrees[node_id] for node_id in node_ids]
            component_sources = _sources(edges, edge_ids)
            component_bounds = _bounds_for_edges(edges, edge_ids, tolerance)
            if any(degree != 2 for degree in degrees):
                issue = "Cut edges form an open or branched path; every cut boundary must be a simple closed contour."
                analysis["open_cut_paths"].append(
                    {
                        "id": f"cut-path-{component_index + 1}",
                        "source_entities": component_sources,
                        "edge_count": len(edge_ids),
                        "closed": False,
                        "valid": False,
                        "bounds": component_bounds,
                        "issues": [issue],
                    }
                )
                continue
            try:
                wires = list(Wire.combine([edges[index].native for index in edge_ids], tol=tolerance))
                if len(wires) != 1:
                    raise ValueError(f"OCCT combined this connected path into {len(wires)} wires.")
                wire = wires[0]
                closed = bool(wire.is_closed)
                valid = bool(wire.is_valid)
                issues: list[str] = []
                if not closed:
                    issues.append("OCCT reports an open cut wire.")
                if not valid:
                    issues.append("OCCT reports an invalid cut wire.")
                face = None
                area: float | None = None
                perimeter = sum(edges[index].length for index in edge_ids)
                if closed and valid:
                    try:
                        face = Face(wire)
                        area_value = float(face.area)
                        if not bool(face.is_valid) or not math.isfinite(area_value) or area_value <= tolerance * tolerance:
                            issues.append("OCCT could not establish a valid positive-area planar contour.")
                            valid = False
                            face = None
                        else:
                            area = area_value
                    except Exception as exc:
                        _raise_if_memory_exhaustion(exc)
                        issues.append(f"OCCT face validation failed: {type(exc).__name__}: {exc}")
                        valid = False
                        face = None
                contour = _Contour(
                    identifier=f"contour-{len(analysis['contours']) + 1}",
                    edge_ids=edge_ids,
                    wire=wire,
                    face=face,
                    sources=component_sources,
                    closed=closed,
                    valid=valid,
                    area=area,
                    perimeter=perimeter,
                    bounds=component_bounds,
                    issues=issues,
                )
                analysis["contours"].append(contour)
            except Exception as exc:
                _raise_if_memory_exhaustion(exc)
                analysis["unsupported"].append(
                    {
                        "entities": component_sources,
                        "stage": "wire_validation",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
                analysis["open_cut_paths"].append(
                    {
                        "id": f"cut-path-{component_index + 1}",
                        "source_entities": component_sources,
                        "edge_count": len(edge_ids),
                        "closed": False,
                        "valid": False,
                        "bounds": component_bounds,
                        "issues": ["OCCT could not prove a single closed wire for this cut path."],
                    }
                )

        # Pairwise analytic intersections use the line/circle/arc records read
        # back from OCCT. Adjacent edges may meet at their shared topological
        # endpoint; all other contacts invalidate cut geometry.
        for first_index, first in enumerate(edges):
            for second_index in range(first_index + 1, len(edges)):
                second = edges[second_index]
                relation, points = _intersections(first.record, second.record, tolerance)
                if relation == "none":
                    continue
                if relation == "touch" and _is_expected_joint(first, second, points, node_degrees, tolerance):
                    continue
                item = {
                    "entities": list(dict.fromkeys((first.source, second.source))),
                    "edges": [
                        {"entity": first.source, "edge_index": first.index},
                        {"entity": second.source, "edge_index": second.index},
                    ],
                    "relation": relation,
                    "points": [[float(x), float(y), 0.0] for x, y in points],
                }
                analysis["intersections"].append(item)
                component_ids = {first.component, second.component}
                if relation == "overlap":
                    analysis["overlaps"].append(item)
                    if len(component_ids) > 1:
                        analysis["contour_intersections"].append(item)
                elif len(component_ids) == 1:
                    analysis["self_intersections"].append(item)
                    shared_nodes = {
                        first.start_node,
                        first.end_node,
                    }.intersection({second.start_node, second.end_node})
                    if relation == "touch" and any(node_degrees[node_id] > 2 for node_id in shared_nodes):
                        # Several otherwise closed contours can become one
                        # endpoint-connected component. Preserve the contour
                        # contact finding even though the graph has branched.
                        analysis["contour_intersections"].append(item)
                else:
                    analysis["contour_intersections"].append(item)

        edge_by_key = {(edge.source, edge.index): edge for edge in edges}
        affected_components = set()
        for item in analysis["intersections"]:
            for endpoint in item["edges"]:
                edge = edge_by_key.get((endpoint["entity"], endpoint["edge_index"]))
                if edge is not None:
                    affected_components.add(edge.component)
        for contour in analysis["contours"]:
            if any(edges[index].component in affected_components for index in contour.edge_ids):
                contour.valid = False
                contour.face = None
                contour.area = None
                if contour.issues is None:
                    contour.issues = []
                contour.issues.append("The contour crosses, touches, or overlaps another cut boundary.")

        valid_contours = [contour for contour in analysis["contours"] if contour.valid and contour.face is not None]
        if valid_contours:
            from OCP.BRepClass import BRepClass_FaceClassifier
            from OCP.TopAbs import TopAbs_IN, TopAbs_ON
            from OCP.gp import gp_Pnt

            parents: dict[str, list[_Contour]] = {contour.identifier: [] for contour in valid_contours}
            nesting_uncertain: set[str] = set()
            for child in valid_contours:
                sample_edge = child.wire.edges()[0]
                sample = sample_edge.position_at(0.371)
                sample_point = gp_Pnt(float(sample.X), float(sample.Y), 0.0)
                for parent in valid_contours:
                    if parent.identifier == child.identifier or float(parent.area) <= float(child.area) + tolerance * tolerance:
                        continue
                    classifier = BRepClass_FaceClassifier(
                        parent.face.wrapped,
                        sample_point,
                        tolerance,
                    )
                    state = classifier.State()
                    if state == TopAbs_ON:
                        nesting_uncertain.update((child.identifier, parent.identifier))
                    elif state == TopAbs_IN:
                        parents[child.identifier].append(parent)
            changed = True
            while changed:
                changed = False
                for child in valid_contours:
                    if child.identifier in nesting_uncertain:
                        continue
                    if any(parent.identifier in nesting_uncertain for parent in parents[child.identifier]):
                        nesting_uncertain.add(child.identifier)
                        changed = True
            for contour in valid_contours:
                if contour.identifier in nesting_uncertain:
                    contour.valid = False
                    contour.face = None
                    contour.area = None
                    if contour.issues is None:
                        contour.issues = []
                    contour.issues.append("OCCT could not distinguish nested boundaries from a touching boundary.")
                    continue
                containing = [
                    parent for parent in parents[contour.identifier]
                    if parent.identifier not in nesting_uncertain
                ]
                parent = min(containing, key=lambda candidate: float(candidate.area)) if containing else None
                contour.parent = parent.identifier if parent is not None else None

            valid_contours = [contour for contour in valid_contours if contour.valid]

            def contour_depth(contour: _Contour, visited: set[str] | None = None) -> int:
                if contour.depth is not None:
                    return contour.depth
                visited = set() if visited is None else visited
                if contour.identifier in visited:
                    raise ValueError("Contour containment cycle detected.")
                visited.add(contour.identifier)
                parent = next((candidate for candidate in valid_contours if candidate.identifier == contour.parent), None)
                contour.depth = 0 if parent is None else contour_depth(parent, visited) + 1
                return contour.depth

            for contour in valid_contours:
                if contour.valid:
                    try:
                        contour_depth(contour)
                    except Exception as exc:
                        _raise_if_memory_exhaustion(exc)
                        contour.valid = False
                        contour.face = None
                        contour.area = None
                        if contour.issues is None:
                            contour.issues = []
                        contour.issues.append(f"Nesting depth could not be established: {exc}")

            for contour in valid_contours:
                if not contour.valid or contour.depth is None or contour.depth % 2 == 0:
                    continue
                circle = _circular_loop(edges, contour.edge_ids, tolerance)
                if circle is None:
                    continue
                center, radius = circle
                analysis["holes"].append(
                    {
                        "contour": contour.identifier,
                        "center": [center[0], center[1], 0.0],
                        "diameter": 2.0 * radius,
                        "source_entities": contour.sources,
                        "nesting_depth": contour.depth,
                    }
                )

        valid_contours = [contour for contour in analysis["contours"] if contour.valid and contour.face is not None]
        for first_index, first in enumerate(valid_contours):
            for second in valid_contours[first_index + 1 :]:
                try:
                    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

                    distance = BRepExtrema_DistShapeShape(first.wire.wrapped, second.wire.wrapped)
                    distance.Perform()
                    if not distance.IsDone():
                        raise RuntimeError("OCCT did not complete the boundary-distance operation.")
                    measured_distance = float(distance.Value())
                    if not math.isfinite(measured_distance):
                        raise RuntimeError("OCCT returned a non-finite boundary distance.")
                    if measured_distance <= tolerance:
                        for contour in (first, second):
                            contour.valid = False
                            contour.face = None
                            contour.area = None
                            if contour.issues is None:
                                contour.issues = []
                            contour.issues.append("A boundary clearance is within the native geometry tolerance.")
                        analysis["contour_intersections"].append(
                            {
                                "entities": list(dict.fromkeys(first.sources + second.sources)),
                                "relation": "touch",
                                "points": [],
                            }
                        )
                        continue
                    analysis["boundary_clearances"].append(
                        {
                            "contours": [first.identifier, second.identifier],
                            "entities": [first.sources, second.sources],
                            "distance": measured_distance,
                            "method": "OCCT BRepExtrema_DistShapeShape",
                        }
                    )
                except Exception as exc:
                    _raise_if_memory_exhaustion(exc)
                    analysis["clearance_complete"] = False
                    analysis["unsupported"].append(
                        {
                            "entities": list(dict.fromkeys(first.sources + second.sources)),
                            "stage": "boundary_clearance",
                            "reason": f"{type(exc).__name__}: {exc}",
                        }
                    )
        valid_ids = {contour.identifier for contour in analysis["contours"] if contour.valid and contour.face is not None}
        analysis["holes"] = [hole for hole in analysis["holes"] if hole["contour"] in valid_ids]
        analysis["boundary_clearances"] = [
            clearance
            for clearance in analysis["boundary_clearances"]
            if set(clearance["contours"]).issubset(valid_ids)
        ]
    except Exception as exc:
        _raise_if_memory_exhaustion(exc)
        analysis["unsupported"].append(
            {
                "stage": "occt_validation",
                "reason": f"{type(exc).__name__}: {exc}",
            }
        )
        analysis["clearance_complete"] = False

    has_invalid = bool(
        analysis["open_cut_paths"]
        or analysis["self_intersections"]
        or analysis["overlaps"]
        or analysis["contour_intersections"]
        or any(not contour.valid for contour in analysis["contours"])
    )
    if has_invalid:
        analysis["status"] = {
            "state": "invalid",
            "complete": False,
            "message": "At least one cut path is open, intersecting, overlapping, touching, or not a valid OCCT contour.",
        }
    elif analysis["unsupported"]:
        analysis["status"] = {
            "state": "partial",
            "complete": False,
            "message": "Some native 2D measurements are unsupported or unavailable; no unsupported result is treated as proven.",
        }
    else:
        analysis["status"] = {
            "state": "complete",
            "complete": True,
            "message": "All native cut paths were measured with exact line/circle/arc geometry and OCCT topology checks.",
        }
    return analysis


def _contour_evidence(analysis: Mapping[str, Any], contour: _Contour) -> dict[str, Any]:
    edges: list[_NativeEdge] = analysis["edges"]
    valid = contour.valid and contour.face is not None
    return {
        "id": contour.identifier,
        "closed": contour.closed,
        "valid": valid,
        "area": contour.area if valid else None,
        "perimeter": contour.perimeter if contour.closed else None,
        "bounds": contour.bounds,
        "nesting_depth": contour.depth if valid else None,
        "parent": contour.parent if valid else None,
        "source_entities": contour.sources,
        "edges": [_edge_evidence(edges[index]) for index in contour.edge_ids],
        "issues": list(contour.issues or []),
    }


def _evidence(analysis: Mapping[str, Any]) -> dict[str, Any]:
    bounds = analysis["bounds"]
    bbox = analysis["bbox"]
    contours = analysis["contours"]
    evidence = {
        "units": analysis["units"],
        "bounds": bounds,
        "bbox": bbox,
        "cut_entities": analysis["cut_entities"],
        "closed_contours": [_contour_evidence(analysis, contour) for contour in contours if contour.closed],
        "open_cut_paths": analysis["open_cut_paths"],
        "holes": list(analysis["holes"]),
        "boundary_clearances": list(analysis["boundary_clearances"]),
        "annotations": list(analysis["annotation_entities"]),
        "unsupported": list(analysis["unsupported"]),
        "status": dict(analysis["status"]),
        "geometry_source": "native OCCT edges created from Sheet2D primitives",
        "topology_tolerance": float(analysis["tolerance"]),
    }
    from .openings2d import measured_openings
    evidence["openings"], evidence["unsupported_openings"] = measured_openings(evidence)
    # Ensure accidental kernel wrappers or non-finite floats never escape the
    # evidence API. This also documents the JSON-native guarantee at runtime.
    try:
        return json.loads(json.dumps(evidence, allow_nan=False))
    except (TypeError, ValueError) as exc:
        return {
            "units": analysis["units"],
            "bounds": None,
            "bbox": None,
            "cut_entities": [],
            "closed_contours": [],
            "open_cut_paths": [],
            "holes": [],
            "boundary_clearances": [],
            "annotations": list(analysis["annotation_entities"]),
            "unsupported": list(analysis["unsupported"]) + [
                {"stage": "json_serialization", "reason": f"{type(exc).__name__}: {exc}"}
            ],
            "status": {
                "state": "partial",
                "complete": False,
                "message": "Evidence could not be serialized as finite JSON-native values.",
            },
            "geometry_source": "native OCCT edges created from Sheet2D primitives",
            "topology_tolerance": float(analysis["tolerance"]),
        }


def measurement_evidence(ctx: Any) -> dict[str, Any]:
    """Return JSON-safe measurements and provenance for native cut geometry.

    ``ctx.sheet`` may be a :class:`Sheet2D` or its JSON-native ``to_dict``
    mapping. The context and sheet units must match. Only the ``cut`` layer
    (or a layer whose metadata role is ``cut``) participates in contour checks.
    """

    return _evidence(_analyze(ctx))


def _laser_process(ctx: Any) -> bool:
    process = _value(ctx, "process")
    return isinstance(process, str) and process.strip().lower() in {"laser", "laser_cutting", "laser_cut"}


def _parse_limits(limits: Any) -> tuple[dict[str, float | None], str | None, bool]:
    parsed: dict[str, float | None] = {"kerf": None, "min_slot": None}
    if limits is None:
        return parsed, None, False
    if not isinstance(limits, Mapping):
        return parsed, "limits must be a mapping containing optional 'kerf' and 'min_slot' values.", True
    unknown = set(limits).difference(parsed)
    if unknown:
        return parsed, f"Unsupported laser limit key(s): {', '.join(sorted(map(str, unknown)))}.", True
    for name in parsed:
        if name not in limits:
            continue
        value = limits[name]
        if isinstance(value, bool):
            return parsed, f"limits.{name} must be a finite positive number, not a boolean.", True
        if not isinstance(value, Real):
            return parsed, f"limits.{name} must be a finite numeric value.", True
        number = float(value)
        if not math.isfinite(number) or number <= 0.0:
            return parsed, f"limits.{name} must be finite and greater than zero.", True
        parsed[name] = number
    return parsed, None, False


def _laser_checks(analysis: Mapping[str, Any], limits: Any) -> list[dict[str, Any]]:
    parsed, error, invalid = _parse_limits(limits)
    if limits is None:
        limit_status = "skip"
        limit_message = "No caller-provided laser limits were supplied; no kerf or minimum-slot value is assumed."
    elif invalid:
        limit_status = "fail"
        limit_message = error or "Laser limits are invalid."
    elif parsed["kerf"] is None and parsed["min_slot"] is None:
        limit_status = "skip"
        limit_message = "No kerf or min_slot value was supplied; no process limit is assumed."
    else:
        limit_status = "pass"
        limit_message = "Caller-provided laser limits are finite, positive, and expressed in sheet native units."
    result = [
        _check(
            "2d.laser.limits",
            "warn",
            limit_status,
            limit_message,
            expected="caller-supplied finite positive limits in sheet native units",
            actual=error if invalid else _json_text(parsed),
        )
    ]
    evidence = _evidence(analysis)
    if invalid:
        result.extend(
            [
                _check("2d.laser.kerf", "warn", "skip", "Kerf clearance was not evaluated because the supplied limits are invalid."),
                _check("2d.laser.circular_openings", "warn", "skip", "Circular openings were not compared because the supplied limits are invalid."),
                _check("2d.laser.min_slot", "warn", "skip", "General minimum-slot evaluation was not possible because the supplied limits are invalid."),
            ]
        )
        return result

    kerf = parsed["kerf"]
    clearances = evidence["boundary_clearances"]
    if kerf is None:
        kerf_check = _check(
            "2d.laser.kerf",
            "warn",
            "skip",
            "No caller-provided kerf limit was supplied.",
            expected="explicit kerf limit",
            actual="unavailable",
        )
    elif not clearances:
        kerf_check = _check(
            "2d.laser.kerf",
            "warn",
            "skip",
            "No valid pair of separate closed cut contours had a measurable boundary clearance.",
            expected=f"boundary clearance >= {kerf:g} {analysis['units']}",
            actual="no relevant contour pairs",
            heuristic=True,
            assumptions="This check compares exact contour-to-contour boundary distances with the caller's kerf. It assumes contours are nominal boundaries and screens whether one kerf allowance can consume the gap; it is not a toolpath simulation.",
        )
    else:
        comparisons = [
            {
                "contours": item["contours"],
                "entities": item["entities"],
                "clearance": item["distance"],
                "kerf": kerf,
                "remaining_after_one_kerf": item["distance"] - kerf,
            }
            for item in clearances
        ]
        below = [item for item in comparisons if item["clearance"] < kerf]
        incomplete = analysis["status"]["state"] != "complete" or not analysis["clearance_complete"]
        status = "fail" if below else "warn" if incomplete else "pass"
        kerf_check = _check(
            "2d.laser.kerf",
            "warn",
            status,
            (
                f"{len(below)} measured boundary clearance(s) are below the caller's kerf."
                if below
                else f"Measured {len(comparisons)} exact boundary clearance(s) against the caller's kerf."
            ),
            expected=f"each measured boundary clearance >= {kerf:g} {analysis['units']}",
            actual=_json_text(comparisons),
            heuristic=True,
            assumptions="OCCT measures distances between valid, distinct closed cut contours. The caller's kerf is treated as the total gap consumed by adjacent cuts; this is a narrow contour-clearance screen, not a toolpath simulation or a general slot-width proof.",
        )
    result.append(kerf_check)

    min_slot = parsed["min_slot"]
    holes = evidence["holes"]
    if min_slot is None:
        opening_check = _check(
            "2d.laser.circular_openings",
            "warn",
            "skip",
            "No caller-provided min_slot limit was supplied for the measured circular cutouts.",
            actual=_json_text(holes),
        )
    elif not holes:
        opening_check = _check(
            "2d.laser.circular_openings",
            "warn",
            "skip",
            "No valid interior circular cutouts were measured for comparison with min_slot.",
            expected=f"circular cutout diameter >= {min_slot:g} {analysis['units']}",
            actual="no measured interior circular cutouts",
            heuristic=True,
            assumptions="The caller's min_slot is applied only as a circular-opening screen. It does not characterize non-circular slots.",
        )
    else:
        comparisons = [
            {
                "center": hole["center"],
                "diameter": hole["diameter"],
                "min_slot": min_slot,
                "source_entities": hole["source_entities"],
            }
            for hole in holes
        ]
        below = [item for item in comparisons if item["diameter"] < min_slot]
        incomplete = analysis["status"]["state"] != "complete"
        status = "fail" if below else "warn" if incomplete else "pass"
        opening_check = _check(
            "2d.laser.circular_openings",
            "warn",
            status,
            (
                f"{len(below)} measured circular opening(s) are narrower than the caller's min_slot."
                if below
                else f"Measured {len(comparisons)} circular cutout diameter(s) against the caller's min_slot."
            ),
            expected=f"each measured circular cutout diameter >= {min_slot:g} {analysis['units']}",
            actual=_json_text(comparisons),
            heuristic=True,
            assumptions="The caller's min_slot is applied to true circular interior cutouts as a narrow opening screen. This does not characterize non-circular slots, kerf compensation, or material-specific rules.",
        )
    result.append(opening_check)

    from .openings2d import min_slot_check
    slot_status, slot_message, slot_actual = (min_slot_check(evidence, min_slot) if min_slot is not None else
        ("skip", "No caller-provided min_slot limit was supplied.", {}))
    result.append(
        _check(
            "2d.laser.min_slot",
            "warn",
            slot_status,
            slot_message,
            expected=f"interior cutout width >= {min_slot:g} {analysis['units']}" if min_slot is not None else "explicit min_slot limit",
            actual=_json_text(slot_actual),
            heuristic=True,
            assumptions="min_slot means the nominal width of closed interior cutouts. Exact circles, rectangles and obrounds are recognized from measured native edges; other contours remain unsupported. This does not establish outside notch widths, minimum webs or compensated toolpaths.",
        )
    )
    return result


def checks(ctx: Any, *, limits: Mapping[str, float] | None = None) -> list[dict[str, Any]]:
    """Validate native 2D cut contours and optionally screen laser limits.

    The only accepted laser keys are ``kerf`` and ``min_slot``. Values are
    positive finite numbers in the sheet's native units. Laser checks run when
    ``ctx.process`` names laser cutting or the caller supplies ``limits``.
    """

    analysis = _analyze(ctx)
    if analysis["input_error"] is not None:
        result = [
            _check(
                "2d.input",
                "error",
                "fail",
                str(analysis["input_error"]),
                expected="ctx.sheet is a valid Sheet2D or JSON-native mapping and its units match ctx.units",
            )
        ]
        result.extend(
            [
                _check("2d.closed_contours", "error", "skip", "Closed-contour validation was unavailable because the sheet input is invalid."),
                _check("2d.self_intersections", "error", "skip", "Self-intersection validation was unavailable because the sheet input is invalid."),
                _check("2d.overlaps", "error", "skip", "Overlap validation was unavailable because the sheet input is invalid."),
                _check("2d.contour_intersections", "error", "skip", "Contour-intersection validation was unavailable because the sheet input is invalid."),
                _check("2d.measurement", "warn", "skip", "Native 2D measurement evidence is unavailable because the sheet input is invalid."),
            ]
        )
        if _laser_process(ctx) or limits is not None:
            result.extend(_laser_checks(analysis, limits))
        return result

    if analysis["no_cut_geometry"]:
        result = [
            _check("2d.input", "error", "pass", "Sheet input and context units are valid."),
            _check("2d.closed_contours", "error", "skip", "No cut-layer geometry is available to validate."),
            _check("2d.self_intersections", "error", "skip", "No cut-layer geometry is available to validate."),
            _check("2d.overlaps", "error", "skip", "No cut-layer geometry is available to validate."),
            _check("2d.contour_intersections", "error", "skip", "No cut-layer geometry is available to validate."),
            _check("2d.measurement", "warn", "skip", "No cut-layer geometry is available for native measurement."),
        ]
        if _laser_process(ctx) or limits is not None:
            result.extend(_laser_checks(analysis, limits))
        return result

    contours: list[_Contour] = analysis["contours"]
    open_paths = analysis["open_cut_paths"]
    unsupported = analysis["unsupported"]
    has_open = bool(open_paths)
    has_nonclosed = any(not contour.closed for contour in contours)
    closed_status = "fail" if has_open or has_nonclosed else "skip" if unsupported and not contours else "pass"
    closed_actual = _json_text(
        {
            "closed_contours": len(contours),
            "open_or_branched_paths": len(open_paths),
            "invalid_closed_contours": sum(not contour.valid for contour in contours),
        }
    )
    if closed_status == "pass" and unsupported:
        closed_status = "warn"
    result = [
        _check("2d.input", "error", "pass", "Sheet input and context units are valid."),
        _check(
            "2d.closed_contours",
            "error",
            closed_status,
            (
                f"Found {len(open_paths)} open or branched cut path(s); every cut contour must close."
                if has_open or has_nonclosed
                else "All measured cut paths form closed OCCT wires."
                if closed_status in {"pass", "warn"}
                else "Closed-contour validation is incomplete because native geometry is unsupported."
            ),
            expected="every cut path forms a closed wire",
            actual=closed_actual,
        ),
    ]
    self_status = "fail" if analysis["self_intersections"] else "skip" if not analysis["edges"] else "pass"
    if unsupported and self_status == "pass":
        self_status = "warn"
    result.append(
        _check(
            "2d.self_intersections",
            "error",
            self_status,
            (
                f"Found {len(analysis['self_intersections'])} non-adjacent self-intersection(s) in cut geometry."
                if analysis["self_intersections"]
                else "No self-intersections were found in the measured native cut edges."
                if self_status in {"pass", "warn"}
                else "Self-intersection validation was unavailable."
            ),
            expected="closed cut contours do not self-intersect",
            actual=_json_text(analysis["self_intersections"]),
        )
    )
    overlap_status = "fail" if analysis["overlaps"] else "skip" if not analysis["edges"] else "pass"
    if unsupported and overlap_status == "pass":
        overlap_status = "warn"
    result.append(
        _check(
            "2d.overlaps",
            "error",
            overlap_status,
            (
                f"Found {len(analysis['overlaps'])} duplicate or overlapping cut-edge pair(s)."
                if analysis["overlaps"]
                else "No duplicate or overlapping cut edges were found."
                if overlap_status in {"pass", "warn"}
                else "Overlap validation was unavailable."
            ),
            expected="no duplicate or partially overlapping cut paths",
            actual=_json_text(analysis["overlaps"]),
        )
    )
    intersection_status = "fail" if analysis["contour_intersections"] else "skip" if not analysis["edges"] else "pass"
    if unsupported and intersection_status == "pass":
        intersection_status = "warn"
    result.append(
        _check(
            "2d.contour_intersections",
            "error",
            intersection_status,
            (
                f"Found {len(analysis['contour_intersections'])} crossing or touching cut-contour pair(s)."
                if analysis["contour_intersections"]
                else "No crossing or touching between distinct cut contours was found."
                if intersection_status in {"pass", "warn"}
                else "Contour-intersection validation was unavailable."
            ),
            expected="distinct cut contours do not cross or touch",
            actual=_json_text(analysis["contour_intersections"]),
        )
    )
    evidence_state = analysis["status"]["state"]
    measurement_status = "fail" if evidence_state == "invalid" else "warn" if evidence_state == "partial" else "pass"
    if evidence_state == "unavailable":
        measurement_status = "skip"
    result.append(
        _check(
            "2d.measurement",
            "warn",
            measurement_status,
            str(analysis["status"]["message"]),
            expected="native measurements are complete and invalid contours are excluded from proven evidence",
            actual=_json_text(
                {
                    "state": evidence_state,
                    "holes": len(analysis["holes"]),
                    "boundary_clearances": len(analysis["boundary_clearances"]),
                    "unsupported": len(unsupported),
                }
            ),
        )
    )
    if _laser_process(ctx) or limits is not None:
        result.extend(_laser_checks(analysis, limits))
    return result


__all__ = ["checks", "measurement_evidence"]
