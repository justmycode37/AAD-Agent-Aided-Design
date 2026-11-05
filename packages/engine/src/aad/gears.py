"""Involute spur and helical gear geometry for build123d.

Angles are in degrees; lengths are in the model's length unit (normally mm).
The profile is an involute flank sampled into interpolating B-splines. A
radial root connector and circular root arc close the tooth; trochoidal root
fillets and strength, contact-ratio, noise, and life calculations are outside
this small geometry library.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from math import acos, atan, cos, degrees, isfinite, pi, radians, sin, sqrt, tan
from typing import Any, Literal
import warnings


Hand = Literal["right", "left"]
UndercutPolicy = Literal["warn", "error", "ignore"]


@dataclass(frozen=True, slots=True)
class _GearDimensions:
    teeth: int
    module: float
    transverse_module: float
    pressure_angle: float
    transverse_pressure_angle: float
    helix_angle: float
    pitch_radius: float
    base_radius: float
    tip_radius: float
    root_radius: float
    pitch_tooth_thickness: float
    pitch_half_angle: float
    involute_at_pitch: float
    undercut: bool
    virtual_teeth: float
    minimum_teeth: float


def _build123d() -> Any:
    """Load the CAD kernel only when a gear shape is requested."""
    return import_module("build123d")


def _number(name: str, value: float, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number; change it to a numeric value.")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite; change it to a finite number.")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}; increase it.")
    return result


def _tooth_count(name: str, teeth: int) -> int:
    if isinstance(teeth, bool) or not isinstance(teeth, int) or teeth < 3:
        raise ValueError(f"{name} must be an integer of at least 3; increase the tooth count.")
    return teeth


def _hand_sign(hand: Hand | str) -> int:
    if hand == "right":
        return 1
    if hand == "left":
        return -1
    raise ValueError("hand must be 'right' or 'left'; choose the required helix hand.")


def _gear_dimensions(
    teeth: int,
    module: float,
    *,
    pressure_angle: float,
    helix_angle: float,
    addendum_coefficient: float,
    dedendum_coefficient: float,
    profile_shift: float,
    backlash: float,
    internal: bool,
) -> _GearDimensions:
    teeth = _tooth_count("teeth", teeth)
    module = _number("module", module, minimum=0.0)
    if module == 0:
        raise ValueError("module must be greater than zero; increase the module.")
    pressure_deg = _number("pressure_angle", pressure_angle, minimum=0.0)
    if not 0.0 < pressure_deg < 89.0:
        raise ValueError("pressure_angle must be between 0 and 89 degrees; use a standard positive pressure angle.")
    helix_deg = _number("helix_angle", helix_angle, minimum=0.0)
    if helix_deg >= 89.0:
        raise ValueError("helix_angle must be less than 89 degrees; reduce the helix angle.")
    if not isinstance(internal, bool):
        raise ValueError("internal must be True or False; choose the gear type explicitly.")
    addendum = _number("addendum_coefficient", addendum_coefficient, minimum=0.0)
    dedendum = _number("dedendum_coefficient", dedendum_coefficient, minimum=0.0)
    if addendum == 0.0 or dedendum == 0.0:
        raise ValueError("addendum_coefficient and dedendum_coefficient must be greater than zero; use positive tooth depths.")
    shift = _number("profile_shift", profile_shift)
    backlash = _number("backlash", backlash, minimum=0.0)

    alpha_n = radians(pressure_deg)
    beta = radians(helix_deg)
    transverse_module = module / cos(beta)
    alpha_t = atan(tan(alpha_n) / cos(beta))
    pitch_radius = teeth * transverse_module / 2.0
    base_radius = pitch_radius * cos(alpha_t)
    if internal:
        tip_radius = pitch_radius - module * (addendum - shift)
        root_radius = pitch_radius + module * (dedendum + shift)
        shifted_thickness = -2.0 * shift * module * tan(alpha_n) / cos(beta)
    else:
        tip_radius = pitch_radius + module * (addendum + shift)
        root_radius = pitch_radius - module * (dedendum - shift)
        shifted_thickness = 2.0 * shift * module * tan(alpha_n) / cos(beta)
    pitch_tooth_thickness = pi * transverse_module / 2.0 + shifted_thickness - backlash
    if pitch_tooth_thickness <= 0.0:
        raise ValueError(
            "backlash and profile shift leave no tooth thickness at the pitch circle; reduce backlash/profile shift or change the tooth count."
        )
    if tip_radius <= 0.0 or root_radius <= 0.0:
        raise ValueError(
            "the addendum or dedendum makes a non-positive tooth radius; change the tooth count, module, profile shift, or tooth-depth coefficients."
        )
    if internal:
        if not tip_radius < pitch_radius < root_radius or root_radius <= base_radius:
            raise ValueError(
                "internal gear radii do not form an involute tooth; reduce profile_shift/addendum or increase dedendum so tip < pitch < root and root > base."
            )
    elif not root_radius < pitch_radius < tip_radius or tip_radius <= base_radius:
        raise ValueError(
            "external gear radii do not form an involute tooth; adjust profile_shift or addendum/dedendum so root < pitch < tip and tip > base."
        )

    involute_at_pitch = tan(alpha_t) - alpha_t
    pitch_half_angle = pitch_tooth_thickness / (2.0 * pitch_radius)
    tooth_pitch = 2.0 * pi / teeth

    def half_angle(radius: float) -> float:
        involute = 0.0
        if radius > base_radius:
            alpha = acos(min(1.0, base_radius / radius))
            involute = tan(alpha) - alpha
        if internal:
            return pitch_half_angle + involute - involute_at_pitch
        return pitch_half_angle + involute_at_pitch - involute

    root_half = half_angle(root_radius)
    tip_half = half_angle(max(tip_radius, base_radius))
    if 2.0 * root_half >= tooth_pitch or 2.0 * tip_half >= tooth_pitch:
        raise ValueError(
            "adjacent teeth overlap at the root or tip; reduce profile_shift/addendum, increase backlash, or use more teeth."
        )

    virtual_teeth = teeth / cos(beta) ** 3
    minimum_teeth = 2.0 * max(0.0, addendum - shift) / sin(alpha_n) ** 2
    undercut = not internal and virtual_teeth + 1e-10 < minimum_teeth
    return _GearDimensions(
        teeth=teeth,
        module=module,
        transverse_module=transverse_module,
        pressure_angle=alpha_n,
        transverse_pressure_angle=alpha_t,
        helix_angle=beta,
        pitch_radius=pitch_radius,
        base_radius=base_radius,
        tip_radius=tip_radius,
        root_radius=root_radius,
        pitch_tooth_thickness=pitch_tooth_thickness,
        pitch_half_angle=pitch_half_angle,
        involute_at_pitch=involute_at_pitch,
        undercut=undercut,
        virtual_teeth=virtual_teeth,
        minimum_teeth=minimum_teeth,
    )


def _check_undercut(dimensions: _GearDimensions, policy: UndercutPolicy | str) -> None:
    if policy not in ("warn", "error", "ignore"):
        raise ValueError("undercut must be 'warn', 'error', or 'ignore'; choose how to handle predicted undercut.")
    if not dimensions.undercut or policy == "ignore":
        return
    message = (
        f"predicted rack undercut: virtual tooth count {dimensions.virtual_teeth:.3f} is below "
        f"the no-undercut limit {dimensions.minimum_teeth:.3f}; increase teeth/profile_shift, "
        "reduce addendum_coefficient, or choose undercut='ignore' only if the simplified root is acceptable."
    )
    if policy == "error":
        raise ValueError(message)
    warnings.warn(message, UserWarning, stacklevel=3)


def _involute_half_angle(
    dimensions: _GearDimensions,
    radius: float,
    pitch_half: float,
    *,
    internal: bool = False,
) -> float:
    involute = 0.0
    if radius > dimensions.base_radius:
        alpha = acos(min(1.0, dimensions.base_radius / radius))
        involute = tan(alpha) - alpha
    if internal:
        return pitch_half + involute - dimensions.involute_at_pitch
    return pitch_half + dimensions.involute_at_pitch - involute


def _sample_count(dimensions: _GearDimensions, r_start: float, r_end: float, tolerance: float) -> int:
    t_start = sqrt(max(0.0, (r_start / dimensions.base_radius) ** 2 - 1.0))
    t_end = sqrt(max(0.0, (r_end / dimensions.base_radius) ** 2 - 1.0))
    span = t_end - t_start
    if span <= 1e-12:
        return 16
    # The involute curvature radius is rb*t. This conservative chord-error
    # bound is halved because the edge is a cubic interpolant, not a polyline.
    bound = max(dimensions.base_radius * max(t_end, 1e-6), 1e-12)
    segments = int(span * sqrt(bound / max(4.0 * tolerance, 1e-12))) + 1
    return max(32, min(512, segments))


def _point(radius: float, angle: float, z: float = 0.0) -> Any:
    bd = _build123d()
    return bd.Vector(radius * cos(angle), radius * sin(angle), z)


def _involute_edge(
    dimensions: _GearDimensions,
    center_angle: float,
    side: int,
    r_start: float,
    r_end: float,
    pitch_half: float,
    sample_count: int,
    z: float = 0.0,
    *,
    internal: bool = False,
) -> Any:
    bd = _build123d()
    if r_end <= r_start:
        raise ValueError("involute flank has no radial span; change the tooth-depth coefficients or profile shift.")
    points = []
    for index in range(sample_count + 1):
        # Uniform radius avoids tiny parameter intervals at the base circle,
        # where the involute's natural t parameter has zero speed. The kernel
        # then chooses a stable chord-length spline parameterization.
        radius = r_start + (r_end - r_start) * index / sample_count
        t = sqrt(max(0.0, (radius / dimensions.base_radius) ** 2 - 1.0))
        involute = t - atan(t)
        half = (
            pitch_half + involute - dimensions.involute_at_pitch
            if internal
            else pitch_half + dimensions.involute_at_pitch - involute
        )
        angle = center_angle + side * half
        points.append(_point(radius, angle, z))
    return bd.Edge.make_spline(points, tol=1e-9)


def _external_wire(
    dimensions: _GearDimensions,
    *,
    z: float,
    profile_tolerance: float,
    sample_count: int | None = None,
) -> Any:
    bd = _build123d()
    root = dimensions.root_radius
    base = dimensions.base_radius
    tip = dimensions.tip_radius
    flank_start = max(root, base)
    sample_count = sample_count or _sample_count(dimensions, flank_start, tip, profile_tolerance)
    root_half = _involute_half_angle(dimensions, flank_start, dimensions.pitch_half_angle)
    tip_half = _involute_half_angle(dimensions, tip, dimensions.pitch_half_angle)
    pitch = 2.0 * pi / dimensions.teeth
    edges = []
    for tooth in range(dimensions.teeth):
        center = tooth * pitch
        lower_root = _point(root, center - root_half, z)
        lower_start = _point(flank_start, center - root_half, z)
        if root < base:
            edges.append(bd.Edge.make_line(lower_root, lower_start))
        lower = _involute_edge(
            dimensions, center, -1, flank_start, tip,
            dimensions.pitch_half_angle, sample_count, z,
        )
        edges.append(lower)
        lower_tip = _point(tip, center - tip_half, z)
        upper_tip = _point(tip, center + tip_half, z)
        edges.append(bd.Edge.make_three_point_arc(lower_tip, _point(tip, center, z), upper_tip))
        upper = _involute_edge(
            dimensions, center, 1, flank_start, tip,
            dimensions.pitch_half_angle, sample_count, z,
        )
        edges.append(upper.reversed())
        upper_start = _point(flank_start, center + root_half, z)
        upper_root = _point(root, center + root_half, z)
        if root < base:
            edges.append(bd.Edge.make_line(upper_start, upper_root))
        next_lower = center + pitch - root_half
        edges.append(
            bd.Edge.make_three_point_arc(
                upper_root,
                _point(root, (center + root_half + next_lower) / 2.0, z),
                _point(root, next_lower, z),
            )
        )
    return bd.Wire(edges)


def _internal_wire(
    dimensions: _GearDimensions,
    *,
    z: float,
    profile_tolerance: float,
    sample_count: int | None = None,
) -> Any:
    bd = _build123d()
    root = dimensions.root_radius
    base = dimensions.base_radius
    tip = dimensions.tip_radius
    flank_start = max(tip, base)
    sample_count = sample_count or _sample_count(dimensions, flank_start, root, profile_tolerance)
    root_half = _involute_half_angle(
        dimensions, root, dimensions.pitch_half_angle, internal=True
    )
    start_half = _involute_half_angle(
        dimensions, flank_start, dimensions.pitch_half_angle, internal=True
    )
    tip_half = _involute_half_angle(
        dimensions, tip, dimensions.pitch_half_angle, internal=True
    )
    pitch = 2.0 * pi / dimensions.teeth
    edges = []
    for tooth in range(dimensions.teeth):
        center = tooth * pitch
        previous_upper = _point(root, center - pitch + root_half, z)
        lower_root = _point(root, center - root_half, z)
        edges.append(
            bd.Edge.make_three_point_arc(
                previous_upper,
                _point(root, center - pitch / 2.0, z),
                lower_root,
            )
        )

        lower = _involute_edge(
            dimensions, center, -1, flank_start, root,
            dimensions.pitch_half_angle, sample_count, z, internal=True,
        )
        if tip < base:
            edges.append(lower.reversed())
            lower_base = _point(base, center - start_half, z)
            lower_tip = _point(tip, center - tip_half, z)
            edges.append(bd.Edge.make_line(lower_base, lower_tip))
        else:
            edges.append(lower.reversed())
            lower_tip = _point(tip, center - tip_half, z)

        upper_tip = _point(tip, center + tip_half, z)
        edges.append(bd.Edge.make_three_point_arc(lower_tip, _point(tip, center, z), upper_tip))
        if tip < base:
            upper_base = _point(base, center + start_half, z)
            edges.append(bd.Edge.make_line(upper_tip, upper_base))
            upper = _involute_edge(
                dimensions, center, 1, flank_start, root,
                dimensions.pitch_half_angle, sample_count, z, internal=True,
            )
            edges.append(upper)
        else:
            upper = _involute_edge(
                dimensions, center, 1, tip, root,
                dimensions.pitch_half_angle, sample_count, z, internal=True,
            )
            edges.append(upper)
    return bd.Wire(edges)


def _profile_from_dimensions(
    dimensions: _GearDimensions,
    *,
    profile_tolerance: float,
    bore: float,
    internal: bool,
    rim_thickness: float | None,
    sample_count: int | None = None,
) -> Any:
    bd = _build123d()
    profile_tolerance = _number("profile_tolerance", profile_tolerance, minimum=0.0)
    if profile_tolerance == 0:
        raise ValueError("profile_tolerance must be greater than zero; use a positive sub-0.01 model-unit value.")
    bore = _number("bore", bore, minimum=0.0)
    if internal:
        if bore != 0.0:
            raise ValueError("bore applies to external gears; set bore=0 for an internal gear because its tooth profile already forms the opening.")
        rim = 2.0 * dimensions.module if rim_thickness is None else _number("rim_thickness", rim_thickness, minimum=0.0)
        if rim == 0:
            raise ValueError("rim_thickness must be greater than zero for an internal gear; increase the ring wall.")
        inner = _internal_wire(
            dimensions, z=0.0, profile_tolerance=profile_tolerance,
            sample_count=sample_count,
        )
        outer = bd.Wire.make_circle(dimensions.root_radius + rim)
        return bd.Face(outer, [inner])

    if rim_thickness is not None:
        raise ValueError("rim_thickness is only used for internal gears; omit it for an external gear.")
    if bore / 2.0 >= dimensions.root_radius:
        raise ValueError("bore must be smaller than the tooth root diameter; reduce the bore or increase the root radius.")
    outer = _external_wire(
        dimensions, z=0.0, profile_tolerance=profile_tolerance,
        sample_count=sample_count,
    )
    inner_wires = [bd.Wire.make_circle(bore / 2.0)] if bore > 0.0 else None
    return bd.Face(outer, inner_wires)


def gear_profile(
    teeth: int,
    module: float,
    *,
    pressure_angle: float = 20.0,
    helix_angle: float = 0.0,
    hand: Hand | str = "right",
    addendum_coefficient: float = 1.0,
    dedendum_coefficient: float = 1.25,
    profile_shift: float = 0.0,
    backlash: float = 0.0,
    bore: float = 0.0,
    internal: bool = False,
    rim_thickness: float | None = None,
    profile_tolerance: float = 0.005,
    undercut: UndercutPolicy | str = "warn",
) -> Any:
    """Return a planar involute-gear section as a build123d ``Face``.

    ``module`` and ``profile_shift`` use normal-module units. ``backlash`` is
    the circular tooth-thickness reduction in model units per gear; two
    equally reduced mating gears have approximately twice that circular
    backlash. ``bore`` is an external gear's bore diameter. For an internal
    gear, ``rim_thickness`` sets the material outside its tooth roots.
    """
    _hand_sign(hand)
    dimensions = _gear_dimensions(
        teeth, module,
        pressure_angle=pressure_angle,
        helix_angle=helix_angle,
        addendum_coefficient=addendum_coefficient,
        dedendum_coefficient=dedendum_coefficient,
        profile_shift=profile_shift,
        backlash=backlash,
        internal=internal,
    )
    _check_undercut(dimensions, undercut)
    return _profile_from_dimensions(
        dimensions,
        profile_tolerance=profile_tolerance,
        bore=bore,
        internal=internal,
        rim_thickness=rim_thickness,
    )


def gear_solid(
    teeth: int,
    module: float,
    width: float,
    *,
    pressure_angle: float = 20.0,
    helix_angle: float = 0.0,
    hand: Hand | str = "right",
    addendum_coefficient: float = 1.0,
    dedendum_coefficient: float = 1.25,
    profile_shift: float = 0.0,
    backlash: float = 0.0,
    bore: float = 0.0,
    internal: bool = False,
    rim_thickness: float | None = None,
    profile_tolerance: float = 0.005,
    undercut: UndercutPolicy | str = "warn",
    phase: float = 0.0,
    x: float = 0.0,
    y: float = 0.0,
    z0: float = 0.0,
) -> Any:
    """Build a straight or helical gear solid with its axis along +Z.

    Helix angle, hand, and phase are degrees. ``width``, ``bore``, and
    placement coordinates use model units. Zero helix uses a plain linear
    extrusion; nonzero helix twists by ``width*tan(beta)/pitch_radius``.
    """
    width = _number("width", width, minimum=0.0)
    if width == 0.0:
        raise ValueError("width must be greater than zero; increase the face width.")
    phase = _number("phase", phase)
    x = _number("x", x)
    y = _number("y", y)
    z0 = _number("z0", z0)
    hand_sign = _hand_sign(hand)
    dimensions = _gear_dimensions(
        teeth, module,
        pressure_angle=pressure_angle,
        helix_angle=helix_angle,
        addendum_coefficient=addendum_coefficient,
        dedendum_coefficient=dedendum_coefficient,
        profile_shift=profile_shift,
        backlash=backlash,
        internal=internal,
    )
    _check_undercut(dimensions, undercut)
    face = _profile_from_dimensions(
        dimensions,
        profile_tolerance=profile_tolerance,
        bore=bore,
        internal=internal,
        rim_thickness=rim_thickness,
    )
    bd = _build123d()
    if dimensions.helix_angle == 0.0:
        solid = bd.Solid.extrude(face, bd.Vector(0, 0, width))
    else:
        twist_deg = degrees(
            hand_sign * width * tan(dimensions.helix_angle) / dimensions.pitch_radius
        )
        solid = bd.Solid.extrude_linear_with_rotation(
            face,
            bd.Vector(0, 0, 0),
            bd.Vector(0, 0, width),
            twist_deg,
        )
    return bd.Pos(x, y, z0) * bd.Rot(0, 0, phase) * solid


def _mesh_counts(teeth1: int, teeth2: int, internal: bool) -> tuple[int, int]:
    z1 = _tooth_count("teeth1", teeth1)
    z2 = _tooth_count("teeth2", teeth2)
    if not isinstance(internal, bool):
        raise ValueError("internal must be True or False; choose the mesh type explicitly.")
    if internal and z2 <= z1:
        raise ValueError("an internal gear pair needs teeth2 > teeth1; assign the ring gear as gear 2 or use an external pair.")
    return z1, z2


def gear_center_distance(
    module: float,
    teeth1: int,
    teeth2: int,
    helix_angle: float = 0.0,
    *,
    internal: bool = False,
) -> float:
    """Return nominal pitch-cylinder center distance for an external or internal pair.

    The module is normal module and helix angle is in degrees. Profile shift,
    center-distance correction, and manufacturing tolerances are not included.
    With ``internal=True``, ``teeth2`` is the ring gear tooth count.
    """
    module = _number("module", module, minimum=0.0)
    if module == 0.0:
        raise ValueError("module must be greater than zero; increase the module.")
    z1, z2 = _mesh_counts(teeth1, teeth2, internal)
    beta_deg = _number("helix_angle", helix_angle, minimum=0.0)
    if beta_deg >= 89.0:
        raise ValueError("helix_angle must be less than 89 degrees; reduce the helix angle.")
    tooth_sum = z2 - z1 if internal else z1 + z2
    return module * tooth_sum / (2.0 * cos(radians(beta_deg)))


def helix_angle_for_center_distance(
    module: float,
    teeth1: int,
    teeth2: int,
    center_distance: float,
    *,
    internal: bool = False,
) -> float:
    """Return the helix-angle magnitude in degrees for a required nominal distance."""
    module = _number("module", module, minimum=0.0)
    if module == 0.0:
        raise ValueError("module must be greater than zero; increase the module.")
    z1, z2 = _mesh_counts(teeth1, teeth2, internal)
    center_distance = _number("center_distance", center_distance, minimum=0.0)
    if center_distance == 0.0:
        raise ValueError("center_distance must be greater than zero; increase the shaft spacing.")
    tooth_sum = z2 - z1 if internal else z1 + z2
    ratio = module * tooth_sum / (2.0 * center_distance)
    if ratio > 1.0 + 1e-12:
        minimum_distance = module * tooth_sum / 2.0
        raise ValueError(
            f"center_distance {center_distance:g} is below the spur pitch distance {minimum_distance:g}; increase shaft spacing."
        )
    if ratio <= 0.0:
        raise ValueError("the tooth counts and center distance do not define a helix angle; change those inputs.")
    return degrees(acos(min(1.0, ratio)))


def gear_mesh_phase(
    teeth1: int,
    teeth2: int,
    centerline_angle: float = 0.0,
    phase1: float = 0.0,
    *,
    internal: bool = False,
) -> float:
    """Return gear 2's tooth-grid phase in degrees for the stated centerline.

    For an external pair the gears rotate in opposite directions. An internal
    pair uses ``internal=True`` and rotates in the same direction. The result
    aligns the nominal tooth grids; requested backlash is supplied by the
    tooth-thickness reduction on the gear profiles.
    """
    z1, z2 = _mesh_counts(teeth1, teeth2, internal)
    phi = radians(_number("centerline_angle", centerline_angle))
    first_phase = radians(_number("phase1", phase1))
    ratio = z1 / z2
    if internal:
        contact_angle = phi + pi
        phase2 = contact_angle + pi / z2 + ratio * (first_phase - contact_angle)
    else:
        phase2 = phi + pi + pi / z2 - ratio * (first_phase - phi)
    return degrees(phase2) % 360.0


def tapered_bevel_standin(
    teeth: int,
    module: float,
    width: float,
    *,
    tip_scale: float = 0.75,
    pressure_angle: float = 20.0,
    addendum_coefficient: float = 1.0,
    dedendum_coefficient: float = 1.25,
    profile_shift: float = 0.0,
    backlash: float = 0.0,
    bore: float = 0.0,
    profile_tolerance: float = 0.005,
    undercut: UndercutPolicy | str = "warn",
    phase: float = 0.0,
    x: float = 0.0,
    y: float = 0.0,
    z0: float = 0.0,
) -> Any:
    """Create a straight tapered-tooth visual stand-in, not a true bevel gear.

    Each section is generated from its own module. This creates a closed loft
    without scaling a finished wire. The flanks remain tapered extruded spur
    sections; no bevel, beveloid, spiral-bevel, or hypoid flank law is modeled.
    """
    width = _number("width", width, minimum=0.0)
    if width == 0.0:
        raise ValueError("width must be greater than zero; increase the face width.")
    tip_scale = _number("tip_scale", tip_scale, minimum=0.0)
    if not 0.0 < tip_scale < 1.0:
        raise ValueError("tip_scale must be greater than zero and less than one; choose a smaller tip section.")
    phase = _number("phase", phase)
    x = _number("x", x)
    y = _number("y", y)
    z0 = _number("z0", z0)
    module = _number("module", module, minimum=0.0)
    if module == 0.0:
        raise ValueError("module must be greater than zero; increase the module.")
    base = _gear_dimensions(
        teeth, module,
        pressure_angle=pressure_angle,
        helix_angle=0.0,
        addendum_coefficient=addendum_coefficient,
        dedendum_coefficient=dedendum_coefficient,
        profile_shift=profile_shift,
        backlash=backlash,
        internal=False,
    )
    top = _gear_dimensions(
        teeth, module * tip_scale,
        pressure_angle=pressure_angle,
        helix_angle=0.0,
        addendum_coefficient=addendum_coefficient,
        dedendum_coefficient=dedendum_coefficient,
        profile_shift=profile_shift,
        backlash=backlash * tip_scale,
        internal=False,
    )
    _check_undercut(base, undercut)
    _check_undercut(top, undercut)
    tolerance = _number("profile_tolerance", profile_tolerance, minimum=0.0)
    if tolerance == 0.0:
        raise ValueError("profile_tolerance must be greater than zero; use a positive sub-0.01 model-unit value.")
    bore = _number("bore", bore, minimum=0.0)
    if bore / 2.0 >= top.root_radius:
        raise ValueError("bore must be smaller than the narrow section's root diameter; reduce the bore or increase tip_scale.")

    common_samples = max(
        _sample_count(base, max(base.root_radius, base.base_radius), base.tip_radius, tolerance),
        _sample_count(top, max(top.root_radius, top.base_radius), top.tip_radius, tolerance),
    )
    low_wire = _external_wire(base, z=0.0, profile_tolerance=tolerance, sample_count=common_samples)
    high_wire = _external_wire(top, z=0.0, profile_tolerance=tolerance, sample_count=common_samples)
    bd = _build123d()
    loft = bd.Solid.make_loft([low_wire, bd.Pos(0, 0, width) * high_wire], ruled=True)
    if bore > 0.0:
        cutter = bd.Solid.make_cylinder(
            bore / 2.0,
            width + 2.0,
            bd.Plane(origin=(0, 0, -1.0)),
        )
        loft = loft - cutter
    return bd.Pos(x, y, z0) * bd.Rot(0, 0, phase) * loft

