"""Geometric hard checks for a built B-rep shape."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, fields
from heapq import heappush, heapreplace
import json
from math import dist, floor, fsum, hypot, isfinite
from operator import index as integer_index
from pathlib import Path
import tempfile
from time import monotonic, perf_counter
from typing import Any

from .memory import is_memory_exhaustion
from .types import Check, ModelContext


@dataclass(frozen=True)
class GateTolerances:
    """Comparison tolerances; length, area, and volume values are in mm units."""

    min_edge_length_mm: float = 0.01
    min_face_area_mm2: float = 0.001
    min_face_width_mm: float = 0.001
    step_volume_abs_mm3: float = 1e-6
    step_volume_rel: float = 1e-6
    mesh_vertex_weld_mm: float = 1e-6
    mesh_triangle_area_mm2: float = 1e-12
    mesh_deflection_mm: float = 0.1
    mesh_angular_deflection_rad: float = 0.35


_GEOMETRY_CHECK_IDS = (
    "scene_coordinate_range",
    "brep_valid",
    "closed_shells",
    "solid_count",
    "edge_quality",
    "face_quality",
    "step_volume_drift",
    "mesh_watertight",
)
_LENGTH_TO_MM = {"mm": 1.0, "in": 25.4}
MAX_SCENE_COORDINATE_MM = 100_000.0
INTERFERENCE_VOLUME_THRESHOLD_MM3 = 1e-6
INTERFERENCE_TIME_BUDGET_S = 60.0
INTERFERENCE_MAX_TIME_BUDGET_S = 120.0
BUILD_INTERFERENCE_TIME_BUDGET_S = 15.0
INTERFERENCE_MAX_REPORTED_PAIRS = 20


def hard_checks(
    ctx: ModelContext,
    tolerances: GateTolerances | Mapping[str, float] | None = None,
) -> list[Check]:
    """Run error-level geometry checks; unavailable checks are error-level skips.

    ``tolerances`` may be a :class:`GateTolerances` or a mapping of its fields.
    Length, area, and volume thresholds use millimetres regardless of the
    project's declared units.
    """
    checks, _ = _hard_checks(ctx, tolerances)
    return checks


def _hard_checks_with_timings(
    ctx: ModelContext,
    tolerances: GateTolerances | Mapping[str, float] | None = None,
) -> tuple[list[Check], dict[str, float]]:
    """Return hard checks and their individual elapsed times for build results."""
    timings: dict[str, float] = {}
    checks, _ = _hard_checks(ctx, tolerances, timings)
    return checks, timings


def _hard_checks(
    ctx: ModelContext,
    tolerances: GateTolerances | Mapping[str, float] | None = None,
    timings: dict[str, float] | None = None,
) -> tuple[list[Check], dict[str, float]]:
    config, config_error = _normalize_tolerances(tolerances)
    checks: list[Check] = []
    if ctx.shape is None:
        checks.append(_timed_gate_check(
            timings, "shape_present", lambda: _check(
                "shape_present",
                "fail",
                "The model did not produce a B-rep shape.",
                expected="a non-empty B-rep shape",
                actual="no shape",
            ),
        ))
        if config_error is not None:
            checks.append(_timed_gate_check(
                timings, "gate_tolerances", lambda: _tolerance_error(config_error),
            ))
        checks.extend(_unavailable_geometry_checks("ctx.shape is None"))
        return checks, _complete_gate_timings(checks, timings)

    checks.append(_timed_gate_check(
        timings, "shape_present", lambda: _check(
            "shape_present",
            "pass",
            "A shape object is available for the hard checks.",
            expected="a B-rep shape object",
            actual=type(ctx.shape).__name__,
        ),
    ))
    if config_error is not None:
        checks.append(_timed_gate_check(
            timings, "gate_tolerances", lambda: _tolerance_error(config_error),
        ))

    unit_scale = _LENGTH_TO_MM.get(ctx.units) if isinstance(ctx.units, str) else None
    if unit_scale is None:
        checks.append(_timed_gate_check(
            timings, "gate_units", lambda: _check(
                "gate_units",
                "fail",
                "The configured geometric thresholds cannot be interpreted for this unit system.",
                expected="ctx.units is 'mm' or 'in'",
                actual=repr(ctx.units),
            ),
        ))

    coordinate_check = _timed_gate_check(
        timings,
        "scene_coordinate_range",
        lambda: _scene_coordinate_range_check(ctx.shape, unit_scale),
    )
    checks.append(coordinate_check)
    if coordinate_check.status == "fail":
        checks.extend(
            _skip(check_id, "not run because the shape exceeds the supported viewer coordinate range")
            for check_id in _GEOMETRY_CHECK_IDS
            if check_id != "scene_coordinate_range"
        )
        return checks, _complete_gate_timings(checks, timings)

    checks.append(_timed_gate_check(
        timings, "brep_valid", lambda: _brep_validity_check(ctx.shape),
    ))
    solid_check, multiple_solids_check = _timed_gate_check(
        timings, "solid_count", lambda: _solid_count_checks(ctx.shape, ctx.kind),
    )
    checks.append(solid_check)
    checks.append(_timed_gate_check(
        timings, "closed_shells", lambda: _closed_shell_check(ctx.shape),
    ))
    if multiple_solids_check is not None:
        checks.append(multiple_solids_check)

    unavailable_reason = config_error or (
        f"ctx.units={ctx.units!r} is unsupported; thresholds are declared in mm"
        if unit_scale is None
        else None
    )
    if unavailable_reason is not None:
        checks.extend(_unavailable_tolerance_checks(unavailable_reason))
        return checks, _complete_gate_timings(checks, timings)

    assert config is not None
    assert unit_scale is not None
    checks.append(_timed_gate_check(
        timings, "edge_quality", lambda: _edge_quality_check(ctx.shape, config, unit_scale),
    ))
    checks.append(_timed_gate_check(
        timings, "face_quality", lambda: _face_quality_check(ctx.shape, config, unit_scale),
    ))
    checks.append(_timed_gate_check(
        timings, "step_volume_drift", lambda: _step_volume_check(ctx, config, unit_scale),
    ))
    checks.append(_timed_gate_check(
        timings, "mesh_watertight", lambda: _mesh_watertight_check(ctx.shape, config, unit_scale, ctx.kind),
    ))
    return checks, _complete_gate_timings(checks, timings)


def _timed_gate_check(timings: dict[str, float] | None, check_id: str, operation: Any) -> Any:
    if timings is None:
        return operation()
    started = perf_counter()
    try:
        return operation()
    finally:
        timings[f"gate.{check_id}"] = (perf_counter() - started) * 1000


def _complete_gate_timings(checks: list[Check], timings: dict[str, float] | None) -> dict[str, float]:
    if timings is None:
        return {}
    for check in checks:
        timings.setdefault(f"gate.{check.id}", 0.0)
    return timings


def _scene_coordinate_range_check(shape: Any, unit_scale: float | None) -> Check:
    """Bound world coordinates so Float32 spacing stays below the 0.01 mm gate tolerance."""
    if unit_scale is None:
        return _skip("scene_coordinate_range", "not run because the unit system is unsupported")
    try:
        bounds = shape.bounding_box()
        coordinates = [
            float(bounds.min.X), float(bounds.min.Y), float(bounds.min.Z),
            float(bounds.max.X), float(bounds.max.Y), float(bounds.max.Z),
        ]
    except Exception as exc:
        return _skip("scene_coordinate_range", f"bounding-box extraction failed: {type(exc).__name__}: {exc}")
    if any(not isfinite(value) for value in coordinates):
        return _check(
            "scene_coordinate_range", "fail",
            "Scene coordinates must be finite before geometry can pass the hard gate.",
            expected=f"finite world coordinates within ±{MAX_SCENE_COORDINATE_MM:g} mm",
            actual=repr(coordinates),
        )
    maximum_mm = max(abs(value * unit_scale) for value in coordinates)
    if not isfinite(maximum_mm) or maximum_mm > MAX_SCENE_COORDINATE_MM:
        return _check(
            "scene_coordinate_range", "fail",
            f"The shape exceeds the supported viewer world-coordinate limit of ±{MAX_SCENE_COORDINATE_MM:g} mm.",
            expected=f"absolute world coordinates at or below {MAX_SCENE_COORDINATE_MM:g} mm",
            actual=f"maximum_absolute_coordinate={maximum_mm:g} mm",
        )
    return _check(
        "scene_coordinate_range", "pass",
        "World coordinates are finite and within the viewer's supported precision range.",
        expected=f"absolute world coordinates at or below {MAX_SCENE_COORDINATE_MM:g} mm",
        actual=f"maximum_absolute_coordinate={maximum_mm:g} mm",
    )


def _normalize_tolerances(
    tolerances: GateTolerances | Mapping[str, float] | None,
) -> tuple[GateTolerances | None, str | None]:
    if tolerances is None:
        candidate = GateTolerances()
    elif isinstance(tolerances, GateTolerances):
        candidate = tolerances
    elif isinstance(tolerances, Mapping):
        names = {item.name for item in fields(GateTolerances)}
        unknown = sorted((name for name in tolerances if name not in names), key=repr)
        if unknown:
            return None, f"unknown tolerance field(s): {', '.join(map(repr, unknown))}"
        candidate = GateTolerances(**dict(tolerances))
    else:
        return None, "tolerances must be GateTolerances, a field mapping, or None"

    positive = {"mesh_deflection_mm", "mesh_angular_deflection_rad"}
    values: dict[str, float] = {}
    for item in fields(GateTolerances):
        raw = getattr(candidate, item.name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None, f"{item.name} must be a finite number"
        try:
            value = float(raw)
        except (OverflowError, ValueError):
            return None, f"{item.name} must be a finite number"
        if not isfinite(value):
            return None, f"{item.name} must be finite"
        if item.name in positive and value <= 0:
            return None, f"{item.name} must be greater than zero"
        if item.name not in positive and value < 0:
            return None, f"{item.name} must be zero or greater"
        values[item.name] = value
    return GateTolerances(**values), None


def _tolerance_error(reason: str) -> Check:
    return _check(
        "gate_tolerances",
        "fail",
        "The geometric gate tolerances are invalid; threshold-based checks cannot run.",
        expected="finite, non-negative values with positive mesh deflections",
        actual=reason,
    )


def _unavailable_geometry_checks(reason: str) -> list[Check]:
    return [
        _skip(check_id, f"not run because {reason}")
        for check_id in _GEOMETRY_CHECK_IDS
    ]


def _unavailable_tolerance_checks(reason: str) -> list[Check]:
    return [
        _skip(check_id, f"not run because {reason}")
        for check_id in (
            "edge_quality",
            "face_quality",
            "step_volume_drift",
            "mesh_watertight",
        )
    ]


def _skip(check_id: str, reason: str, expected: str | None = None) -> Check:
    return _check(
        check_id,
        "skip",
        f"The {check_id.replace('_', ' ')} check could not run: {reason}.",
        expected=expected,
        actual=reason,
    )


def _check(
    check_id: str,
    status: str,
    message: str,
    *,
    expected: str | None = None,
    actual: str | None = None,
    level: str = "error",
    heuristic: bool | None = None,
    assumptions: str | None = None,
) -> Check:
    return Check(
        id=check_id,
        suite="hard",
        level=level,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        message=message,
        expected=expected,
        actual=actual,
        heuristic=heuristic,
        assumptions=assumptions,
    )


def _brep_validity_check(shape: Any) -> Check:
    try:
        from OCP.BRepCheck import BRepCheck_Analyzer

        analyzer_valid = bool(BRepCheck_Analyzer(shape.wrapped).IsValid())
        shape_valid = bool(shape.is_valid)  # build123d exposes this as a property.
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        reason = _exception_text(exc)
        return _skip(
            "brep_valid",
            f"BRepCheck_Analyzer or Shape.is_valid raised {reason}",
            expected="BRepCheck_Analyzer.IsValid() and Shape.is_valid are both true",
        )

    valid = analyzer_valid and shape_valid
    return _check(
        "brep_valid",
        "pass" if valid else "fail",
        "BRepCheck_Analyzer and Shape.is_valid both report a valid B-rep."
        if valid
        else "BRepCheck_Analyzer or Shape.is_valid reports an invalid B-rep.",
        expected="BRepCheck_Analyzer.IsValid()=true; Shape.is_valid=true",
        actual=(
            f"BRepCheck_Analyzer.IsValid()={str(analyzer_valid).lower()}; "
            f"Shape.is_valid={str(shape_valid).lower()}"
        ),
    )


def _solid_count_checks(shape: Any, kind: str) -> tuple[Check, Check | None]:
    if not isinstance(kind, str) or kind not in {"part", "assembly"}:
        return (
            _skip(
                "solid_count",
                f"no 3D body-count policy is defined for kind={kind!r}",
                expected="kind is 'part' or 'assembly'",
            ),
            None,
        )
    expected = "exactly 1 solid for a part" if kind == "part" else "at least 1 solid for an assembly"
    try:
        solids = list(shape.solids())
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        reason = _exception_text(exc)
        return (
            _skip("solid_count", f"solid enumeration raised {reason}", expected=expected),
            None,
        )

    count = len(solids)
    passed = count == 1 if kind == "part" else count >= 1
    if passed:
        message = f"The {kind} contains {count} solid(s); the body-count policy is satisfied."
    elif kind == "part" and count > 1:
        message = (
            f"The part contains {count} solids, but a part project must contain exactly one solid. "
            "Use an assembly project for a multi-solid model."
        )
    elif kind == "part":
        message = "The part contains no solids; a part project must contain exactly one solid."
    else:
        message = f"The {kind} contains {count} solid(s); the body-count policy is not satisfied."
    count_check = _check(
        "solid_count",
        "pass" if passed else "fail",
        message,
        expected=expected,
        actual=f"{count} solid(s)",
    )
    if count <= 1:
        return count_check, None

    warning = _check(
        "multiple_solids",
        "warn",
        f"The {kind} contains {count} solids; their intended relationship has not been checked.",
        expected="review the body count and specify any required component relationships",
        actual=f"{count} solids",
        level="warn",
        heuristic=True,
        assumptions="Multiple solids can be intentional in an assembly; count alone does not establish intent or clearance.",
    )
    return count_check, warning


def interference_checks(
    ctx: ModelContext,
    *,
    volume_threshold_mm3: float = INTERFERENCE_VOLUME_THRESHOLD_MM3,
    time_budget_s: float = INTERFERENCE_TIME_BUDGET_S,
) -> list[Check]:
    """Check each potentially intersecting solid pair with an OCCT common.

    Bounding boxes only discard pairs that are provably disjoint. Pairwise
    common-volume checks use a finite wall-clock budget; pairs not reached when
    it expires remain explicitly unchecked. A pair already being checked finishes
    and counts, because it was fully checked. The result contains at most
    ``INTERFERENCE_MAX_REPORTED_PAIRS`` overlap and unknown-pair details.
    """
    if isinstance(volume_threshold_mm3, bool) or not isinstance(volume_threshold_mm3, (int, float)):
        return [_interference_skip(
            "volume_threshold_mm3 must be a finite non-negative number",
            expected="finite non-negative mm³ threshold",
        )]
    try:
        threshold = float(volume_threshold_mm3)
    except (OverflowError, ValueError):
        return [_interference_skip(
            "volume_threshold_mm3 must be a finite non-negative number",
            expected="finite non-negative mm³ threshold",
        )]
    if not isfinite(threshold) or threshold < 0:
        return [_interference_skip(
            "volume_threshold_mm3 must be finite and non-negative",
            expected="finite non-negative mm³ threshold",
        )]

    if isinstance(time_budget_s, bool) or not isinstance(time_budget_s, (int, float)):
        return [_interference_skip(
            "time_budget_s must be a finite non-negative number",
            expected=f"0 to {INTERFERENCE_MAX_TIME_BUDGET_S:g} seconds",
        )]
    try:
        budget = float(time_budget_s)
    except (OverflowError, ValueError):
        return [_interference_skip(
            "time_budget_s must be a finite non-negative number",
            expected=f"0 to {INTERFERENCE_MAX_TIME_BUDGET_S:g} seconds",
        )]
    if not isfinite(budget) or budget < 0 or budget > INTERFERENCE_MAX_TIME_BUDGET_S:
        return [_interference_skip(
            f"time_budget_s must be finite and between 0 and {INTERFERENCE_MAX_TIME_BUDGET_S:g}",
            expected=f"0 to {INTERFERENCE_MAX_TIME_BUDGET_S:g} seconds",
        )]

    started = monotonic()
    unit_scale = _LENGTH_TO_MM.get(ctx.units) if isinstance(ctx.units, str) else None
    if unit_scale is None:
        return [_interference_skip(
            f"ctx.units={ctx.units!r} is unsupported; interference volumes are reported in mm³",
            expected=f"a common volume at or below {threshold:g} mm³",
        )]
    if ctx.shape is None:
        return [_interference_skip("ctx.shape is None", expected=f"a common volume at or below {threshold:g} mm³")]

    try:
        solids = list(ctx.shape.solids())
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        return [_interference_skip(
            f"solid enumeration raised {_exception_text(exc)}",
            expected=f"a common volume at or below {threshold:g} mm³",
        )]
    if not solids:
        return [_interference_skip(
            "the shape contains no solids to compare",
            expected=f"a common volume at or below {threshold:g} mm³",
            actual=_interference_actual(
                threshold=threshold,
                budget=budget,
                solid_count=0,
                total_pairs=0,
                screened_pairs=0,
                disjoint_pairs=0,
                candidate_pairs=0,
                boolean_attempts=0,
                boolean_completions=0,
                overlap_count=0,
                unknown_count=0,
                unchecked_pairs=0,
                bbox_failures=0,
                elapsed_s=monotonic() - started,
                budget_exhausted=False,
                overlap_count_complete=True,
                overlaps=[],
                unknown_pairs=[],
            ),
        )]

    count = len(solids)
    total_pairs = count * (count - 1) // 2
    labels = _solid_labels(ctx.shape, solids)
    boxes: list[tuple[tuple[float, float, float], tuple[float, float, float]] | None] = []
    bbox_failures = 0
    for solid in solids:
        try:
            boxes.append(_solid_box(solid))
        except Exception as exc:
            if is_memory_exhaustion(exc):
                raise
            boxes.append(None)
            bbox_failures += 1

    screened_pairs = 0
    disjoint_pairs = 0
    candidate_pairs = 0
    boolean_attempts = 0
    boolean_completions = 0
    overlap_count = 0
    unknown_count = 0
    overlaps: list[tuple[float, int, int, dict[str, Any]]] = []
    unknown_pairs: list[dict[str, Any]] = []
    budget_exhausted = False
    for first_index, first in enumerate(solids):
        for second_index in range(first_index + 1, count):
            if monotonic() - started >= budget:
                budget_exhausted = True
                break
            screened_pairs += 1
            if _boxes_disjoint(boxes[first_index], boxes[second_index]):
                disjoint_pairs += 1
                continue
            candidate_pairs += 1
            boolean_attempts += 1
            try:
                overlap_mm3, location_mm = _solid_intersection_mm(
                    first, solids[second_index], unit_scale, threshold
                )
            except Exception as exc:
                if is_memory_exhaustion(exc):
                    raise
                unknown_count += 1
                if len(unknown_pairs) < INTERFERENCE_MAX_REPORTED_PAIRS:
                    unknown_pairs.append({
                        "parts": [_solid_reference(first_index, labels), _solid_reference(second_index, labels)],
                        "status": "unknown",
                        "reason": _exception_text(exc)[:240],
                    })
                continue
            boolean_completions += 1
            if overlap_mm3 <= threshold:
                continue
            overlap_count += 1
            pair = {
                "parts": [_solid_reference(first_index, labels), _solid_reference(second_index, labels)],
                "overlap_volume_mm3": overlap_mm3,
                "location_mm": list(location_mm) if location_mm is not None else None,
            }
            entry = (overlap_mm3, -first_index, -second_index, pair)
            if len(overlaps) < INTERFERENCE_MAX_REPORTED_PAIRS:
                heappush(overlaps, entry)
            elif entry[:3] > overlaps[0][:3]:
                heapreplace(overlaps, entry)
        if budget_exhausted:
            break

    unchecked_pairs = total_pairs - screened_pairs
    overlap_count_complete = not budget_exhausted and unknown_count == 0
    if overlap_count:
        status = "fail"
        count_text = str(overlap_count) if overlap_count_complete else f"at least {overlap_count}"
        message = (
            f"Found {count_text} solid pair(s) with common volume above {threshold:g} mm³."
        )
        if unknown_count or unchecked_pairs:
            message += (
                f" {unknown_count} pair(s) have unknown boolean results; "
                f"{unchecked_pairs} pair(s) were unchecked."
            )
    elif overlap_count_complete:
        status = "pass"
        message = f"No solid pair overlaps above {threshold:g} mm³; all {total_pairs} pair(s) were resolved."
    else:
        status = "skip"
        message = (
            f"Interference checking is incomplete: no overlap has been confirmed, but {unknown_count} pair(s) "
            f"have unknown boolean results and {unchecked_pairs} pair(s) were unchecked. No pass is reported."
        )

    ordered_overlaps = [entry[3] for entry in sorted(overlaps, key=lambda item: (-item[0], -item[1], -item[2]))]
    actual = _interference_actual(
        threshold=threshold,
        budget=budget,
        solid_count=count,
        total_pairs=total_pairs,
        screened_pairs=screened_pairs,
        disjoint_pairs=disjoint_pairs,
        candidate_pairs=candidate_pairs,
        boolean_attempts=boolean_attempts,
        boolean_completions=boolean_completions,
        overlap_count=overlap_count,
        unknown_count=unknown_count,
        unchecked_pairs=unchecked_pairs,
        bbox_failures=bbox_failures,
        elapsed_s=monotonic() - started,
        budget_exhausted=budget_exhausted,
        overlap_count_complete=overlap_count_complete,
        overlaps=ordered_overlaps,
        unknown_pairs=unknown_pairs,
    )
    return [Check(
        id="interference_pairs",
        suite="interference",
        level="error",
        status=status,  # type: ignore[arg-type]
        message=message,
        expected=f"No solid pair has common volume above {threshold:g} mm³",
        actual=actual,
        assumptions=(
            "Pairs whose axis-aligned bounding boxes are disjoint are skipped; "
            "every remaining pair uses an OCCT common. "
            f"Volumes at or below {threshold:g} mm³ are treated as zero-volume contact."
        ),
    )]


def validator_geometry_checks(
    ctx: ModelContext, *, time_budget_s: float = BUILD_INTERFERENCE_TIME_BUDGET_S,
) -> list[Check]:
    """Run geometry-only assembly checks from a validator-owned BREP context.

    The ordinary build worker's check list is untrusted. This hook recomputes
    interference from the shape loaded by the validator and reports automatic
    findings as advisory warnings. An explicit spec requirement remains an
    error-level requirement through the quality evaluator.
    """
    if ctx.kind != "assembly" or ctx.shape is None:
        return []
    try:
        if len(list(ctx.shape.solids())) <= 1:
            return []
    except Exception:
        # The hard geometry gate already reports an unavailable body count.
        return []
    checks = interference_checks(ctx, time_budget_s=time_budget_s)
    for check in checks:
        check.level = "warn"
        if check.status == "fail":
            check.status = "warn"
            check.message = "Independent build interference warning: " + check.message
        elif check.status == "skip":
            check.message = "Independent build interference result is unknown: " + check.message
    return checks


def _interference_skip(reason: str, *, expected: str, actual: str | None = None) -> Check:
    return Check(
        id="interference_pairs",
        suite="interference",
        level="error",
        status="skip",
        message=f"The interference check could not prove pair clearance: {reason}.",
        expected=expected,
        actual=actual or reason,
        assumptions="An unavailable or failed pair check is unknown, not a clearance pass.",
    )


def _interference_actual(
    *,
    threshold: float,
    budget: float,
    solid_count: int,
    total_pairs: int,
    screened_pairs: int,
    disjoint_pairs: int,
    candidate_pairs: int,
    boolean_attempts: int,
    boolean_completions: int,
    overlap_count: int,
    unknown_count: int,
    unchecked_pairs: int,
    bbox_failures: int,
    elapsed_s: float,
    budget_exhausted: bool,
    overlap_count_complete: bool,
    overlaps: list[dict[str, Any]],
    unknown_pairs: list[dict[str, Any]],
) -> str:
    details = {
        "volume_threshold_mm3": threshold,
        "time_budget_s": budget,
        "elapsed_s": round(elapsed_s, 3),
        "solid_count": solid_count,
        "total_solid_pairs": total_pairs,
        "screened_pairs": screened_pairs,
        "bbox_disjoint_pairs": disjoint_pairs,
        "candidate_pairs": candidate_pairs,
        "boolean_attempts": boolean_attempts,
        "boolean_completions": boolean_completions,
        "confirmed_overlapping_pairs": overlap_count,
        "total_overlapping_pairs": overlap_count if overlap_count_complete else None,
        "overlap_count_complete": overlap_count_complete,
        "unknown_boolean_pairs": unknown_count,
        "unchecked_pairs": unchecked_pairs,
        "bbox_failures": bbox_failures,
        "budget_exhausted": budget_exhausted,
        "reported_overlaps": overlaps,
        "reported_unknown_pairs": unknown_pairs,
        "reported_overlaps_truncated": overlap_count > len(overlaps),
        "reported_unknown_pairs_truncated": unknown_count > len(unknown_pairs),
    }
    return json.dumps(details, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _shape_children(shape: Any) -> list[Any]:
    children = getattr(shape, "children", None)
    if callable(children):
        children = children()
    try:
        return list(children) if children is not None else []
    except TypeError:
        return []


def _shape_label(shape: Any) -> str | None:
    for attribute in ("label", "name", "part_name"):
        try:
            value = getattr(shape, attribute, None)
        except Exception as exc:
            if is_memory_exhaustion(exc):
                raise
            continue
        if isinstance(value, str) and value.strip():
            label = value.strip()
            return label if len(label) <= 120 else label[:117] + "..."
    return None


def _solid_labels(shape: Any, solids: list[Any]) -> list[str | None]:
    direct = [_shape_label(solid) for solid in solids]
    discovered: list[str | None] = []

    def visit(item: Any) -> None:
        children = _shape_children(item)
        if children:
            for child in children:
                visit(child)
            return
        try:
            nested_solids = list(item.solids())
        except Exception as exc:
            if is_memory_exhaustion(exc):
                raise
            return
        if len(nested_solids) == 1:
            discovered.append(_shape_label(item) or _shape_label(nested_solids[0]))
        elif nested_solids:
            discovered.extend(_shape_label(solid) for solid in nested_solids)

    visit(shape)
    if len(discovered) == len(solids):
        return [label or discovered[index] for index, label in enumerate(direct)]
    return direct


def _solid_reference(index: int, labels: list[str | None]) -> dict[str, Any]:
    reference: dict[str, Any] = {"index": index}
    if index < len(labels) and labels[index]:
        reference["label"] = labels[index]
    return reference


def _coordinates(value: Any) -> tuple[float, float, float]:
    result = []
    for upper, lower in (("X", "x"), ("Y", "y"), ("Z", "z")):
        component = getattr(value, upper, None)
        if component is None:
            component = getattr(value, lower)
        if callable(component):
            component = component()
        number = float(component)
        if not isfinite(number):
            raise ValueError("non-finite coordinate")
        result.append(number)
    return tuple(result)  # type: ignore[return-value]


def _solid_box(solid: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    bounds = solid.bounding_box()
    return _coordinates(bounds.min), _coordinates(bounds.max)


def _boxes_disjoint(
    first: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
    second: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
) -> bool:
    if first is None or second is None:
        return False
    first_min, first_max = first
    second_min, second_max = second
    return any(
        first_max[axis] <= second_min[axis] or second_max[axis] <= first_min[axis]
        for axis in range(3)
    )


def _solid_intersection_mm(
    first: Any,
    second: Any,
    unit_scale: float,
    volume_threshold_mm3: float,
) -> tuple[float, tuple[float, float, float] | None]:
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.collections import List_TopoDS_Shape
    from build123d import Shape

    operation = BRepAlgoAPI_Common()
    arguments = List_TopoDS_Shape()
    arguments.Append(first.wrapped)
    tools = List_TopoDS_Shape()
    tools.Append(second.wrapped)
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.SetRunParallel(True)
    operation.Build()
    if not operation.IsDone():
        raise RuntimeError("OCCT common did not complete")
    raw_common = operation.Shape()
    if raw_common.IsNull():
        raise RuntimeError("OCCT common returned a null shape")
    common = Shape.cast(raw_common)
    if common is None or common.is_null:
        raise RuntimeError("OCCT common returned no shape")
    try:
        valid = common.is_valid
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        raise RuntimeError(f"OCCT common validity check raised {_exception_text(exc)}") from exc
    if not valid:
        raise RuntimeError("OCCT common returned an invalid shape")
    volume_native = float(common.volume)
    if not isfinite(volume_native) or volume_native < 0:
        raise ValueError("OCCT common returned a non-finite or negative volume")
    volume_mm3 = volume_native * unit_scale**3
    if not isfinite(volume_mm3):
        raise ValueError("OCCT common returned a non-finite volume in mm³")
    if volume_mm3 <= volume_threshold_mm3:
        return volume_mm3, None
    try:
        center = _coordinates(common.center())
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        try:
            minimum, maximum = _solid_box(common)
            center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
        except Exception as exc:
            if is_memory_exhaustion(exc):
                raise
            return volume_mm3, None
    location = tuple(value * unit_scale + 0.0 for value in center)
    return volume_mm3, location  # type: ignore[return-value]


def _closed_shell_check(shape: Any) -> Check:
    try:
        from OCP.BRep import BRep_Tool

        solids = list(shape.solids())
        shells = list(shape.shells())
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        reason = _exception_text(exc)
        return _skip(
            "closed_shells",
            f"solid or shell enumeration raised {reason}",
            expected="at least one shell and every shell closed",
        )

    if not shells:
        if solids:
            return _check(
                "closed_shells",
                "fail",
                "No shell topology was found for a shape containing solids.",
                expected="at least one shell and every shell closed",
                actual=f"solids={len(solids)}; shells=0",
            )
        return _skip(
            "closed_shells",
            "the shape contains no shells to inspect",
            expected="at least one shell and every shell closed",
        )

    closed: list[bool] = []
    try:
        for shell in shells:
            closed.append(bool(BRep_Tool.IsClosed_s(shell.wrapped)))
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        reason = _exception_text(exc)
        return _skip(
            "closed_shells",
            f"BRep_Tool.IsClosed_s could not inspect every shell: {reason}",
            expected="every shell closed",
        )

    open_indices = [index + 1 for index, is_closed in enumerate(closed) if not is_closed]
    all_closed = not open_indices
    return _check(
        "closed_shells",
        "pass" if all_closed else "fail",
        "Every shell is closed."
        if all_closed
        else f"Found {len(open_indices)} open shell(s).",
        expected="every shell closed",
        actual=(
            f"shells={len(shells)}; closed={len(shells) - len(open_indices)}; "
            f"open={len(open_indices)}; open_indices={open_indices}"
        ),
    )


_POLE_EDGE_LENGTH_MM = 1e-7


def _edge_quality_check(shape: Any, tolerance: GateTolerances, unit_scale: float) -> Check:
    threshold = tolerance.min_edge_length_mm
    try:
        from OCP.BRep import BRep_Tool
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.TopoDS import TopoDS

        # build123d Shape.edges() filters out edges marked degenerate by OCCT.
        # entities() exposes the unique underlying edges without that filter.
        edges = list(shape.entities("Edge"))
        if not edges:
            return _skip(
                "edge_quality",
                "the shape contains no edges to measure",
                expected=f"no degenerate edges; every edge length > {_fmt(threshold)} mm",
            )

        lengths: list[float] = []
        pole_count = 0
        degenerate_count = 0
        for raw_edge in edges:
            edge = TopoDS.Edge(raw_edge)
            properties = GProp_GProps()
            BRepGProp.LinearProperties_s(edge, properties)
            length = float(properties.Mass()) * unit_scale
            if not isfinite(length) or length < 0:
                raise ValueError("an edge length was negative or not finite")
            if BRep_Tool.Degenerated_s(edge):
                # OCCT represents the pole of a sphere or the apex of a cone as a
                # zero-length degenerated edge. That is correct topology, not a sliver.
                if length <= _POLE_EDGE_LENGTH_MM:
                    pole_count += 1
                    continue
                degenerate_count += 1
            lengths.append(length)
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        reason = _exception_text(exc)
        return _skip(
            "edge_quality",
            f"edge enumeration or measurement raised {reason}",
            expected=f"no degenerate edges; every edge length > {_fmt(threshold)} mm",
        )

    small_count = sum(length <= threshold for length in lengths)
    passed = degenerate_count == 0 and small_count == 0
    minimum_length = _fmt(min(lengths)) if lengths else "none"
    return _check(
        "edge_quality",
        "pass" if passed else "fail",
        f"All {len(lengths)} measured edges are above the configured threshold; {pole_count} surface pole edge(s) are expected."
        if passed
        else f"Found {degenerate_count} degenerate edge(s) and {small_count} edge(s) at or below the configured threshold.",
        expected=f"no degenerate edges other than surface poles; each edge length > {_fmt(threshold)} mm",
        actual=(
            f"edges={len(lengths)}; surface_pole_edges={pole_count}; degenerate_edges={degenerate_count}; "
            f"edges_at_or_below_threshold={small_count}; "
            f"minimum_edge_length={minimum_length} mm; "
            f"configured_threshold={_fmt(threshold)} mm"
        ),
        heuristic=True,
        assumptions="The configurable small-edge threshold is a numerical topology heuristic, not a manufacturing limit.",
    )


def _face_quality_check(shape: Any, tolerance: GateTolerances, unit_scale: float) -> Check:
    area_threshold = tolerance.min_face_area_mm2
    width_threshold = tolerance.min_face_width_mm
    expected = (
        f"every face area > {_fmt(area_threshold)} mm² and "
        f"2 × area / summed edge lengths > {_fmt(width_threshold)} mm"
    )
    try:
        faces = list(shape.faces())
        if not faces:
            return _skip(
                "face_quality",
                "the shape contains no faces to measure",
                expected=expected,
            )

        areas: list[float] = []
        widths: list[float] = []
        for face in faces:
            area = float(face.area) * unit_scale**2
            if not isfinite(area) or area < 0:
                raise ValueError("a face area was negative or not finite")
            areas.append(area)
            if area == 0:
                widths.append(0.0)
                continue
            lengths = [float(edge.length) * unit_scale for edge in face.edges()]
            if not lengths or any(not isfinite(length) or length < 0 for length in lengths):
                raise ValueError("a face has no measurable finite edge lengths")
            perimeter = sum(lengths)
            if not isfinite(perimeter) or perimeter <= 0:
                raise ValueError("a face's summed edge lengths are not positive and finite")
            width = 2 * area / perimeter
            if not isfinite(width):
                raise ValueError("a face width proxy was not finite")
            widths.append(width)
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        reason = _exception_text(exc)
        return _skip(
            "face_quality",
            f"face enumeration or measurement raised {reason}",
            expected=expected,
        )

    degenerate_count = sum(area == 0 for area in areas)
    small_area_count = sum(0 < area <= area_threshold for area in areas)
    sliver_count = sum(area > 0 and width <= width_threshold for area, width in zip(areas, widths))
    passed = degenerate_count == 0 and small_area_count == 0 and sliver_count == 0
    return _check(
        "face_quality",
        "pass" if passed else "fail",
        f"All {len(areas)} faces exceed the configured area and sliver-width thresholds."
        if passed
        else (
            f"Found {degenerate_count} zero-area face(s), {small_area_count} small-area face(s), "
            f"and {sliver_count} face(s) at or below the sliver-width threshold; counts can overlap."
        ),
        expected=expected,
        actual=(
            f"faces={len(areas)}; zero_area_faces={degenerate_count}; "
            f"small_area_faces={small_area_count}; sliver_faces={sliver_count}; "
            f"minimum_face_area={_fmt(min(areas))} mm²; "
            f"minimum_face_width_proxy={_fmt(min(widths))} mm; "
            f"configured_area_threshold={_fmt(area_threshold)} mm²; "
            f"configured_width_threshold={_fmt(width_threshold)} mm"
        ),
        heuristic=True,
        assumptions=(
            "2 × area / the sum of distinct topological edge lengths is a sliver proxy; "
            "it depends on face trimming and is not a manufacturing limit."
        ),
    )


def _step_volume_check(ctx: ModelContext, tolerance: GateTolerances, unit_scale: float) -> Check:
    threshold_abs = tolerance.step_volume_abs_mm3
    threshold_rel = tolerance.step_volume_rel
    expected = (
        f"positive STEP volume; absolute drift <= max({_fmt(threshold_abs)} mm³, "
        f"{_fmt(threshold_rel)} × source volume)"
    )
    try:
        from build123d import Unit, export_step

        source_volume = fsum(float(solid.volume) for solid in ctx.shape.solids()) * unit_scale**3
        if not isfinite(source_volume):
            raise ValueError("the source volume was not finite")
        if source_volume <= 0:
            return _skip(
                "step_volume_drift",
                f"source volume is {_fmt(source_volume)} mm³; a positive volume is required for comparison",
                expected=expected,
            )

        job_dir = Path(ctx.job_dir)
        job_dir.mkdir(exist_ok=True)
        unit = Unit.IN if ctx.units == "in" else Unit.MM
        with tempfile.TemporaryDirectory(prefix="aad-gate-", dir=job_dir) as temporary:
            step_path = Path(temporary) / "roundtrip.step"
            exported = export_step(ctx.shape, step_path, unit=unit)
            if not exported:
                return _skip("step_volume_drift", "export_step returned false", expected=expected)
            if not step_path.is_file() or step_path.stat().st_size == 0:
                return _skip(
                    "step_volume_drift",
                    "export_step did not create a non-empty STEP file",
                    expected=expected,
                )
            roundtrip_shape = _import_step_shape(step_path)
            # build123d's STEP importer returns geometry in mm even when the
            # exported document declared inch coordinates.
            roundtrip_volume = fsum(float(solid.volume) for solid in roundtrip_shape.solids())
            if not isfinite(roundtrip_volume):
                raise ValueError("the re-imported STEP volume was not finite")

        drift = abs(roundtrip_volume - source_volume)
        threshold = max(threshold_abs, threshold_rel * source_volume)
        relative_value = drift / source_volume
        if not all(isfinite(value) for value in (drift, threshold, relative_value)):
            raise ValueError("the STEP drift or comparison threshold was not finite")
        relative_drift = _fmt(relative_value)
        passed = roundtrip_volume > 0 and drift <= threshold
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        reason = _exception_text(exc)
        return _skip(
            "step_volume_drift",
            f"STEP export, re-import, or volume measurement raised {reason}",
            expected=expected,
        )

    expected = (
        f"positive STEP volume; absolute drift <= max({_fmt(threshold_abs)} mm³, "
        f"{_fmt(threshold_rel)} × {_fmt(source_volume)} mm³) = {_fmt(threshold)} mm³"
    )
    return _check(
        "step_volume_drift",
        "pass" if passed else "fail",
        "STEP re-import volume drift is within the configured threshold."
        if passed
        else "STEP re-import volume is not positive or its drift exceeds the configured threshold.",
        expected=expected,
        actual=(
            f"source_volume={_fmt(source_volume)} mm³; "
            f"step_volume={_fmt(roundtrip_volume)} mm³; "
            f"absolute_drift={_fmt(drift)} mm³; relative_drift={relative_drift}; "
            f"configured_absolute_threshold={_fmt(threshold_abs)} mm³; "
            f"configured_relative_threshold={_fmt(threshold_rel)}"
        ),
    )


def _import_step_shape(path: Path) -> Any:
    """Isolated to make the actual STEP import boundary fault-injectable in tests."""
    from build123d import import_step

    return import_step(path)


def _assembly_bodies(shape: Any) -> list[Any] | None:
    """The solids to mesh one at a time, or None when loose topology needs the whole-shape check."""
    solids = list(shape.solids())
    if len(solids) < 2 or sum(len(solid.faces()) for solid in solids) != len(shape.faces()):
        return None
    return solids


def _mesh_watertight_check(shape: Any, tolerance: GateTolerances, unit_scale: float, kind: str = "part") -> Check:
    """Each assembly body must close by itself. Bodies may touch, so welding across them proves nothing."""
    try:
        bodies = _assembly_bodies(shape) if kind == "assembly" else None
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        return _skip("mesh_watertight", f"solid enumeration raised {_exception_text(exc)}")
    if bodies is None:
        return _shape_watertight_check(shape, tolerance, unit_scale)
    try:
        meshes = _fresh_mesh_arrays_by_solid(
            shape,
            tolerance.mesh_deflection_mm / unit_scale,
            tolerance.mesh_angular_deflection_rad,
        )
        if len(meshes) != len(bodies):
            raise ValueError("assembly copy produced a different number of solids")
        results = [
            _check_mesh_arrays(vertices, triangles, tolerance, unit_scale)
            for vertices, triangles in meshes
        ]
        # A failing or unavailable batched result is checked again through the
        # original per-body path. That keeps unusual OCCT assembly topologies on
        # the established behavior, while the common valid case meshes once.
        if any(result.status != "pass" for result in results):
            results = [_shape_watertight_check(body, tolerance, unit_scale) for body in bodies]
    except Exception:
        results = [_shape_watertight_check(body, tolerance, unit_scale) for body in bodies]
    worst = next(
        (result for status in ("fail", "skip") for result in results if result.status == status),
        results[0],
    )
    failed = [index for index, result in enumerate(results) if result.status != "pass"]
    worst.actual = f"bodies_checked={len(bodies)}; bodies_not_passed={len(failed)}; first_not_passed={failed[:8]}; {worst.actual}"
    if worst.status == "pass":
        worst.message = f"All {len(bodies)} assembly bodies mesh to closed, consistently wound surfaces."
    return worst


def _shape_watertight_check(shape: Any, tolerance: GateTolerances, unit_scale: float) -> Check:
    try:
        vertices, triangles = _fresh_mesh_arrays(
            shape,
            tolerance.mesh_deflection_mm / unit_scale,
            tolerance.mesh_angular_deflection_rad,
        )
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        reason = _exception_text(exc)
        return _skip(
            "mesh_watertight",
            f"shape tessellation raised {reason}; "
            f"configured_deflection={_fmt(tolerance.mesh_deflection_mm)} mm; "
            f"configured_angular_deflection={_fmt(tolerance.mesh_angular_deflection_rad)} rad",
            expected=(
                "no degenerate or duplicate triangles; paired, oppositely oriented edges; "
                "cyclic vertex links; "
                f"weld tolerance={_fmt(tolerance.mesh_vertex_weld_mm)} mm"
            ),
        )
    check = _check_mesh_arrays(vertices, triangles, tolerance, unit_scale)
    check.actual = (
        f"{check.actual}; configured_deflection={_fmt(tolerance.mesh_deflection_mm)} mm; "
        f"configured_angular_deflection={_fmt(tolerance.mesh_angular_deflection_rad)} rad"
    )
    return check


def _fresh_mesh_arrays(shape: Any, linear_deflection: float, angular_deflection: float) -> tuple[list, list]:
    """Mesh private native topology with absolute deflection and no cached mesh."""
    copied = _fresh_meshed_copy(shape, linear_deflection, angular_deflection)
    return _triangulation_arrays(copied)


def _fresh_mesh_arrays_by_solid(
    shape: Any, linear_deflection: float, angular_deflection: float
) -> list[tuple[list, list]]:
    """Mesh an assembly copy once and return isolated triangle arrays per solid."""
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
    from OCP.TopoDS import TopoDS_Iterator

    copied = _fresh_meshed_copy(shape, linear_deflection, angular_deflection)
    arrays: list[tuple[list, list]] = []

    def visit(item: Any, solid_index: int | None = None) -> None:
        shape_type = item.ShapeType()
        if shape_type == TopAbs_SOLID:
            arrays.append(([], []))
            solid_index = len(arrays) - 1
        elif shape_type == TopAbs_FACE:
            if solid_index is None:
                raise ValueError("an assembly face is not contained in a solid")
            _append_face_triangles(item, *arrays[solid_index])
            return

        children = TopoDS_Iterator(item, True, True)
        while children.More():
            visit(children.Value(), solid_index)
            children.Next()

    visit(copied)
    return arrays


def _fresh_meshed_copy(shape: Any, linear_deflection: float, angular_deflection: float) -> Any:
    """Copy and remesh native topology once without using or changing source meshes."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools

    wrapped = getattr(shape, "wrapped", shape)
    copier = BRepBuilderAPI_Copy(wrapped, False, False)
    if not copier.IsDone():
        raise ValueError("native topology could not be copied for meshing")
    copied = copier.Shape()
    BRepTools.Clean_s(copied)
    mesher = BRepMesh_IncrementalMesh(copied, linear_deflection, False, angular_deflection, False)
    if not mesher.IsDone():
        raise ValueError("absolute native meshing did not complete")
    return copied


def _triangulation_arrays(shape: Any) -> tuple[list, list]:
    """Extract oriented triangle data from a fresh, already-meshed topology."""
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        _append_face_triangles(explorer.Current(), vertices, triangles)
        explorer.Next()
    return vertices, triangles


def _append_face_triangles(face_shape: Any, vertices: list, triangles: list) -> None:
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    face = TopoDS.Face(face_shape)
    location = TopLoc_Location()
    mesh = BRep_Tool.Triangulation_s(face, location)
    if mesh is None:
        raise ValueError("a face has no triangulation after native meshing")
    offset = len(vertices)
    transform = location.Transformation()
    for index in range(1, mesh.NbNodes() + 1):
        vertices.append(tuple(mesh.Node(index).Transformed(transform).Coord()))
    for triangle in mesh.Triangles():
        indices = triangle.Get()
        if face.Orientation() == TopAbs_REVERSED:
            indices = (indices[0], indices[2], indices[1])
        triangles.append(tuple(index + offset - 1 for index in indices))


def _check_mesh_arrays(
    vertices: Any,
    triangles: Any,
    tolerance: GateTolerances,
    unit_scale: float = 1.0,
) -> Check:
    """Check triangle topology, welding each vertex to a nearby representative."""
    expected = (
        "no degenerate or duplicate triangles; every edge has degree 2 with opposite winding; "
        "each used vertex has one connected cyclic link; "
        f"weld tolerance={_fmt(tolerance.mesh_vertex_weld_mm)} mm; "
        f"triangle area threshold={_fmt(tolerance.mesh_triangle_area_mm2)} mm²"
    )
    try:
        source_vertices = list(vertices)
        source_triangles = list(triangles)
        if not source_vertices or not source_triangles:
            return _skip("mesh_watertight", "tessellation produced no vertices or triangles", expected)

        points = [_point_in_mm(point, unit_scale) for point in source_vertices]
        welded_points, vertex_map = _weld_vertices(points, tolerance.mesh_vertex_weld_mm)
        edge_degrees: Counter[tuple[int, int]] = Counter()
        edge_directions: Counter[tuple[int, int]] = Counter()
        triangle_counts: Counter[tuple[int, int, int]] = Counter()
        links: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
        degenerate_triangles = 0
        collapsed_triangles = 0
        minimum_triangle_area = float("inf")

        for triangle_number, triangle in enumerate(source_triangles, start=1):
            if len(triangle) != 3:
                raise ValueError(f"triangle {triangle_number} does not have three indices")
            if any(isinstance(index, bool) for index in triangle):
                raise ValueError(f"triangle {triangle_number} contains a boolean index")
            raw_indices = tuple(integer_index(index) for index in triangle)
            if any(index < 0 or index >= len(vertex_map) for index in raw_indices):
                raise ValueError(f"triangle {triangle_number} references an out-of-range vertex")
            indices = tuple(vertex_map[index] for index in raw_indices)
            if len(set(indices)) != 3:
                # Corners welded together, as at a surface pole. The triangle encloses
                # nothing, so dropping it cannot hide a hole: the edge checks still run.
                collapsed_triangles += 1
                continue

            area = _triangle_area(*(welded_points[index] for index in indices))
            if not isfinite(area):
                raise ValueError(f"triangle {triangle_number} has a non-finite area")
            minimum_triangle_area = min(minimum_triangle_area, area)
            if area <= tolerance.mesh_triangle_area_mm2:
                degenerate_triangles += 1
                continue

            triangle_counts[tuple(sorted(indices))] += 1
            for start, end in ((indices[0], indices[1]), (indices[1], indices[2]), (indices[2], indices[0])):
                edge = tuple(sorted((start, end)))
                edge_degrees[edge] += 1
                edge_directions[edge] += 1 if start < end else -1
            for vertex, first, second in (
                (indices[0], indices[1], indices[2]),
                (indices[1], indices[2], indices[0]),
                (indices[2], indices[0], indices[1]),
            ):
                links[vertex][first].add(second)
                links[vertex][second].add(first)

        duplicate_triangles = sum(count - 1 for count in triangle_counts.values())
        winding_conflicts = sum(
            degree == 2 and edge_directions[edge] != 0 for edge, degree in edge_degrees.items()
        )
        nonmanifold_vertices = sum(not _is_single_cycle(link) for link in links.values())

        if not edge_degrees:
            boundaries = 0
            nonmanifold = 0
            other_bad = 0
            bad_edges = 0
            min_degree = 0
            max_degree = 0
        else:
            degrees = list(edge_degrees.values())
            boundaries = sum(degree == 1 for degree in degrees)
            nonmanifold = sum(degree > 2 for degree in degrees)
            other_bad = sum(degree < 1 for degree in degrees)
            bad_edges = sum(degree != 2 for degree in degrees)
            min_degree = min(degrees)
            max_degree = max(degrees)
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        reason = _exception_text(exc)
        return _skip("mesh_watertight", f"mesh topology measurement raised {reason}", expected)

    passed = (
        bool(edge_degrees)
        and degenerate_triangles == 0
        and duplicate_triangles == 0
        and bad_edges == 0
        and winding_conflicts == 0
        and nonmanifold_vertices == 0
    )
    return _check(
        "mesh_watertight",
        "pass" if passed else "fail",
        "The welded mesh has no degenerate or duplicate triangles, has paired oriented edges, and has cyclic vertex links."
        if passed
        else "The welded mesh failed at least one triangle, edge, winding, or vertex-link check.",
        expected=expected,
        actual=(
            f"input_vertices={len(points)}; welded_vertices={len(welded_points)}; "
            f"triangles={len(source_triangles)}; degenerate_triangles={degenerate_triangles}; "
            f"collapsed_triangles={collapsed_triangles}; "
            f"duplicate_triangles={duplicate_triangles}; winding_conflicts={winding_conflicts}; "
            f"nonmanifold_vertices={nonmanifold_vertices}; "
            f"undirected_edges={len(edge_degrees)}; boundary_edges={boundaries}; "
            f"nonmanifold_edges={nonmanifold}; other_non_2_degree_edges={other_bad}; "
            f"edge_degree_range={min_degree}..{max_degree}; "
            f"weld_tolerance={_fmt(tolerance.mesh_vertex_weld_mm)} mm; "
            f"minimum_triangle_area={_fmt(minimum_triangle_area)} mm²; "
            f"triangle_area_threshold={_fmt(tolerance.mesh_triangle_area_mm2)} mm²"
        ),
    )


def _is_single_cycle(link: dict[int, set[int]]) -> bool:
    """A closed 2-manifold vertex has one connected cycle of neighbors."""
    if not link or any(len(neighbors) != 2 for neighbors in link.values()):
        return False
    pending = [next(iter(link))]
    visited: set[int] = set()
    while pending:
        vertex = pending.pop()
        if vertex not in visited:
            visited.add(vertex)
            pending.extend(link[vertex] - visited)
    return len(visited) == len(link)


def _point_in_mm(point: Any, unit_scale: float) -> tuple[float, float, float]:
    if all(hasattr(point, axis) for axis in ("X", "Y", "Z")):
        coordinates = (point.X, point.Y, point.Z)
    elif all(hasattr(point, axis) for axis in ("x", "y", "z")):
        coordinates = (point.x, point.y, point.z)
    else:
        coordinates = tuple(point)
    if len(coordinates) != 3:
        raise ValueError("a tessellated vertex does not have three coordinates")
    result = tuple(float(value) * unit_scale for value in coordinates)
    if not all(isfinite(value) for value in result):
        raise ValueError("a tessellated vertex coordinate was not finite")
    return result  # type: ignore[return-value]


def _weld_vertices(
    points: list[tuple[float, float, float]], tolerance_mm: float
) -> tuple[list[tuple[float, float, float]], list[int]]:
    if tolerance_mm == 0:
        representatives: list[tuple[float, float, float]] = []
        exact: dict[tuple[float, float, float], int] = {}
        vertex_map: list[int] = []
        for point in points:
            index = exact.get(point)
            if index is None:
                index = len(representatives)
                exact[point] = index
                representatives.append(point)
            vertex_map.append(index)
        return representatives, vertex_map

    buckets: dict[tuple[int, int, int], list[int]] = {}
    representatives = []
    vertex_map = []
    for point in points:
        cell = tuple(floor(coordinate / tolerance_mm) for coordinate in point)
        candidates: set[int] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    candidates.update(buckets.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()))

        nearest: int | None = None
        nearest_distance = float("inf")
        for candidate in sorted(candidates):
            representative = representatives[candidate]
            distance = dist(point, representative)
            if distance <= tolerance_mm and distance < nearest_distance:
                nearest = candidate
                nearest_distance = distance

        if nearest is None:
            nearest = len(representatives)
            representatives.append(point)
            buckets.setdefault(cell, []).append(nearest)
        vertex_map.append(nearest)
    return representatives, vertex_map


def _triangle_area(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
    third: tuple[float, float, float],
) -> float:
    ab = tuple(second[index] - first[index] for index in range(3))
    ac = tuple(third[index] - first[index] for index in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return 0.5 * hypot(*cross)


def _exception_text(exc: Exception) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _fmt(value: float) -> str:
    # Preserve enough digits to reconstruct the actual comparison value.
    return repr(float(value)).removesuffix(".0")
