"""Specification parsing, geometry checks, and fail-closed readiness logic."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import importlib
import math
import re
from typing import Any

from ._checks import finite, make_check, mm_per_unit, record
from .schema import NumericRange, SpecIssue, load_spec, parse_numeric_range, parse_spec_for_checks
from . import spec_geometry as geometry


def _text_number(value: float) -> str:
    if value == 0:
        value = 0.0
    return f"{value:.8g}"


def _unit_name(unit: str, power: int) -> str:
    if not power:
        return ""
    suffix = "" if power == 1 else f"^{power}"
    return f" {unit}{suffix}"


def _display_value(value: float, *, power: int, unit: str) -> str:
    if power:
        value /= mm_per_unit(unit) ** power
    return f"{_text_number(value)}{_unit_name(unit, power)}"


def _range_text(value: NumericRange) -> str:
    unit = value.display_unit or "mm"
    if value.nominal is not None and value.tolerance is not None:
        return (
            f"{_display_value(value.nominal, power=value.power, unit=unit)} ± "
            f"{_display_value(value.tolerance, power=value.power, unit=unit).strip()}"
        )
    if value.minimum is not None and value.minimum == value.maximum:
        band = 'numerical comparison resolution only' if value.implicit_exact else 'strict explicit boundary'
        return f"exact {_display_value(value.minimum, power=value.power, unit=unit)} ({band})"
    pieces = []
    if value.minimum is not None:
        pieces.append(f"minimum {_display_value(value.minimum, power=value.power, unit=unit)}")
    if value.maximum is not None:
        pieces.append(f"maximum {_display_value(value.maximum, power=value.power, unit=unit)}")
    return " and ".join(pieces) if pieces else "any finite value"


def _actual_value(value: float, *, power: int, unit: str) -> str:
    return _display_value(value, power=power, unit=unit)


def _within(value: float, expected: NumericRange) -> bool:
    measured = finite(value)
    # OCCT bounds and decimal pitches carry sub-micron numerical noise. This
    # resolution band is only for an exact measurement, never a discrete count or
    # an explicitly toleranced/ranged acceptance boundary.
    resolution = (1e-6 if expected.power else 1e-9) if expected.implicit_exact else 0.0
    return (expected.minimum is None or measured >= expected.minimum - resolution) and (expected.maximum is None or measured <= expected.maximum + resolution)


def _comparison_status(matches: bool, level: str) -> str:
    return "pass" if matches else ("fail" if level == "error" else "warn")


def _check(
    check_id: str,
    *,
    level: str,
    status: str,
    message: str,
    expected: str,
    actual: str,
    fix_hint: str,
    selector: str | None = None,
    feature: str | None = None,
    assumptions: str | None = None,
) -> Any:
    fields: dict[str, Any] = {
        "id": check_id,
        "suite": "spec",
        "level": level,
        "status": status,
        "message": message,
        "expected": expected,
        "actual": actual,
        "fix_hint": fix_hint,
    }
    if selector is not None:
        fields["selector"] = selector
    if feature is not None:
        fields["feature"] = feature
    if assumptions is not None:
        fields["assumptions"] = assumptions
    return make_check(**fields)


def _diagnostic(check_id: str, message: str, *, expected: str, actual: str, fix_hint: str) -> Any:
    return _check(
        check_id,
        level="error",
        status="skip",
        message=message,
        expected=expected,
        actual=actual,
        fix_hint=fix_hint,
    )


def _level(requirement: Any) -> str:
    if isinstance(requirement, dict) and requirement.get("level") in {"error", "warn"}:
        return requirement.get("level", "error")
    return "error"


def _requirement_id(requirement: Any, index: int) -> str:
    if isinstance(requirement, dict):
        value = requirement.get("id")
        if isinstance(value, str) and value.strip():
            return value
    return f"requirement_{index + 1}"


def _range(value: Any, *, unit: str, path: tuple[str | int, ...], power: int = 1) -> NumericRange:
    return parse_numeric_range(value, default_unit=unit, power=power, path=path)


def _shape_or_skip(ctx: Any) -> Any:
    shape = getattr(ctx, "shape", None)
    if shape is None:
        raise geometry.GeometryUnavailable("model shape is missing")
    return shape


def _eval_bbox(req_id: str, level: str, req: dict[str, Any], unit: str, factor: float | None, ctx: Any) -> Any:
    expected_ranges = {
        axis: _range(value, unit=unit, path=("requirements", req_id, "bbox", axis))
        for axis, value in req["bbox"].items()
    }
    expected_text = ", ".join(f"{axis.upper()} {_range_text(value)}" for axis, value in expected_ranges.items())
    if factor is None:
        raise geometry.UnsupportedGeometry("context units are not known")
    actual = geometry.bbox_mm(_shape_or_skip(ctx), factor)
    matches = all(_within(actual[axis], axis_range) for axis, axis_range in expected_ranges.items())
    actual_text = ", ".join(
        f"{axis.upper()} {_actual_value(actual[axis], power=1, unit=unit)}" for axis in expected_ranges
    )
    return _check(
        req_id,
        level=level,
        status=_comparison_status(matches, level),
        message=f"Bounding-box requirement {req_id!r} {'is satisfied' if matches else 'does not match the measured B-rep'}.",
        expected=expected_text,
        actual=actual_text,
        fix_hint="Adjust the model dimensions or update the spec to the intended overall size.",
        selector="model bounding box",
    )


def _eval_solids(req_id: str, level: str, req: dict[str, Any], ctx: Any) -> Any:
    expected_value = req["solids"]
    expected_count = expected_value.get("count") if isinstance(expected_value, dict) else expected_value
    actual_count = geometry.solid_count(_shape_or_skip(ctx))
    matches = actual_count == expected_count
    return _check(
        req_id,
        level=level,
        status=_comparison_status(matches, level),
        message=f"Solid-count requirement {req_id!r} {'is satisfied' if matches else 'does not match the measured B-rep'}.",
        expected=f"exactly {expected_count} solid(s)",
        actual=f"{actual_count} solid(s) from B-rep topology",
        fix_hint="Join or separate the modeled bodies to match the required solid count.",
        selector="model solids",
    )


def _grid_result(
    points: list[tuple[float, float, float]],
    cylinders: list[geometry.HoleCylinder],
    grid: list[Any],
    *,
    unit: str,
    req_id: str,
) -> tuple[bool | None, str, str]:
    expected_x = _range(grid[0], unit=unit, path=("requirements", req_id, "holes", "pattern", "grid", "x"))
    expected_y = _range(grid[1], unit=unit, path=("requirements", req_id, "holes", "pattern", "grid", "y"))
    normal_axis = max(range(3), key=lambda i: abs(cylinders[0].axis[i])) if cylinders else 2
    coordinate_axes = [i for i in range(3) if i != normal_axis]
    labels = ''.join('XYZ'[i] for i in coordinate_axes)
    expected = f"complete {labels} grid with pitches {_range_text(expected_x)} and {_range_text(expected_y)}"
    if any(abs(abs(cylinder.axis[normal_axis]) - 1.0) > 1e-8 for cylinder in cylinders):
        return None, expected, "unknown (grid axes require parallel holes along one principal coordinate axis)"
    points = [(point[coordinate_axes[0]], point[coordinate_axes[1]], 0.0) for point in points]
    if not points:
        return False, expected, "0 hole centers; no XY grid is present"
    coordinate_tol = 1e-6

    def unique_coords(values: list[float]) -> list[float]:
        result: list[float] = []
        for value in sorted(values):
            if not result or abs(value - result[-1]) > coordinate_tol:
                result.append(value)
        return result

    xs = unique_coords([point[0] for point in points])
    ys = unique_coords([point[1] for point in points])
    if len(xs) < 2 or len(ys) < 2:
        return False, expected, "a two-axis rectangular grid needs at least two X and two Y positions"
    if len(points) != len(xs) * len(ys):
        return False, expected, f"{len(points)} hole centers do not form a complete {len(xs)} by {len(ys)} grid"
    occupied={(next(i for i,x in enumerate(xs) if abs(point[0]-x)<=coordinate_tol),
               next(i for i,y in enumerate(ys) if abs(point[1]-y)<=coordinate_tol)) for point in points}
    if len(occupied)!=len(points):
        return False,expected,'duplicate XY axes leave at least one grid position unoccupied'
    pitches_x=[b-a for a,b in zip(xs,xs[1:])]
    pitches_y=[b-a for a,b in zip(ys,ys[1:])]
    matches = all(_within(value,expected_x) for value in pitches_x) and all(_within(value,expected_y) for value in pitches_y)
    actual = f"{labels[0]} pitches {pitches_x}, {labels[1]} pitches {pitches_y} mm across a complete {len(xs)}×{len(ys)} grid"
    return matches, expected, actual


def _eval_holes(req_id: str, level: str, req: dict[str, Any], unit: str, factor: float | None, ctx: Any) -> Any:
    if factor is None:
        raise geometry.UnsupportedGeometry("context units are not known")
    body = req["holes"]
    scope = body.get("scope", "all")
    if scope == "all" and not body.get('through', True):
        geometry.require_round_planar_openings(_shape_or_skip(ctx))
    if geometry.solid_count(_shape_or_skip(ctx)) == 0:
        raise geometry.UnsupportedGeometry("cylindrical bore counts require 3D solids")
    requested_count = body.get("count")
    diameter = _range(body["diameter"], unit=unit, path=("requirements", req_id, "holes", "diameter")) if "diameter" in body else None
    cylinders = geometry.inward_full_cylinders(_shape_or_skip(ctx), factor, cylindrical_only=scope == 'cylindrical' or body.get('through', True))
    bore_options = dict(shape=_shape_or_skip(ctx), mm_per_native_unit=factor, through=body.get('through', True))
    all_points = geometry.unique_hole_axes(cylinders, **bore_options)
    selected_points = (
        geometry.unique_hole_axes(cylinders, diameter_mm=(diameter.minimum, diameter.maximum), diameter_resolution_mm=1e-6 if diameter.implicit_exact else 0.0, **bore_options)
        if diameter is not None
        else all_points
    )
    if diameter is None:
        selected_cylinders = cylinders
    else:
        selected_cylinders = [
            cylinder
            for cylinder in cylinders
            if _within(2.0 * cylinder.radius_mm, diameter)
        ]
    selected_cylinders = [c for c in selected_cylinders if geometry.unique_hole_axes([c], **bore_options)]
    failures: list[str] = []
    skips: list[str] = []
    if scope == 'all' and body.get('through', True):
        topology_count = geometry.closed_shell_tunnels(_shape_or_skip(ctx))
        if topology_count != len(all_points):
            # A noncircular/branched tunnel must not disappear just because
            # the cylindrical recognizer has no wall to measure.
            skips.append('noncircular, branched or unclassified through geometry prevents complete bore-count coverage')
    expected_parts = [
        ("all through tunnels; unclassified dimensions withhold coverage" if body.get('through', True)
         else "open cylindrical bores including blind bores; noncircular openings withhold coverage") if scope == "all"
        else "cylindrical bores only; other opening types are outside this requirement"
    ]
    actual_parts = list(failures) + list(skips)
    if requested_count is not None:
        expected_parts.append(f"{requested_count} recognized hole(s)")
        actual_parts.append(f"{len(selected_points)} matching analytic hole axis/axes")
        if len(selected_points) != requested_count:
            failures.append("recognized hole count differs")
    if diameter is not None:
        expected_parts.append(f"diameter {_range_text(diameter)}")
        observed_diameters = sorted({round(2 * cylinder.radius_mm, 8) for cylinder in cylinders})
        actual_parts.append(
            "recognized cylindrical diameters "
            + (", ".join(f"{_actual_value(value, power=1, unit=unit)}" for value in observed_diameters) if observed_diameters else "none")
        )
        if not all_points:
            failures.append("no analytic hole diameter was measured")
        elif requested_count is None and len(selected_points) != len(all_points):
            failures.append("one or more recognized hole axes lack a cylinder in the requested diameter range")
    if "pattern" in body:
        grid_match, grid_expected, grid_actual = _grid_result(
            selected_points,
            selected_cylinders,
            body["pattern"]["grid"],
            unit=unit,
            req_id=req_id,
        )
        expected_parts.append(grid_expected)
        actual_parts.append(grid_actual)
        if grid_match is None:
            skips.append("the requested XY grid could not be established from the recognized B-rep axes")
        elif not grid_match:
            failures.append("recognized hole centers do not match the requested XY grid")
    matches = not failures and not skips
    status = _comparison_status(matches, level) if not skips else "skip"
    if failures and skips:
        status = _comparison_status(False, level)
    message = f"{'Cylindrical bore' if scope == 'cylindrical' else 'Opening-count'} requirement {req_id!r} "
    if status == "pass":
        message += "is satisfied by analytic inward cylindrical faces."
    elif status == "skip":
        message += "could not be fully verified from the available analytic B-rep evidence."
    else:
        message += "does not match the recognized analytic B-rep evidence."
    return _check(
        req_id,
        level=level,
        status=status,
        message=message,
        expected="; ".join(expected_parts),
        actual="; ".join(actual_parts),
        fix_hint="Use full cylindrical bores with the requested count, diameter, and XY grid; inspect unsupported partial or non-vertical bores manually.",
        selector="inward full-revolution cylindrical B-rep faces",
    )


def _eval_clearance(req_id: str, level: str, req: dict[str, Any], unit: str, factor: float | None, ctx: Any) -> Any:
    if factor is None:
        raise geometry.UnsupportedGeometry("context units are not known")
    body = req["clearance"]
    expected_range = _range(
        {key: body[key] for key in ("min", "max") if key in body},
        unit=unit,
        path=("requirements", req_id, "clearance"),
    )
    try:
        actual_mm = geometry.step_asset_distance_mm(_shape_or_skip(ctx),getattr(ctx,'project_dir',None),body['with'],factor)
    except geometry.GeometryOverlap as exc:
        return _check(req_id,level=level,status=_comparison_status(False,level),
            message='Overlapping solids do not satisfy a clearance requirement.',
            expected=f"against {body['with']}: {_range_text(expected_range)} with no solid overlap",
            actual=f'solid intersection volume {_text_number(exc.volume_mm3)} mm³',
            fix_hint='Remove the solid overlap and rerun the clearance measurement.',
            selector=f"model versus project asset {body['with']}")
    matches = _within(actual_mm, expected_range)
    return _check(
        req_id,
        level=level,
        status=_comparison_status(matches, level),
        message=f"Clearance requirement {req_id!r} {'is satisfied' if matches else 'does not match the measured STEP clearance'}.",
        expected=f"against {body['with']}: {_range_text(expected_range)}",
        actual=f"minimum B-rep distance {_actual_value(actual_mm, power=1, unit=unit)}",
        fix_hint="Move the model or STEP asset to provide the specified clearance, and keep the reference asset inside the project.",
        selector=f"model versus project asset {body['with']}",
    )


def _load_material(material_id: str) -> dict[str, Any] | None:
    try:
        module = importlib.import_module(".materials", package=__package__)
    except ModuleNotFoundError as exc:
        if exc.name == f"{__package__}.materials":
            return None
        raise
    getter = getattr(module, "get_material", None)
    if not callable(getter):
        return None
    result = getter(material_id)
    return result if isinstance(result, dict) else None


def _eval_mass(req_id: str, level: str, req: dict[str, Any], unit: str, factor: float | None, ctx: Any, doc: dict[str, Any]) -> Any:
    body = req["mass_g"]
    mass_range = _range(body if isinstance(body, dict) else {"max": body}, unit=unit, power=0, path=("requirements", req_id, "mass_g"))
    material_id = getattr(ctx, "material", None) or doc.get("material")
    if doc.get('material') and getattr(ctx,'material',None) and doc['material']!=ctx.material:
        raise geometry.UnsupportedGeometry('context material conflicts with the requested material')
    if not isinstance(material_id, str) or not material_id.strip():
        raise geometry.UnsupportedGeometry("material is not identified; mass cannot be measured")
    material = _load_material(material_id)
    if material is None:
        raise geometry.UnsupportedGeometry(f"material data for {material_id!r} is unavailable")
    density = finite(material.get("density_g_cm3"))
    if density <= 0:
        raise geometry.UnsupportedGeometry(f"material {material_id!r} has no positive finite density_g_cm3")
    if factor is None:
        raise geometry.UnsupportedGeometry("context units are not known")
    volume = geometry.volume_mm3(_shape_or_skip(ctx), factor)
    mass_g = finite(volume * density / 1000.0)
    matches = _within(mass_g, mass_range)
    return _check(
        req_id,
        level=level,
        status=_comparison_status(matches, level),
        message=f"Nominal mass estimate for {req_id!r} {'satisfies the requested range' if matches else 'does not match the requested range'}; finished-part mass is unproven.",
        expected=f"nominal mass using material {material_id}: {_range_text(mass_range)} g",
        actual=f"{_text_number(mass_g)} g from B-rep volume and {density:g} g/cm³ density; nominal fully dense estimate",
        assumptions=f"Material definition: {material.get('name', material_id)}. Fully dense solid; no infill, moisture, coatings or temperature adjustment. Nominal density, not a certified finished-part mass. Source: {material.get('source_url', 'not supplied by material provider')}",
        fix_hint="For nominal mass, change modeled volume or choose the matching sourced material. Measure the manufactured part when finished-part mass must be established.",
        selector="model B-rep volume",
    )


def _eval_symmetry(req_id: str, level: str, req: dict[str, Any], unit: str, factor: float | None, ctx: Any) -> Any:
    if factor is None:
        raise geometry.UnsupportedGeometry("context units are not known")
    body = req["symmetric"]
    tolerance = _range(body["tolerance"], unit=unit, path=("requirements", req_id, "symmetric", "tolerance"))
    tolerance_mm = tolerance.minimum if tolerance.minimum is not None else None
    if tolerance_mm is None or tolerance_mm < 0:
        raise geometry.UnsupportedGeometry("symmetry tolerance is invalid")
    difference_mm3 = geometry.symmetry_difference_volume(_shape_or_skip(ctx), body["plane"], factor)
    expected = (
        f"a certified pointwise mirror-deviation bound about {body['plane']} ≤ {_actual_value(tolerance_mm, power=1, unit=unit)}"
    )
    actual = f"B-rep symmetric-difference volume {_text_number(difference_mm3)} mm^3"
    separation = geometry.mirror_separation_mm(_shape_or_skip(ctx), body['plane'], factor)
    if separation > tolerance_mm + 1e-6:
        return _check(req_id, level=level, status=_comparison_status(False, level),
            message=f"Symmetry requirement {req_id!r} fails: the model and its mirror are separated beyond tolerance.",
            expected=expected, actual=actual + f"; minimum model-to-mirror separation {separation:.8g} mm",
            fix_hint="Place corresponding geometry within the requested mirror tolerance.")
    # Booleans may erase differences below kernel resolution. No certified
    # pointwise error bound is exposed here, including when volume is zero.
    status = "skip"
    message = f"Symmetry requirement {req_id!r} has only a volume diagnostic; its pointwise tolerance is unproven."
    actual += f"; pointwise deviation relative to {_actual_value(tolerance_mm, power=1, unit=unit)} and the kernel error bound are unknown"
    return _check(
        req_id,
        level=level,
        status=status,
        message=message,
        expected=expected,
        actual=actual,
        fix_hint="Keep this requirement unverified and inspect a mirror overlay until a comparison bounds pointwise deviation and kernel error.",
        selector=f"B-rep reflected about {body['plane']}",
    )


def _eval_material_point(req_id: str, level: str, req: dict[str, Any], unit: str, factor: float | None, native_factor: float | None, ctx: Any) -> Any:
    if factor is None or native_factor is None:
        raise geometry.UnsupportedGeometry("context units are not known")
    body = req["material_at_point"]
    point_mm = tuple(
        _range(coord, unit=unit, path=("requirements", req_id, "material_at_point", "point", axis)).minimum
        for axis, coord in zip("xyz", body["point"], strict=True)
    )
    point_native = tuple(value / native_factor for value in point_mm)
    state = geometry.material_state_at_point(_shape_or_skip(ctx), point_native)
    if state == "boundary":
        raise geometry.UnsupportedGeometry("point lies on a B-rep boundary within the OCCT classifier tolerance")
    actual = "solid material" if state == "solid" else "void"
    expected = f"{body['expected']} at " + ", ".join(
        _actual_value(value, power=1, unit=unit) for value in point_mm
    )
    matches = state == body["expected"]
    return _check(
        req_id,
        level=level,
        status=_comparison_status(matches, level),
        message=f"Local material-at-point requirement {req_id!r} {'is satisfied' if matches else 'does not match the classified B-rep'}.",
        expected=expected,
        actual=f"{actual} at the requested point",
        fix_hint="Move the point away from a boundary or adjust the solid/void geometry at that location.",
        selector="B-rep point classification",
    )


def _eval_custom_measure(req_id: str, level: str, req: dict[str, Any], unit: str, factor: float | None, ctx: Any) -> Any:
    if factor is None:
        raise geometry.UnsupportedGeometry("context units are not known")
    measure = req["measure"]
    actual, power = geometry.selector_measurement(ctx, measure, factor)
    range_spec: Any
    if "equals" in req:
        range_spec = req["equals"]
    else:
        range_spec = {key: req[key] for key in ("min", "max") if key in req}
    expected_range = _range(
        range_spec,
        unit=unit,
        power=power,
        path=("requirements", req_id, "measure"),
    )
    matches = _within(actual, expected_range)
    selector = f"{measure['kind']}: {measure.get('a', measure.get('selector'))}"
    if measure.get("b"):
        selector += f" to {measure['b']}"
    return _check(
        req_id,
        level=level,
        status=_comparison_status(matches, level),
        message=f"Custom selector measurement {req_id!r} {'is satisfied' if matches else 'does not match the resolved geometry'}.",
        expected=_range_text(expected_range),
        actual=_actual_value(actual, power=power, unit=unit) if power else _text_number(actual),
        fix_hint="Correct the semantic selectors or change the model so their resolved geometry meets the stated measurement.",
        selector=selector,
    )


def _evaluate_one(req: dict[str, Any], index: int, doc: dict[str, Any], ctx: Any, native_factor: float | None) -> Any:
    req_id = _requirement_id(req, index)
    level = _level(req)
    types = [key for key in req if key not in {"id", "level"}]
    supported = {
        "bbox",
        "solids",
        "holes",
        "min_wall",
        "clearance",
        "mass_g",
        "symmetric",
        "measure",
        "material_at_point",
    }
    known = [key for key in types if key in supported]
    if len(known) != 1:
        type_name = ", ".join(types) if types else "no measurement type"
        return _check(
            req_id,
            level=level,
            status="skip",
            message=f"Requirement {req_id!r} has unknown or ambiguous measurement type(s): {type_name}.",
            expected="one supported requirement type with a measurable specification",
            actual="unknown (this requirement type is not evaluated)",
            fix_hint="Use one documented requirement type or add an explicit evaluator for this type.",
        )
    field = known[0]
    unit = doc.get("units", "mm")
    if field == "bbox":
        return _eval_bbox(req_id, level, req, unit, native_factor, ctx)
    if field == "solids":
        return _eval_solids(req_id, level, req, ctx)
    if field == "holes":
        return _eval_holes(req_id, level, req, unit, native_factor, ctx)
    if field == "min_wall":
        target = _range(req[field], unit=unit, path=("requirements", req_id, field))
        from .process_geometry import wall_thickness_screen, GeometryUnavailable
        try:
            sampled = wall_thickness_screen(_shape_or_skip(ctx), units=getattr(ctx, 'units', None),
                tolerance_mm=1e-5, max_faces=256, max_samples=4096)
            lower = target.minimum
            if lower is not None and sampled.minimum_mm < lower - 1e-6:
                return _check(req_id, level=level, status=_comparison_status(False, level),
                    message=f"Minimum-wall requirement {req_id!r} fails at a measured local wall ray.",
                    expected=f"global minimum wall at least {lower:.8g} mm",
                    actual=f"local wall ray {sampled.minimum_mm:.8g} mm at face {sampled.face_index}",
                    fix_hint="Thicken the reported local wall and rerun the screen; passing samples do not prove a global minimum.")
        except GeometryUnavailable:
            pass
        return _check(
            req_id,
            level=level,
            status="skip",
            message=f"Global minimum-wall requirement {req_id!r} is not measured by the current evaluator.",
            expected=f"at least {_range_text(target)} global wall thickness",
            actual="unknown (a bbox or sparse point samples cannot establish global minimum thickness)",
            fix_hint="Use a validated global B-rep thickness algorithm or add a local material_at_point check for specific locations.",
            selector="global B-rep wall thickness",
        )
    if field == "clearance":
        return _eval_clearance(req_id, level, req, unit, native_factor, ctx)
    if field == "mass_g":
        return _eval_mass(req_id, level, req, unit, native_factor, ctx, doc)
    if field == "symmetric":
        return _eval_symmetry(req_id, level, req, unit, native_factor, ctx)
    if field == "measure":
        return _eval_custom_measure(req_id, level, req, unit, native_factor, ctx)
    if field == "material_at_point":
        spec_factor = mm_per_unit(unit)
        return _eval_material_point(req_id, level, req, unit, spec_factor, native_factor, ctx)
    raise AssertionError(f"unhandled requirement type {field}")


def _issue_check(issue: SpecIssue, number: int) -> Any:
    path = ".".join(str(item) for item in issue.path) or "document"
    return _diagnostic(
        f"spec:input:{number}",
        f"Invalid spec input at {path}: {issue.message}",
        expected="a valid spec document with unique IDs, valid levels, finite numeric values, and supported requirement shapes",
        actual=issue.message,
        fix_hint="Correct the reported field and rerun the spec checks.",
    )


def spec_checks(ctx: Any) -> list[Any]:
    """Evaluate each declared requirement once and return explicit evidence.

    Geometry data comes from ``ctx.shape`` and lazy OCCT operations only. Cached
    ``ctx.metrics`` and parameter values are never treated as measurements.
    """
    raw = getattr(ctx, "spec", None)
    if raw is None or isinstance(raw,dict) and not raw or isinstance(raw,str) and not raw.strip():
        raw={'requirements':[]}
    document, issues = parse_spec_for_checks(raw)
    if document is None:
        issue = issues[0] if issues else SpecIssue("spec could not be parsed")
        return [_issue_check(issue, 1)]

    requirements = document.get("requirements")
    if not isinstance(requirements, list):
        return [_issue_check(issues[0] if issues else SpecIssue("requirements must be a list"), 1)]

    checks = [_issue_check(issue, number) for number, issue in enumerate((i for i in issues if i.requirement_index is None), 1)]
    issues_by_index: dict[int, list[SpecIssue]] = defaultdict(list)
    for issue in issues:
        if issue.requirement_index is not None:
            issues_by_index[issue.requirement_index].append(issue)

    if not requirements:
        checks.append(
            _diagnostic(
                "spec:empty",
                "The spec declares no requirements to verify.",
                expected="at least one declared requirement",
                actual="0 requirements",
                fix_hint="Add one or more requirements to spec.yaml.",
            )
        )
        return checks

    spec_unit = document.get("units", "mm")
    try:
        mm_per_unit(spec_unit)
    except (TypeError, ValueError) as exc:
        native_factor = None
        checks.append(
            _diagnostic(
                "spec:context_units",
                f"Spec units are invalid: {exc}",
                expected="units: mm or units: in",
                actual=str(spec_unit),
                fix_hint="Set the spec units to mm or in.",
            )
        )
    else:
        ctx_unit = getattr(ctx, "units", spec_unit)
        try:
            native_factor = mm_per_unit(ctx_unit)
        except (TypeError, ValueError) as exc:
            native_factor = None
            checks.append(
                _diagnostic(
                    "spec:context_units",
                    f"Model context units are invalid: {exc}",
                    expected="ModelContext.units is mm or in",
                    actual=str(ctx_unit),
                    fix_hint="Set the model context units to mm or in before evaluating geometry.",
                )
            )

    for index, req in enumerate(requirements):
        req_id = _requirement_id(req, index)
        req_issues = issues_by_index.get(index, [])
        if req_issues:
            details = "; ".join(issue.message for issue in req_issues)
            checks.append(
                _diagnostic(
                    req_id,
                    f"Requirement {req_id!r} is invalid: {details}",
                    expected="one well-shaped requirement with a unique non-empty id and level error or warn",
                    actual=details,
                    fix_hint="Correct this requirement before relying on its result.",
                )
            )
            continue
        if not isinstance(req, dict):
            # The parser normally records this as an issue; keep a defensive
            # per-requirement check if an unusual Mapping implementation slips in.
            checks.append(
                _diagnostic(
                    req_id,
                    f"Requirement {req_id!r} is not a mapping.",
                    expected="a requirement mapping",
                    actual=type(req).__name__,
                    fix_hint="Replace this list item with a mapping containing id and one requirement type.",
                )
            )
            continue
        try:
            checks.append(_evaluate_one(req, index, document, ctx, native_factor))
        except (geometry.GeometryUnavailable, geometry.UnsupportedGeometry) as exc:
            checks.append(
                _check(
                    req_id,
                    level=_level(req),
                    status="skip",
                    message=f"Requirement {req_id!r} could not be verified: {exc}.",
                    expected=f"a measured result for {next((key for key in req if key not in {'id', 'level'}), 'unknown')}",
                    actual="unknown (required geometry evidence is unavailable)",
                    fix_hint="Provide the required model shape, asset, material data, or supported selector evidence.",
                )
            )
        except Exception as exc:
            checks.append(
                _check(
                    req_id,
                    level=_level(req),
                    status="skip",
                    message=f"Requirement {req_id!r} raised an evaluator exception: {type(exc).__name__}: {exc}.",
                    expected="a finite measurement from valid geometry evidence",
                    actual="unknown (evaluation did not produce a trustworthy measurement)",
                    fix_hint="Correct the input or geometry error and rerun the evaluator; this check is not proven.",
                )
            )
    return checks


def _check_mapping(value: Any) -> dict[str, Any] | None:
    try:
        mapping = record(value)
    except Exception:
        return None
    return mapping if isinstance(mapping, dict) else None


def verification_ready(spec: Any, checks: Sequence[Any], hard_checks: Sequence[Any], *, process: str | None) -> bool:
    """Return true only when every declared error requirement is proven.

    Readiness requires a valid non-empty spec, exact unique coverage by spec
    checks, at least one measured passing requirement, an entirely passing
    non-empty hard gate, and pass status for every error-level check supplied
    from every suite. Pass the context process explicitly so a project-only
    process cannot bypass evidence requirements. Unknown warning-level spec
    results also withhold readiness; explicitly advisory process screens do not.
    """
    try:
        document = load_spec(spec)
    except Exception:
        return False
    if process is not None and (not isinstance(process,str) or not process.strip()):
        return False
    requirements = document.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return False
    requirement_ids: list[str] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            return False
        req_id = requirement.get("id")
        if not isinstance(req_id, str) or not req_id.strip() or req_id != req_id.strip():
            return False
        if requirement.get("level", "error") not in {"error", "warn"}:
            return False
        requirement_ids.append(req_id)
    if len(set(requirement_ids)) != len(requirement_ids):
        return False

    try:
        all_checks = [_check_mapping(check) for check in checks]
        hard = [_check_mapping(check) for check in hard_checks]
    except Exception:
        return False
    if any(check is None for check in all_checks) or any(check is None for check in hard):
        return False
    check_maps = [check for check in all_checks if check is not None]
    hard_maps = [check for check in hard if check is not None]
    if not hard_maps or not any(check.get('level')=='error' for check in hard_maps) or any(check.get("suite") != "hard" or check.get("status") != "pass" for check in hard_maps):
        return False

    spec_checks_only = [check for check in check_maps if check.get("suite") == "spec"]
    if any(check.get('status')=='skip' for check in check_maps if check.get('suite') == 'spec' or (check.get('suite') == 'process' and check.get('level') == 'error')):
        return False
    declared=document.get('process')
    if process and declared and process!=declared:
        return False
    effective_process=process or declared
    if (effective_process or document.get('process_settings')) and not any(check.get('suite')=='process' for check in check_maps):
        return False
    if effective_process:
        from .process_profiles import PROCESS_NAMES
        requested=effective_process
        if requested not in PROCESS_NAMES or not any(
            check.get('suite')=='process' and str(check.get('id','')).startswith(f'process.{requested}.') for check in check_maps
        ):
            return False
    spec_ids = [check.get("id") for check in spec_checks_only]
    if len(spec_ids) != len(requirement_ids) or any(not isinstance(check_id, str) for check_id in spec_ids):
        return False
    if len(set(spec_ids)) != len(spec_ids) or set(spec_ids) != set(requirement_ids):
        return False

    by_id = {check.get("id"): check for check in spec_checks_only}
    measured_pass = False
    for requirement in requirements:
        req_id = requirement["id"]
        check = by_id.get(req_id)
        if check is None:
            return False
        expected_level = requirement.get("level", "error")
        if check.get("level") != expected_level:
            return False
        status = check.get("status")
        if expected_level == "error" and status != "pass":
            return False
        if status == "pass":
            expected = check.get("expected")
            actual = check.get("actual")
            if not isinstance(expected, str) or not expected.strip() or not isinstance(actual, str) or not actual.strip():
                return False
            if "unknown" in actual.lower() or "exception" in actual.lower():
                return False
            measured_pass = True

    if not measured_pass:
        return False
    for check in check_maps + hard_maps:
        level = check.get("level")
        if level == "error" and check.get("status") != "pass":
            return False
        if level not in {"error", "warn"}:
            return False
        if check.get('status') not in {'pass','fail','warn','skip'} or check.get('suite') not in {'hard','spec','process','interference','2d'}:
            return False
        if check.get('status')=='pass':
            actual=check.get('actual')
            if actual is not None and not isinstance(actual,str):
                return False
            if check.get('suite') in {'spec','process'} and (not isinstance(actual,str) or not actual.strip() or 'unknown' in actual.lower() or 'exception' in actual.lower()):
                return False
            if isinstance(actual,str) and (re.search(r'(?<![\w])[-+]?(?:nan|inf(?:inity)?)(?![\w])',actual,re.IGNORECASE) or '∞' in actual):
                return False
        if check.get('suite')=='spec' and check.get('level')=='error' and check.get('heuristic') is True:
            return False
    return True


__all__ = ["spec_checks", "load_spec", "verification_ready"]
