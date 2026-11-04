"""Conservative heuristic manufacturing screens for recognized B-reps."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any
from types import SimpleNamespace

from ._checks import finite, make_check, mm_per_unit
from .process_geometry import (
    GeometryUnavailable,
    approach_probe_collision_mm3,
    bend_surface_pairs,
    cylindrical_surfaces,
    feature_for_face,
    overhang_screen,
    surface_samples,
    through_cylindrical_features,
    wall_thickness_screen,
)
from .process_profiles import PROCESS_NAMES, process_settings


def _check(
    *,
    check_id: str,
    level: str,
    status: str,
    message: str,
    expected: str,
    actual: str,
    assumptions: str,
    fix_hint: str,
    feature: str | None = None,
    selector: str | None = None,
) -> Any:
    return make_check(
        id=check_id,
        suite="process",
        level=level,
        status=status,
        message=message,
        expected=expected,
        actual=actual,
        feature=feature,
        selector=selector,
        heuristic=True,
        assumptions=assumptions,
        fix_hint=fix_hint,
    )


def _context_assumptions(process: str, units: Any, tolerance_mm: float | None = None) -> str:
    tol = f" Configured comparison band is ±{tolerance_mm:g} mm; numerical classifier floors and kernel tolerances also apply, so this is not a measurement-accuracy guarantee." if tolerance_mm is not None else ""
    return (
        f"Process profile {process!r}; ctx.units={units!r}. This is a finite geometric screen, "
        f"not a process certification.{tol} Unknown setup, material behavior, supports, and "
        "unrecognized geometry remain unknown."
    )


def _skip(
    check_id: str,
    *,
    level: str,
    reason: str,
    expected: str,
    assumptions: str,
    fix_hint: str,
    feature: str | None = None,
    selector: str | None = None,
) -> Any:
    return _check(
        check_id=check_id,
        level=level,
        status="skip",
        message=f"Process screen skipped: {reason}.",
        expected=expected,
        actual=reason,
        assumptions=assumptions,
        fix_hint=fix_hint,
        feature=feature,
        selector=selector,
    )


def _geometry_or_skip(ctx: Any, check_id: str, level: str, assumption: str, expected: str, fix_hint: str):
    shape = getattr(ctx, "shape", None)
    if shape is None:
        return None, _skip(
            check_id,
            level=level,
            reason="ctx.shape is missing, so no measured geometry is available",
            expected=expected,
            assumptions=assumption,
            fix_hint=fix_hint,
        )
    return shape, None


def _finding_status(level: str, violates: bool, near_tolerance: bool = False) -> str:
    if near_tolerance:
        return "warn"
    if not violates:
        return "pass"
    return "fail" if level == "error" else "warn"


def _fnum(value: float, places: int = 3) -> str:
    return f"{finite(value):.{places}f}"


def _additive_checks(ctx: Any, process: str, settings: dict[str, Any]) -> list[Any]:
    level = settings["level"]
    units = getattr(ctx, "units", None)
    try:
        scale = mm_per_unit(units)
    except ValueError as exc:
        return [
            _skip(
                f"process.{process}.units",
                level="error",
                reason=str(exc),
                expected="ctx.units is 'mm' or 'in'",
                assumptions=_context_assumptions(process, units),
                fix_hint="Set ctx.units to 'mm' or 'in' before evaluating process geometry.",
            )
        ]
    tolerance = settings["tolerance_mm"]
    samples_clause = (
        f"at most {settings['max_faces']} faces and {settings['max_samples']} classified surface samples"
    )
    direction = settings["build_direction"]
    slope_limit = settings["min_downward_slope_deg"]
    overhang_assumption = (
        f"Orientation assumes build_direction={tuple(round(x, 5) for x in direction)} (default +Z); "
        f"downward planar/curved B-rep normals are screened at a {slope_limit:g}° minimum "
        "surface slope above horizontal; contact within tolerance of the actual B-rep lower bound in the build frame is excluded. "
        f"Sampling is {samples_clause}; no support placement, bridging span, resin behavior, or global guarantee. "
        f"{_context_assumptions(process, units, tolerance)}"
    )
    shape, missing = _geometry_or_skip(
        ctx,
        f"process.{process}.overhang",
        level,
        overhang_assumption,
        f"no sampled downward face below {slope_limit:g}° from horizontal (angle tolerance ±{settings['angle_tolerance_deg']:g}°)",
        "Provide a built B-rep and/or change process_settings.build_direction, then review support and bridging needs.",
    )
    checks: list[Any] = []
    if missing is not None:
        checks.append(missing)
    else:
        try:
            result = overhang_screen(
                shape,
                units=units,
                build_direction=direction,
                min_downward_slope_deg=slope_limit,
                tolerance_mm=tolerance,
                max_faces=settings["max_faces"],
                max_samples=settings["max_samples"],
            )
            actual = (
                "no downward sample above the assumed build plane; "
                if result.minimum_downward_slope_deg is None
                else (
                    f"minimum sampled downward slope={_fnum(result.minimum_downward_slope_deg)}°; "
                    f"{result.violating_samples}/{result.downward_samples} downward samples below threshold; "
                )
            ) + f"{result.total_samples} total samples"
            if result.sample_limit_reached:
                actual += "; finite sample limit reached"
            near = (
                result.minimum_downward_slope_deg is not None
                and abs(result.minimum_downward_slope_deg - slope_limit) <= settings["angle_tolerance_deg"]
                and result.violating_samples == 0
            )
            violates = result.violating_samples > 0
            status = _finding_status(level, violates, near or result.sample_limit_reached and not violates)
            message = (
                "A sampled downward-facing surface is shallower than the assumed self-support screen."
                if violates
                else "No sampled downward-facing surface crossed the assumed self-support screen."
            )
            feature = (
                feature_for_face(ctx, shape, result.face_index)
                if result.face_index is not None
                else None
            )
            checks.append(
                _check(
                    check_id=f"process.{process}.overhang",
                    level=level,
                    status=status,
                    message=message,
                    expected=f"sampled slope ≥ {slope_limit:g}° above horizontal, within ±{settings['angle_tolerance_deg']:g}°",
                    actual=actual,
                    assumptions=overhang_assumption,
                    fix_hint="Reorient the part, add or move supports, or reduce the unsupported span; inspect sections because this screen samples normals only.",
                    feature=feature,
                )
            )
        except (GeometryUnavailable, ValueError, TypeError) as exc:
            checks.append(
                _skip(
                    f"process.{process}.overhang",
                    level=level,
                    reason=str(exc),
                    expected=f"finite B-rep normal samples for {samples_clause}",
                    assumptions=overhang_assumption,
                    fix_hint="Provide a supported solid B-rep and a finite build_direction vector.",
                )
            )

    if process == "fdm_pla" and "min_wall_mm" in settings:
        wall_limit = settings["min_wall_mm"]
        wall_basis = "explicit min_wall_mm"
    elif process == "fdm_pla" and "nozzle_diameter_mm" in settings:
        line_count = settings["wall_line_count"]
        wall_limit = finite(settings["nozzle_diameter_mm"] * line_count)
        wall_basis = f"nozzle_diameter_mm × wall_line_count ({line_count} assumed line widths)"
    elif process == "sla" and "min_wall_mm" in settings:
        wall_limit = settings["min_wall_mm"]
        wall_basis = "explicit min_wall_mm"
    else:
        wall_limit = None
        wall_basis = "no wall threshold configured"

    wall_assumption = (
        f"Orientation does not change the thickness rays; threshold comes from {wall_basis}. "
        f"At most {settings['max_faces']} faces and {settings['max_samples']} interior surface points are sampled; "
        "each ray follows the sampled outward face normal into one solid and records its first B-rep exit. "
        "A ray minimum is not a global wall-thickness proof and may miss narrow or curved regions. "
        f"{_context_assumptions(process, units, tolerance)}"
    )
    if wall_limit is None:
        needed = "min_wall_mm" if process == "sla" else "min_wall_mm or nozzle_diameter_mm"
        checks.append(
            _skip(
                f"process.{process}.wall_thickness",
                level=level,
                reason=f"{needed} is not configured; no wall threshold was inferred",
                expected=f"measured sampled wall ≥ configured threshold ±{tolerance:g} mm",
                assumptions=wall_assumption,
                fix_hint=f"Set spec.process_settings.{needed.split(' or ')[0]} to an explicit mm assumption; FDM may instead set nozzle_diameter_mm and wall_line_count.",
            )
        )
    elif shape is None:
        checks.append(
            _skip(
                f"process.{process}.wall_thickness",
                level=level,
                reason="ctx.shape is missing, so no thickness rays can be cast",
                expected=f"sampled wall ≥ {_fnum(wall_limit)} mm within ±{tolerance:g} mm",
                assumptions=wall_assumption,
                fix_hint="Provide the built solid B-rep and the process-specific wall threshold.",
            )
        )
    else:
        try:
            result = wall_thickness_screen(
                shape,
                units=units,
                tolerance_mm=tolerance,
                max_faces=settings["max_faces"],
                max_samples=settings["max_samples"],
            )
            difference = result.minimum_mm - wall_limit
            violates = difference < -tolerance
            near = abs(difference) <= tolerance
            status = _finding_status(level, violates, near or (result.sample_limit_reached or result.samples_missed > 0) and not violates)
            actual = (
                f"minimum of {result.samples_hit} successful rays={_fnum(result.minimum_mm)} mm; "
                f"{result.samples_missed} rays had no opposing hit"
            )
            if result.sample_limit_reached:
                actual += "; finite sample limit reached"
            checks.append(
                _check(
                    check_id=f"process.{process}.wall_thickness",
                    level=level,
                    status=status,
                    message=(
                        "A sampled wall ray is below the configured screening threshold."
                        if violates
                        else "Sampled wall rays meet the configured screening threshold."
                    ),
                    expected=f"sampled wall ≥ {_fnum(wall_limit)} mm within ±{tolerance:g} mm",
                    actual=actual,
                    assumptions=wall_assumption,
                    fix_hint="Increase local material thickness or change the explicit threshold after reviewing the feature; ray sampling does not certify unsampled regions.",
                    feature=feature_for_face(ctx, shape, result.face_index),
                )
            )
        except (GeometryUnavailable, ValueError, TypeError) as exc:
            checks.append(
                _skip(
                    f"process.{process}.wall_thickness",
                    level=level,
                    reason=str(exc),
                    expected=f"sampled wall ≥ {_fnum(wall_limit)} mm within ±{tolerance:g} mm",
                    assumptions=wall_assumption,
                    fix_hint="Provide one finite solid B-rep, supported units, and an explicit wall threshold.",
                )
            )
    return checks


def _cnc_checks(ctx: Any, settings: dict[str, Any]) -> list[Any]:
    process = "cnc_3axis"
    level = settings["level"]
    units = getattr(ctx, "units", None)
    try:
        mm_per_unit(units)
    except ValueError as exc:
        return [
            _skip(
                "process.cnc_3axis.units",
                level="error",
                reason=str(exc),
                expected="ctx.units is 'mm' or 'in'",
                assumptions=_context_assumptions(process, units),
                fix_hint="Set ctx.units to 'mm' or 'in' before evaluating tool and cavity geometry.",
            )
        ]
    tolerance = settings["tolerance_mm"]
    context = _context_assumptions(process, units, tolerance)
    shape = getattr(ctx, "shape", None)
    checks: list[Any] = []
    cylinders = []
    cylinder_error: str | None = None
    if shape is not None:
        try:
            cylinders = cylindrical_surfaces(shape, units=units, tolerance_mm=tolerance,max_faces=settings['max_faces'])
        except (GeometryUnavailable, ValueError, TypeError) as exc:
            cylinder_error = str(exc)

    inward = [cylinder for cylinder in cylinders if cylinder.inward]
    mouths = [(cylinder, mouth) for cylinder in inward if cylinder.angular_span >= 2*math.pi-1e-5 for mouth in cylinder.mouths]
    access_assumption = (
        "Recognizes inward-oriented cylindrical B-rep faces and circular edges shared with a planar face whose normal is within 0.98 dot of the cylinder axis. "
        "For each such opening, an explicit solid cylindrical probe is swept coaxially from outside the opening through that cylinder segment: radius is configured approach_probe_radius_mm, else tool_diameter_mm/2, else recognized bore radius minus approach_clearance_mm; outside length is approach_length_mm. "
        "OCCT solid intersection volume is measured with a ±" + f"{tolerance:g} mm" + " end tolerance. This does not identify a real screw, cutter, fixture, machine setup, or other approach direction. "
        + context
    )
    if shape is None:
        checks.append(
            _skip(
                "process.cnc_3axis.hole_approach_probe",
                level=level,
                reason="ctx.shape is missing, so no B-rep cavity or probe intersection is available",
                expected="zero swept-probe overlap at each recognized inward cylindrical opening",
                assumptions=access_assumption,
                fix_hint="Provide the built B-rep and configure approach_probe_radius_mm or the assumed bore-clearance probe.",
            )
        )
    elif cylinder_error:
        checks.append(
            _skip(
                "process.cnc_3axis.hole_approach_probe",
                level=level,
                reason=cylinder_error,
                expected="recognized inward cylindrical B-rep faces and planar mouths",
                assumptions=access_assumption,
                fix_hint="Provide finite cylindrical B-rep faces with a planar opening, or treat access as unknown.",
            )
        )
    elif not mouths:
        reason = (
            "no inward cylindrical cavity was recognized"
            if not inward
            else "inward cylindrical faces had no recognized circular mouth adjoining an axial planar face"
        )
        checks.append(
            _skip(
                "process.cnc_3axis.hole_approach_probe",
                level=level,
                reason=reason,
                expected="a finite coaxial swept probe for each recognized inward cylindrical opening",
                assumptions=access_assumption,
                fix_hint="Add a recognized circular opening or configure another process-specific access method; access remains unknown.",
            )
        )
    else:
        tested: list[tuple[float, Any, Any, float]] = []
        probe_skips: list[str] = []
        for cylinder, mouth in mouths[:128]:
            if "approach_probe_radius_mm" in settings:
                probe_radius = settings["approach_probe_radius_mm"]
            elif "tool_diameter_mm" in settings:
                probe_radius = settings["tool_diameter_mm"] / 2.0
            elif "tool_radius_mm" in settings:
                probe_radius = settings["tool_radius_mm"]
            else:
                probe_radius = cylinder.radius_mm - settings["approach_clearance_mm"]
            if probe_radius <= tolerance:
                probe_skips.append(f"face {cylinder.face_index}: derived probe radius is not positive")
                continue
            try:
                volume = approach_probe_collision_mm3(
                    shape,
                    mouth=mouth,
                    cylinder=cylinder,
                    probe_radius_mm=probe_radius,
                    outside_length_mm=settings["approach_length_mm"],
                    units=units,
                    end_tolerance_mm=tolerance,
                )
                threshold=finite(math.pi*probe_radius**2*tolerance)
                if threshold<=0:
                    raise GeometryUnavailable('probe-volume tolerance underflowed')
                tested.append((volume, cylinder, mouth, threshold))
            except (GeometryUnavailable, ValueError, TypeError) as exc:
                probe_skips.append(f"face {cylinder.face_index}: {exc}")
        if not tested:
            checks.append(
                _skip(
                    "process.cnc_3axis.hole_approach_probe",
                    level=level,
                    reason="no candidate swept probe completed an OCCT intersection" + (f" ({'; '.join(probe_skips[:3])})" if probe_skips else ""),
                    expected="finite swept probe overlap measurements for recognized mouths",
                    assumptions=access_assumption,
                    fix_hint="Configure a positive probe radius and length, and provide a B-rep supported by the pinned OCCT kernel.",
                )
            )
        else:
            volumes = [item[0] for item in tested]
            thresholds=[item[3] for item in tested]
            by_cylinder: dict[int, list[Any]] = {}
            for item in tested:
                by_cylinder.setdefault(item[1].face_index, []).append(item)
            # A through bore needs one usable approach, not both. A larger
            # counterbore remains a separate cylinder and needs its own entry.
            blocked_groups = [items for items in by_cylinder.values() if all(item[0] > item[3] for item in items)]
            obstructed = [min(items, key=lambda item: item[0]) for items in blocked_groups]
            best_volumes = [min(item[0] for item in items) for items in by_cylinder.values()]
            biggest = max(best_volumes)
            partial = bool(probe_skips) or len(mouths) > 128
            violates = bool(obstructed)
            near = not violates and biggest > 0
            status = _finding_status(level, violates, near or partial and not violates)
            actual = (
                f"{len(obstructed)}/{len(by_cylinder)} recognized cylinders lack a clear tested approach ({len(tested)} sweeps); "
                f"largest intersection volume={_fnum(biggest, 6)} mm³; "
                f"per-probe volume tolerances={min(thresholds):g}..{max(thresholds):g} mm³ (actual probe area × length tolerance)"
            )
            if probe_skips:
                actual += f"; {len(probe_skips)} probe(s) skipped"
            if len(mouths) > 128:
                actual += "; 128-mouth finite cap reached"
            obstruction = bool(obstructed)
            checks.append(
                _check(
                    check_id="process.cnc_3axis.hole_approach_probe",
                    level=level,
                    status=status,
                    message=(
                        "The assumed coaxial approach probe intersects solid B-rep geometry."
                        if obstruction
                        else "No overlap was found for the tested coaxial approach probes; other tools and setups remain unknown."
                    ),
                    expected="zero probe overlap beyond the stated volume tolerance",
                    actual=actual,
                    assumptions=access_assumption,
                    fix_hint=(
                        "Move or relieve the obstructing bridge, or provide a different explicit tool envelope and approach direction; inspect the opening in section."
                        if obstruction
                        else "Confirm the real cutter, holder, fixture, and setup; this bounded probe is not a general access certificate."
                    ),
                    feature="unknown" if obstruction else None,
                    selector="unknown" if obstruction else None,
                )
            )

    tool_axis = settings.get("tool_axis")
    tool_diameter = settings.get("tool_diameter_mm")
    if tool_axis is None or tool_diameter is None:
        missing = []
        if tool_axis is None:
            missing.append("tool_axis")
        if tool_diameter is None:
            missing.append("tool_diameter_mm")
        checks.append(
            _skip(
                "process.cnc_3axis.fixed_axis_access",
                level=level,
                reason="missing declared machine setup field(s): " + ", ".join(missing),
                expected="all recognized cavity axes align with the configured fixed 3-axis setup within " + f"±{settings['tool_axis_tolerance_deg']:g}°",
                assumptions=(
                    "This check needs one configured machine/tool axis and a tool diameter. It can only screen recognized inward cylindrical axes and does not cover tool holders, fixtures, alternate setups, or arbitrary cutters. "
                    + context
                ),
                fix_hint="Set process_settings.tool_axis and tool_diameter_mm for the intended setup; otherwise tool access is unknown.",
            )
        )
    elif shape is None:
        checks.append(
            _skip(
                "process.cnc_3axis.fixed_axis_access",
                level=level,
                reason="ctx.shape is missing, so no cavity axes can be measured",
                expected=f"recognized cavity axis deviation ≤ {settings['tool_axis_tolerance_deg']:g}°",
                assumptions="A single fixed 3-axis tool direction is assumed; only recognized cylindrical cavity axes are considered. " + context,
                fix_hint="Provide the built B-rep and the declared tool_axis/tool_diameter_mm.",
            )
        )
    elif cylinder_error:
        checks.append(
            _skip(
                "process.cnc_3axis.fixed_axis_access",
                level=level,
                reason=cylinder_error,
                expected="finite recognized inward cylindrical axes",
                assumptions="A single fixed 3-axis tool direction is assumed; only recognized cylindrical cavity axes are considered. " + context,
                fix_hint="Provide recognized cylindrical B-rep geometry or treat fixed-axis access as unknown.",
            )
        )
    elif not inward:
        checks.append(
            _skip(
                "process.cnc_3axis.fixed_axis_access",
                level=level,
                reason="no inward cylindrical cavity axes were recognized",
                expected="recognized cavity axis deviation within configured tolerance",
                assumptions="A single fixed 3-axis tool direction is assumed; only recognized cylindrical cavity axes are considered. " + context,
                fix_hint="Review access through other geometric feature types manually; this screen does not infer them.",
            )
        )
    else:
        deviations = [
            math.degrees(math.acos(max(-1.0, min(1.0, abs(sum(a * b for a, b in zip(cylinder.axis, tool_axis)))))))
            for cylinder in inward
        ]
        maximum = max(deviations)
        bad = sum(value > settings["tool_axis_tolerance_deg"] for value in deviations)
        status = _finding_status(level, bad > 0, 0 < maximum - settings["tool_axis_tolerance_deg"] <= 0.1)
        checks.append(
            _check(
                check_id="process.cnc_3axis.fixed_axis_access",
                level=level,
                status=status,
                message=(
                    "A recognized cavity axis is outside the configured fixed 3-axis setup."
                    if bad
                    else "Recognized cavity axes align with the configured fixed 3-axis setup."
                ),
                expected=f"axis deviation ≤ {settings['tool_axis_tolerance_deg']:g}° for tool diameter {_fnum(tool_diameter)} mm",
                actual=f"maximum recognized axis deviation={_fnum(maximum)}° across {len(inward)} inward cylinders",
                assumptions=(
                    f"One fixed tool axis {tuple(round(x, 5) for x in tool_axis)} is assumed, with no repositioning; tool diameter is {_fnum(tool_diameter)} mm. "
                    "Only recognized inward cylindrical axes are screened; holders, fixtures, noncylindrical access, and other setups remain unknown. "
                    + context
                ),
                fix_hint="Change the hole axis or configure a different explicit setup; verify tool, holder and fixture clearance.",
            )
        )

    tool_radius = settings.get("tool_radius_mm")
    if tool_radius is None and tool_diameter is not None:
        tool_radius = tool_diameter / 2.0
    radius_limit = settings.get("minimum_internal_radius_mm", tool_radius)
    radius_assumption = (
        "Recognizes partial cylinder faces bounded by two distinct planar side faces as possible internal corner radii; full cylinders and other corner forms are excluded. "
        "The threshold is minimum_internal_radius_mm when supplied, otherwise the configured cutter radius. This does not infer a cutter from ctx.params/metrics or prove complete pocket accessibility. "
        + context
    )
    if radius_limit is None:
        checks.append(
            _skip(
                "process.cnc_3axis.internal_radius",
                level=level,
                reason="no minimum_internal_radius_mm or cutter radius/diameter is configured",
                expected="recognized internal corner radius ≥ explicit configured threshold",
                assumptions=radius_assumption,
                fix_hint="Set minimum_internal_radius_mm or tool_radius_mm/tool_diameter_mm for the intended cutter screen.",
            )
        )
    elif shape is None:
        checks.append(
            _skip(
                "process.cnc_3axis.internal_radius",
                level=level,
                reason="ctx.shape is missing, so no corner radii can be recognized",
                expected=f"recognized internal radius ≥ {_fnum(radius_limit)} mm within ±{tolerance:g} mm",
                assumptions=radius_assumption,
                fix_hint="Provide a B-rep with recognizable cylindrical corner faces and the intended tool threshold.",
            )
        )
    elif cylinder_error:
        checks.append(
            _skip(
                "process.cnc_3axis.internal_radius",
                level=level,
                reason=cylinder_error,
                expected="finite recognized partial cylindrical corner faces",
                assumptions=radius_assumption,
                fix_hint="Provide supported cylindrical B-rep geometry or treat internal radius as unknown.",
            )
        )
    else:
        corners = [
            cylinder
            for cylinder in cylinders
            if cylinder.angular_span < 2.0 * math.pi - 0.15
            and cylinder.angular_span > 0.05
            and cylinder.inward
            and len(cylinder.corner_plane_normals) >= 2
            and abs(sum(a * b for a, b in zip(cylinder.corner_plane_normals[0], cylinder.corner_plane_normals[1]))) < 0.8
        ]
        if not corners:
            checks.append(
                _skip(
                    "process.cnc_3axis.internal_radius",
                    level=level,
                    reason="no partial cylinder bounded by two distinct perpendicular planar side faces was recognized",
                    expected=f"recognized inside-corner radius ≥ {_fnum(radius_limit)} mm within ±{tolerance:g} mm",
                    assumptions=radius_assumption,
                    fix_hint="Review noncylindrical pockets manually or provide a model with explicit inside corner geometry.",
                )
            )
        else:
            minimum = min(cylinder.radius_mm for cylinder in corners)
            violates = minimum < radius_limit - tolerance
            near = abs(minimum - radius_limit) <= tolerance
            status = _finding_status(level, violates, near)
            checks.append(
                _check(
                    check_id="process.cnc_3axis.internal_radius",
                    level=level,
                    status=status,
                    message=(
                        "A recognized internal corner radius is smaller than the configured cutter screen."
                        if violates
                        else "Recognized internal corner radii meet the configured cutter screen."
                    ),
                    expected=f"minimum recognized inside radius ≥ {_fnum(radius_limit)} mm within ±{tolerance:g} mm",
                    actual=f"minimum recognized partial-cylinder radius={_fnum(minimum)} mm across {len(corners)} candidate(s)",
                    assumptions=radius_assumption,
                    fix_hint="Increase the internal radius or choose a smaller declared cutter; confirm the corner and tool geometry in the intended setup.",
                    feature=feature_for_face(ctx, shape, min(corners, key=lambda cylinder: cylinder.radius_mm).face_index),
                )
            )
    return checks


def _sheet_metal_checks(ctx: Any, settings: dict[str, Any]) -> list[Any]:
    process = "sheet_metal"
    level = settings["level"]
    units = getattr(ctx, "units", None)
    try:
        mm_per_unit(units)
    except ValueError as exc:
        return [
            _skip(
                "process.sheet_metal.units",
                level="error",
                reason=str(exc),
                expected="ctx.units is 'mm' or 'in'",
                assumptions=_context_assumptions(process, units),
                fix_hint="Set ctx.units to 'mm' or 'in' before evaluating stock and bend geometry.",
            )
        ]
    tolerance = settings["tolerance_mm"]
    context = _context_assumptions(process, units, tolerance)
    missing = [
        key
        for key in ("stock_thickness_mm", "minimum_inside_bend_radius_mm")
        if key not in settings
    ]
    assumption = (
        "Orientation is arbitrary for the co-axial bend-pair screen. Recognizes overlapping partial cylindrical B-rep faces with matching axes and angular spans; their radius difference is compared with declared stock thickness, and the smaller radius with the declared minimum inside bend radius. "
        "No flat-pattern, material, grain, springback, relief, or forming-tool behavior is inferred. "
        + context
    )
    if missing:
        return [
            _skip(
                "process.sheet_metal.bend_geometry",
                level=level,
                reason="missing declared field(s): " + ", ".join(missing),
                expected="stock_thickness_mm and minimum_inside_bend_radius_mm are explicit",
                assumptions=assumption,
                fix_hint="Set stock_thickness_mm and minimum_inside_bend_radius_mm in spec.process_settings; material-specific bend allowance remains separate.",
            )
        ]
    shape = getattr(ctx, "shape", None)
    if shape is None:
        return [
            _skip(
                "process.sheet_metal.bend_geometry",
                level=level,
                reason="ctx.shape is missing, so no bend cylinder pair is available",
                expected="recognized bend pair matching declared stock thickness and inside radius",
                assumptions=assumption,
                fix_hint="Provide a bent solid B-rep and explicit stock/bend assumptions.",
            )
        ]
    try:
        pairs = bend_surface_pairs(shape, units=units, tolerance_mm=tolerance,max_faces=settings['max_faces'])
    except (GeometryUnavailable, ValueError, TypeError) as exc:
        return [
            _skip(
                "process.sheet_metal.bend_geometry",
                level=level,
                reason=str(exc),
                expected="finite co-axial partial cylinder pairs",
                assumptions=assumption,
                fix_hint="Provide finite B-rep cylinder faces or treat the bend screen as unknown.",
            )
        ]
    if not pairs:
        return [
            _skip(
                "process.sheet_metal.bend_geometry",
                level=level,
                reason="no overlapping co-axial partial cylinder pair was recognized",
                expected="recognized inside/outside bend surfaces with matching axes and angular spans",
                assumptions=assumption,
                fix_hint="Use explicit bend geometry or inspect the sheet-metal bend and flat pattern manually.",
            )
        ]

    stock = settings["stock_thickness_mm"]
    target_radius = settings["minimum_inside_bend_radius_mm"]
    differences = [abs(first.radius_mm - second.radius_mm) for first, second in pairs]
    thickness_deviation = max(abs(value - stock) for value in differences)
    thickness_bad = thickness_deviation > tolerance
    thickness_near = not thickness_bad and thickness_deviation >= tolerance * 0.75
    checks = [
        _check(
            check_id="process.sheet_metal.stock_thickness",
            level=level,
            status=_finding_status(level, thickness_bad, thickness_near),
            message=(
                "Recognized bend-cylinder spacing differs from the declared stock thickness."
                if thickness_bad
                else "Recognized bend-cylinder spacing matches the declared stock thickness."
            ),
            expected=f"outer/inner cylinder radius difference={_fnum(stock)} mm within ±{tolerance:g} mm",
            actual=(
                f"recognized radius differences={[round(value, 3) for value in differences]} mm; "
                f"largest deviation={_fnum(thickness_deviation)} mm across {len(pairs)} pair(s)"
            ),
            assumptions=assumption,
            fix_hint="Correct the bend geometry or declared stock thickness; check a section because this only recognizes paired circular B-rep surfaces.",
            feature=feature_for_face(ctx, shape, pairs[0][0].face_index),
        ),
    ]
    inside_radius = min(min(first.radius_mm, second.radius_mm) for first, second in pairs)
    radius_bad = inside_radius < target_radius - tolerance
    radius_near = abs(inside_radius - target_radius) <= tolerance
    checks.append(
        _check(
            check_id="process.sheet_metal.inside_bend_radius",
            level=level,
            status=_finding_status(level, radius_bad, radius_near),
            message=(
                "A recognized bend radius is below the declared minimum inside radius."
                if radius_bad
                else "Recognized bend radii meet the declared minimum inside radius."
            ),
            expected=f"minimum recognized inside radius ≥ {_fnum(target_radius)} mm within ±{tolerance:g} mm",
            actual=f"smallest radius in recognized pairs={_fnum(inside_radius)} mm across {len(pairs)} pair(s)",
            assumptions=assumption,
            fix_hint="Increase the inside bend radius or revise the declared process/material assumption after checking forming limits.",
            feature=feature_for_face(ctx, shape, min(pairs, key=lambda pair: min(pair[0].radius_mm, pair[1].radius_mm))[0].face_index),
        )
    )
    return checks


def _laser_checks(ctx: Any, settings: dict[str, Any]) -> list[Any]:
    process = "laser"
    level = settings["level"]
    units = getattr(ctx, "units", None)
    try:
        mm_per_unit(units)
    except ValueError as exc:
        return [
            _skip(
                "process.laser.units",
                level="error",
                reason=str(exc),
                expected="ctx.units is 'mm' or 'in'",
                assumptions=_context_assumptions(process, units),
                fix_hint="Set ctx.units to 'mm' or 'in' before evaluating cut features.",
            )
        ]
    tolerance = settings["tolerance_mm"]
    context = _context_assumptions(process, units, tolerance)
    if "kerf_mm" not in settings:
        return [
            _skip(
                "process.laser.minimum_feature",
                level=level,
                reason="kerf_mm is missing, so no cut-width screen can be calculated",
                expected="recognized through-hole diameter ≥ max(2 × kerf_mm, minimum_feature_mm) within tolerance",
                assumptions=(
                    "Orientation is not used; only inward cylindrical cuts with broad planar openings on opposite ends are recognized. "
                    "Slots, open contours, material-dependent edge quality, and the 2D sheet API are not inferred. "
                    + context
                ),
                fix_hint="Set the material/process kerf_mm and optional minimum_feature_mm in spec.process_settings.",
            )
        ]
    threshold = max(2.0 * settings["kerf_mm"], settings.get("minimum_feature_mm", 0.0))
    assumption = (
        f"Orientation is not used. Recognizes inward cylindrical cuts whose two opposite circular edges adjoin planar faces at least 1.5× the circle area; threshold is max(2×kerf_mm, minimum_feature_mm if set), here {threshold:g} mm. "
        "Only through-like circular B-rep features are checked; slots, open contours, and unrecognized 2D geometry are skipped. This is not a kerf-compensation or material certification. "
        + context
    )
    shape = getattr(ctx, "shape", None)
    if shape is None:
        return [
            _skip(
                "process.laser.minimum_feature",
                level=level,
                reason="ctx.shape is missing; no solid B-rep cut features are available",
                expected=f"recognized circular cut diameter ≥ {_fnum(threshold)} mm within ±{tolerance:g} mm",
                assumptions=assumption,
                fix_hint="Provide an extruded solid B-rep or a supported 2D feature evaluator, plus explicit kerf_mm.",
            )
        ]
    try:
        features = through_cylindrical_features(shape, units=units, tolerance_mm=tolerance,max_faces=settings['max_faces'])
    except (GeometryUnavailable, ValueError, TypeError) as exc:
        return [
            _skip(
                "process.laser.minimum_feature",
                level=level,
                reason=str(exc),
                expected="finite recognized through-like cylindrical B-rep cuts",
                assumptions=assumption,
                fix_hint="Provide a supported solid B-rep with recognized through cuts or use a supported 2D process screen.",
            )
        ]
    if not features:
        return [
            _skip(
                "process.laser.minimum_feature",
                level=level,
                reason="no inward cylinder with opposite broad planar openings was recognized",
                expected=f"recognized circular cut diameter ≥ {_fnum(threshold)} mm within ±{tolerance:g} mm",
                assumptions=assumption,
                fix_hint="Review slots and open contours manually; only recognized through-like circular features are screened.",
            )
        ]
    diameters = [2.0 * feature.radius_mm for feature in features]
    minimum = min(diameters)
    bad = minimum < threshold - tolerance
    near = abs(minimum - threshold) <= tolerance
    smallest = min(features, key=lambda item: item.radius_mm)
    return [
        _check(
            check_id="process.laser.minimum_feature",
            level=level,
            status=_finding_status(level, bad, near),
            message=(
                "A recognized circular cut is below the configured kerf/minimum-feature screen."
                if bad
                else "Recognized circular cuts meet the configured kerf/minimum-feature screen."
            ),
            expected=f"minimum recognized through-hole diameter ≥ {_fnum(threshold)} mm within ±{tolerance:g} mm",
            actual=f"minimum recognized diameter={_fnum(minimum)} mm across {len(diameters)} cut(s)",
            assumptions=assumption,
            fix_hint="Increase the cut diameter or slot width, or revise the declared kerf/material assumption after checking the real laser process.",
            feature=feature_for_face(ctx, shape, smallest.face_index),
        )
    ]


def _injection_checks(ctx: Any, settings: dict[str, Any]) -> list[Any]:
    process = "injection"
    level = settings["level"]
    units = getattr(ctx, "units", None)
    try:
        mm_per_unit(units)
    except ValueError as exc:
        return [
            _skip(
                "process.injection.units",
                level="error",
                reason=str(exc),
                expected="ctx.units is 'mm' or 'in'",
                assumptions=_context_assumptions(process, units),
                fix_hint="Set ctx.units to 'mm' or 'in' before evaluating draft surfaces.",
            )
        ]
    tolerance = settings["tolerance_mm"]
    pull = settings["pull_direction"]
    minimum_draft = settings["min_draft_deg"]
    context = _context_assumptions(process, units, tolerance)
    assumption = (
        f"Mold pull orientation is pull_direction={tuple(round(x, 5) for x in pull)} (default +Z); threshold is {minimum_draft:g}° with ±{settings['angle_tolerance_deg']:g}° angular tolerance. "
        f"At most {settings['max_faces']} faces and {settings['max_samples']} classified surface samples are used; only planar side faces (normal component along pull < 0.95) are screened. "
        "Only the absolute local draft angle is measured: curved faces, reverse draft, shutoffs, undercuts, slides, parting line, ejection, shrinkage, and opposite mold halves are not inferred. "
        + context
    )
    shape, missing = _geometry_or_skip(
        ctx,
        "process.injection.draft",
        level,
        assumption,
        f"recognized planar side-face draft ≥ {minimum_draft:g}° within ±{settings['angle_tolerance_deg']:g}°",
        "Add draft relative to the intended pull direction or set process_settings.pull_direction for the real mold orientation.",
    )
    if missing is not None:
        return [missing]
    try:
        samples, truncated = surface_samples(
            shape,
            units=units,
            tolerance_mm=tolerance,
            max_faces=settings["max_faces"],
            max_samples=settings["max_samples"],
        )
    except (GeometryUnavailable, ValueError, TypeError) as exc:
        return [
            _skip(
                "process.injection.draft",
                level=level,
                reason=str(exc),
                expected="finite planar side-face normals and a nonzero pull direction",
                assumptions=assumption,
                fix_hint="Provide a supported B-rep with planar side surfaces and an explicit pull_direction when known.",
            )
        ]
    try:
        from build123d import GeomType
    except Exception as exc:
        return [
            _skip(
                "process.injection.draft",
                level=level,
                reason=f"could not load the pinned geometry type definitions: {exc}",
                expected="finite planar side-face normals",
                assumptions=assumption,
                fix_hint="Install the pinned geometry runtime in the worker and retry.",
            )
        ]
    candidate_drafts: list[tuple[float, int]] = []
    for sample in samples:
        if getattr(sample.face, "geom_type", None) != GeomType.PLANE:
            continue
        pull_component = max(-1.0, min(1.0, sum(a * b for a, b in zip(sample.normal, pull))))
        if abs(pull_component) >= 0.95:
            continue
        draft = math.degrees(math.asin(min(1.0, abs(pull_component))))
        candidate_drafts.append((finite(draft), sample.face_index))
    if not candidate_drafts:
        return [
            _skip(
                "process.injection.draft",
                level=level,
                reason="no planar side face was recognized under the assumed pull direction",
                expected=f"planar side-face draft ≥ {minimum_draft:g}° within ±{settings['angle_tolerance_deg']:g}°",
                assumptions=assumption,
                fix_hint="Use a model with recognized planar side faces or inspect curved/undercut regions manually.",
            )
        ]
    minimum, face_index = min(candidate_drafts, key=lambda item: item[0])
    bad = minimum < minimum_draft - settings["angle_tolerance_deg"]
    near = abs(minimum - minimum_draft) <= settings["angle_tolerance_deg"]
    status = _finding_status(level, bad, near or truncated and not bad)
    actual = f"minimum sampled planar side draft={_fnum(minimum)}° across {len(candidate_drafts)} samples"
    if truncated:
        actual += "; finite face/sample cap reached"
    return [
        _check(
            check_id="process.injection.draft",
            level=level,
            status=status,
            message=(
                "A recognized planar side face is below the configured draft screen."
                if bad
                else "Recognized planar side faces meet the configured draft screen."
            ),
            expected=f"minimum sampled side draft ≥ {minimum_draft:g}° within ±{settings['angle_tolerance_deg']:g}°",
            actual=actual,
            assumptions=assumption,
            fix_hint="Add draft to the measured side face or revise the assumed pull direction after checking parting and ejection strategy.",
            feature=feature_for_face(ctx, shape, face_index),
        )
    ]


def _coverage_checked(ctx: Any, process: str, settings: dict[str, Any], checks: list[Any]) -> list[Any]:
    """Prevent a partial feature/surface sample from certifying omitted geometry."""
    from ._checks import record
    from .process_geometry import rectangular_box_thickness_mm
    from . import spec_geometry
    from build123d import GeomType

    shape = ctx.shape
    faces = list(shape.faces())
    all_planar = all(face.geom_type == GeomType.PLANE for face in faces)
    box_thickness = rectangular_box_thickness_mm(shape, ctx.units)
    result = []
    for check in checks:
        data = record(check)
        reason = None
        if data['status'] == 'pass':
            check_id = data['id']
            if check_id.endswith('.wall_thickness') and box_thickness is None:
                reason = 'sampled rays do not bound the global minimum wall thickness'
            elif check_id.endswith('.overhang') and not all_planar:
                reason = 'curved surfaces have unsampled normals; global overhang coverage is unproven'
            elif check_id.endswith('.internal_radius'):
                reason = 'recognized rounded corners do not rule out sharp or unclassified internal corners'
            elif check_id.startswith('process.sheet_metal.') and len(faces) != 6:
                reason = 'recognized bend pairs do not rule out additional sharp or unclassified bends'
            elif check_id == 'process.laser.minimum_feature':
                try:
                    spec_geometry.require_round_planar_openings(shape)
                    spec_geometry.inward_full_cylinders(shape, mm_per_unit(ctx.units))
                except spec_geometry.UnsupportedGeometry as exc:
                    reason = f'noncircular or unclassified cuts are not screened: {exc}'
            elif check_id == 'process.injection.draft':
                reason = 'absolute local draft does not establish signed release or exclude undercuts'
        if box_thickness is not None and data['id'].endswith('.wall_thickness') and data['status'] == 'warn':
            threshold = settings.get('min_wall_mm')
            if threshold is None and 'nozzle_diameter_mm' in settings:
                threshold = settings['nozzle_diameter_mm'] * settings.get('wall_line_count', 2)
            if threshold is not None and box_thickness >= threshold - 1e-6:
                data.update(status='pass', message='The recognized rectangular solid meets the inclusive wall threshold.',
                    actual=f'exact rectangular-solid minimum thickness={box_thickness:.8g} mm')
        if box_thickness is not None and data['status'] == 'skip':
            if process == 'cnc_3axis' and data['id'] in {
                'process.cnc_3axis.hole_approach_probe',
                'process.cnc_3axis.fixed_axis_access',
                'process.cnc_3axis.internal_radius',
            }:
                data.update(status='pass', message='A native rectangular solid has no holes or internal corners to screen.',
                    actual='0 internal bores and 0 internal corners on the recognized six-face rectangular solid')
        if reason:
            data['status'] = 'skip'
            data['message'] = 'Process screen has partial evidence: ' + reason + '.'
            data['actual'] += '; full coverage unproven: ' + reason
            data['fix_hint'] = 'Review the omitted geometry or use a validated complete evaluator; retain draft status at error severity.'
        result.append(make_check(**data))
    return result


def process_checks(ctx: Any) -> list[Any]:
    """Evaluate the requested process with finite, explicitly assumed B-rep screens.

    Missing process is intentionally optional. Unknown non-empty process names
    and malformed configured assumptions produce error-level skips so the
    verification guard cannot treat them as successful evaluation.
    """
    process = getattr(ctx, "process", None)
    spec = getattr(ctx, 'spec', None)
    requested = spec.get('process') if isinstance(spec, Mapping) else None
    if requested is not None and process not in (None, '', requested):
        return [_check(check_id='process.context_mismatch',level='error',status='fail',
            message='Context process disagrees with the request specification.',
            expected=repr(requested),actual=repr(process),
            assumptions='No manufacturing screen ran: the requested process and model context conflict.',
            fix_hint='Reconcile the project/model process with spec.process, then rebuild and rerun checks.')]
    if process in (None, '') and requested is not None:
        process = requested
    if process in (None,'') and isinstance(spec,Mapping) and spec.get('process_settings'):
        return [_skip('process.settings_without_process',level='error',
            reason='process_settings were supplied without a requested process',expected='a supported process for the supplied assumptions',
            assumptions='No process or setup could be selected; the settings were not evaluated.',
            fix_hint='Declare spec.process or the project process before supplying process_settings.')]
    if process is None or process == "":
        return []
    if not isinstance(process, str) or process not in PROCESS_NAMES:
        actual = repr(process)
        return [
            _skip(
                "process.unknown",
                level="error",
                reason=f"requested process {actual} is not recognized",
                expected="one of " + ", ".join(PROCESS_NAMES),
                assumptions=(
                    "No process geometry was evaluated because the requested process has no implemented profile. "
                    "Orientation, thresholds, equipment, and manufacturing behavior are unknown."
                ),
                fix_hint="Set ctx.process to a supported process profile or leave it unset when no process check is requested.",
            )
        ]

    parsed = process_settings(ctx, process)
    if parsed.error is not None or parsed.values is None:
        return [
            _skip(
                "process.settings_invalid",
                level="error",
                reason=parsed.error or "process settings could not be read",
                expected=f"spec.process_settings is a valid mapping for {process}",
                assumptions=(
                    f"No geometric screen ran for {process!r}; orientation, thresholds, finite sampling, and equipment assumptions are unknown until the settings are corrected."
                ),
                fix_hint="Correct the named process_settings field using packages/quality/PROCESS.md, then rerun process checks.",
            )
        ]
    settings = parsed.values
    try:
        mm_per_unit(getattr(ctx, "units", None))
    except ValueError as exc:
        return [
            _skip(
                f"process.{process}.units",
                level="error",
                reason=str(exc),
                expected="ctx.units is 'mm' or 'in'",
                assumptions=_context_assumptions(process, getattr(ctx, "units", None)),
                fix_hint="Set ctx.units to 'mm' or 'in' and rerun process checks.",
            )
        ]

    if getattr(ctx,'shape',None) is not None:
        try:
            from build123d import Shape
            # Model code can override Python wrapper methods/attributes. Fresh
            # wrappers make the following screens inspect the actual B-rep.
            clean=Shape.cast(ctx.shape.wrapped)
            ctx=SimpleNamespace(shape=clean,units=ctx.units,spec=ctx.spec,features=getattr(ctx,'features',[]))
        except Exception as exc:
            return [_skip(f'process.{process}.geometry',level='error',
                reason=f'could not normalize native B-rep geometry: {exc}',expected='a supported native B-rep',
                assumptions=_context_assumptions(process,getattr(ctx,'units',None)),
                fix_hint='Provide a valid native B-rep and rerun the process screen.')]

    try:
        if process in ("fdm_pla", "sla"):
            return _coverage_checked(ctx, process, settings, _additive_checks(ctx, process, settings))
        if process == "cnc_3axis":
            return _coverage_checked(ctx, process, settings, _cnc_checks(ctx, settings))
        if process == "sheet_metal":
            return _coverage_checked(ctx, process, settings, _sheet_metal_checks(ctx, settings))
        if process == "laser":
            return _coverage_checked(ctx, process, settings, _laser_checks(ctx, settings))
        if process == "injection":
            return _coverage_checked(ctx, process, settings, _injection_checks(ctx, settings))
    except Exception as exc:
        # Evaluator exceptions are explicit skips; they are never interpreted
        # as manufacturing evidence or a pass.
        return [
            _skip(
                f"process.{process}.evaluation",
                level="error",
                reason=f"unexpected process evaluator error: {type(exc).__name__}: {exc}",
                expected="finite process evidence with declared assumptions",
                assumptions=_context_assumptions(process, getattr(ctx, "units", None), settings["tolerance_mm"]),
                fix_hint="Inspect the B-rep and the stated assumptions; treat this process result as unknown until the evaluator completes.",
            )
        ]
    return []
