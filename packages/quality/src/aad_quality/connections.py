"""Geometric evidence for declared assembly connections and physical touching."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from aad.engine.memory import is_memory_exhaustion

from . import spec_geometry


CONNECTION_CHECK_BUDGET_S = 60.0
_DETAIL_LIMIT = 100
_UNKNOWN_DETAIL_LIMIT = 20


class _UnionFind:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))
        self.rank = [0] * count

    def find(self, value: int) -> int:
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            next_value = self.parent[value]
            self.parent[value] = root
            value = next_value
        return root

    def union(self, first: int, second: int) -> None:
        a, b = self.find(first), self.find(second)
        if a == b:
            return
        if self.rank[a] < self.rank[b]:
            a, b = b, a
        self.parent[b] = a
        if self.rank[a] == self.rank[b]:
            self.rank[a] += 1


@dataclass
class ConnectionEvidence:
    report: dict[str, Any]
    unknown: bool = False


def _assembly_parts(ctx: Any) -> tuple[list[str], list[Any], list[Any]]:
    shape = getattr(ctx, "shape", None)
    if shape is None:
        raise ValueError("assembly B-rep is unavailable")
    meta = getattr(ctx, "meta", None)
    assembly = meta.get("assembly") if isinstance(meta, dict) else None
    if not isinstance(assembly, dict):
        assembly = {}

    children = list(getattr(shape, "children", []) or [])
    declared_names = assembly.get("components")
    if declared_names is not None:
        if not isinstance(declared_names, list) or any(not isinstance(name, str) or not name for name in declared_names):
            raise ValueError("assembly component labels are malformed")
        names = list(declared_names)
        if len(children) != len(names):
            solids = list(shape.solids())
            if len(solids) != len(names):
                raise ValueError("assembly labels cannot be matched to component B-reps")
            children = solids
    elif children:
        names = [str(getattr(child, "label", "") or f"component_{index + 1}") for index, child in enumerate(children)]
    else:
        children = list(shape.solids())
        names = [str(getattr(child, "label", "") or f"component_{index + 1}") for index, child in enumerate(children)]

    if not names:
        raise ValueError("assembly has no identifiable components")
    if len(set(names)) != len(names):
        raise ValueError("assembly component labels are not unique")
    joints = assembly.get("joints", [])
    if not isinstance(joints, list):
        raise ValueError("assembly joint metadata is malformed")
    return names, children, joints


def _bounds(shape: Any) -> tuple[float, float, float, float, float, float]:
    box = shape.bounding_box()
    return (
        float(box.min.X), float(box.max.X),
        float(box.min.Y), float(box.max.Y),
        float(box.min.Z), float(box.max.Z),
    )


def _bbox_gap_mm(
    first: tuple[float, float, float, float, float, float],
    second: tuple[float, float, float, float, float, float],
    mm_per_native: float,
) -> float:
    gaps = (
        max(0.0, first[0] - second[1], second[0] - first[1]),
        max(0.0, first[2] - second[3], second[2] - first[3]),
        max(0.0, first[4] - second[5], second[4] - first[5]),
    )
    distance = math.sqrt(sum(gap * gap for gap in gaps)) * mm_per_native
    if not math.isfinite(distance):
        raise ValueError("component bounding-box distance is invalid")
    return distance


def _component_paths(graph: _UnionFind, names: list[str], ground_index: int | None, allowed: set[str]) -> list[str]:
    if ground_index is None:
        return [name for name in names if name not in allowed]
    ground_root = graph.find(ground_index)
    return [
        name for index, name in enumerate(names)
        if name not in allowed and graph.find(index) != ground_root
    ]


def connected_evidence(
    ctx: Any,
    *,
    ground: str,
    allow_floating: list[str],
    tolerance_mm: float,
    mm_per_native: float,
) -> ConnectionEvidence:
    """Return separate, fail-closed declared-joint and touching graph evidence.

    A declared joint is an edge only after its pair's minimum B-rep distance is
    measured within the joint tolerance. Touching is measured independently and
    is never promoted to fastening evidence.
    """
    started = time.monotonic()
    deadline = started + CONNECTION_CHECK_BUDGET_S
    names, shapes, joints = _assembly_parts(ctx)
    index_by_name = {name: index for index, name in enumerate(names)}
    ground_index = index_by_name.get(ground)
    allowed = set(allow_floating)
    bad_references: list[str] = []
    if ground_index is None:
        bad_references.append(f"ground {ground!r} is not an assembly component")
    for name in allow_floating:
        if name not in index_by_name:
            bad_references.append(f"allow_floating component {name!r} is not in the assembly")

    unknown_count = 0
    unknown_details: list[str] = []

    def mark_unknown(message: str) -> None:
        nonlocal unknown_count
        unknown_count += 1
        if len(unknown_details) < _UNKNOWN_DETAIL_LIMIT:
            unknown_details.append(message)

    bounds: list[tuple[float, float, float, float, float, float] | None] = []
    for name, component in zip(names, shapes, strict=True):
        try:
            bounds.append(_bounds(component))
        except Exception as exc:
            if is_memory_exhaustion(exc):
                raise
            mark_unknown(f"{name}: bounding box failed ({type(exc).__name__}: {exc})")
            bounds.append(None)

    joint_graph = _UnionFind(len(names))
    contact_graph = _UnionFind(len(names))
    measured: dict[tuple[int, int], float] = {}
    measured_unknown: set[tuple[int, int]] = set()
    joint_reports: list[dict[str, Any]] = []
    joint_failures: list[str] = []
    validated_joint_count = 0
    timed_out = False

    def pair_key(first: int, second: int) -> tuple[int, int]:
        return (first, second) if first < second else (second, first)

    def measure_pair(first: int, second: int) -> float | None:
        nonlocal timed_out
        key = pair_key(first, second)
        if key in measured:
            return measured[key]
        if key in measured_unknown:
            return None
        if time.monotonic() >= deadline:
            mark_unknown("connection graph check exceeded its 60 second geometry budget")
            timed_out = True
            return None
        try:
            distance = spec_geometry.distance_mm(shapes[first], shapes[second], mm_per_native)
        except Exception as exc:
            if is_memory_exhaustion(exc):
                raise
            measured_unknown.add(key)
            mark_unknown(f"{names[first]} to {names[second]}: minimum B-rep distance failed ({type(exc).__name__}: {exc})")
            return None
        measured[key] = distance
        return distance

    for joint_index, joint in enumerate(joints):
        if time.monotonic() >= deadline:
            mark_unknown("connection graph check exceeded its 60 second geometry budget")
            break
        if not isinstance(joint, dict):
            mark_unknown(f"joint {joint_index + 1}: joint record is malformed")
            continue
        first_name, second_name = joint.get("a"), joint.get("b")
        if (
            not isinstance(first_name, str)
            or not isinstance(second_name, str)
            or first_name not in index_by_name
            or second_name not in index_by_name
            or first_name == second_name
        ):
            joint_failures.append(f"joint {joint_index + 1} has a bad component reference ({first_name!r}, {second_name!r})")
            continue
        details = joint.get("details", {})
        if not isinstance(details, dict):
            mark_unknown(f"joint {joint_index + 1} ({first_name} to {second_name}): details are malformed")
            continue
        joint_tolerance = details.get("tolerance", tolerance_mm / mm_per_native)
        if isinstance(joint_tolerance, bool) or not isinstance(joint_tolerance, (int, float)):
            mark_unknown(f"joint {joint_index + 1} ({first_name} to {second_name}): tolerance is invalid")
            continue
        joint_tolerance_mm = float(joint_tolerance) * mm_per_native
        if not math.isfinite(joint_tolerance_mm) or joint_tolerance_mm < 0:
            mark_unknown(f"joint {joint_index + 1} ({first_name} to {second_name}): tolerance is invalid")
            continue

        first, second = index_by_name[first_name], index_by_name[second_name]
        if bounds[first] is None or bounds[second] is None:
            mark_unknown(f"{first_name} to {second_name}: component bounding box is unavailable")
            continue
        distance = measure_pair(first, second)
        if timed_out:
            break
        if distance is None:
            joint_reports.append({"a": first_name, "b": second_name, "kind": joint.get("kind"), "status": "unknown", "tolerance_mm": joint_tolerance_mm})
            continue
        if distance > joint_tolerance_mm:
            failure = (
                f"{first_name} to {second_name} ({joint.get('kind', 'joint')}): minimum distance "
                f"{distance:.8g} mm exceeds joint tolerance {joint_tolerance_mm:.8g} mm"
            )
            joint_failures.append(failure)
            joint_reports.append({"a": first_name, "b": second_name, "kind": joint.get("kind"), "status": "fail", "distance_mm": distance, "tolerance_mm": joint_tolerance_mm})
            continue
        validated_joint_count += 1
        joint_graph.union(first, second)
        joint_reports.append({"a": first_name, "b": second_name, "kind": joint.get("kind"), "status": "pass", "distance_mm": distance, "tolerance_mm": joint_tolerance_mm})

    # Sweep by expanded X bounds so separated large assemblies avoid all-pairs
    # B-rep distance work. Exact distance is only requested for broad-phase pairs.
    order = sorted(
        (index for index, value in enumerate(bounds) if value is not None),
        key=lambda item: bounds[item][0],
    )
    active: list[int] = []
    contact_pairs: list[dict[str, Any]] = []
    contact_pair_count = 0
    for current in order:
        if time.monotonic() >= deadline:
            mark_unknown("connection graph check exceeded its 60 second geometry budget")
            timed_out = True
            break
        current_bounds = bounds[current]
        assert current_bounds is not None
        current_min_x = current_bounds[0]
        active = [
            prior for prior in active
            if bounds[prior] is not None and bounds[prior][1] + tolerance_mm / mm_per_native >= current_min_x
        ]
        for prior in active:
            if time.monotonic() >= deadline:
                mark_unknown("connection graph check exceeded its 60 second geometry budget")
                timed_out = True
                break
            prior_bounds = bounds[prior]
            assert prior_bounds is not None
            try:
                bbox_gap = _bbox_gap_mm(prior_bounds, current_bounds, mm_per_native)
            except Exception as exc:
                if is_memory_exhaustion(exc):
                    raise
                mark_unknown(f"{names[prior]} to {names[current]}: bounding-box distance failed ({type(exc).__name__}: {exc})")
                continue
            if bbox_gap > tolerance_mm:
                continue
            distance = measure_pair(prior, current)
            if timed_out:
                break
            if distance is None or distance > tolerance_mm:
                continue
            contact_graph.union(prior, current)
            contact_pair_count += 1
            if len(contact_pairs) < _DETAIL_LIMIT:
                contact_pairs.append({"a": names[prior], "b": names[current], "distance_mm": distance})
        if timed_out:
            break
        active.append(current)

    joint_unconnected = _component_paths(joint_graph, names, ground_index, allowed)
    contact_unconnected = _component_paths(contact_graph, names, ground_index, allowed)
    report = {
        "ground": ground,
        "allow_floating": [name for name in allow_floating if name in index_by_name],
        "components": names,
        "joint_count": len(joints),
        "validated_joint_count": validated_joint_count,
        "joint_checks": joint_reports[:_DETAIL_LIMIT],
        "joint_checks_truncated": len(joint_reports) > _DETAIL_LIMIT,
        "joint_failures": joint_failures[:_DETAIL_LIMIT],
        "joint_failures_truncated": len(joint_failures) > _DETAIL_LIMIT,
        "bad_references": bad_references,
        "unconnected_by_joints": joint_unconnected,
        "contact_pair_count": contact_pair_count,
        "contact_pairs": contact_pairs,
        "contact_pairs_truncated": contact_pair_count > len(contact_pairs),
        "unconnected_by_contact": contact_unconnected,
        "unknown_count": unknown_count,
        "unknown_details": unknown_details,
        "timed_out": timed_out,
    }
    return ConnectionEvidence(report, unknown=unknown_count > 0)


__all__ = ["CONNECTION_CHECK_BUDGET_S", "ConnectionEvidence", "connected_evidence"]
