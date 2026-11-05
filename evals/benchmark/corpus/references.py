"""Host-only positive-control builders for the benchmark corpus.

The CAD and DXF dependencies are imported only when a builder is called.  These
objects are controls for validating the corpus and an independent auditor; they
are never solver inputs or sampled-agent results.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Callable


def _cad() -> dict[str, Any]:
    # Keep package import cheap for public projection and task validation.
    from build123d import (  # type: ignore[import-not-found]
        Align,
        Box,
        Compound,
        Cylinder,
        Face,
        Pos,
        Rot,
        Vector,
        Wire,
        extrude,
    )

    return locals()


def _box_from_base(
    width: float,
    depth: float,
    height: float,
    *,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
) -> Any:
    c = _cad()
    return c["Pos"](x, y, z + height / 2) * c["Box"](
        width,
        depth,
        height,
        align=(c["Align"].CENTER, c["Align"].CENTER, c["Align"].CENTER),
    )


def _cylinder(
    diameter: float,
    length: float,
    *,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    axis: str = "z",
) -> Any:
    c = _cad()
    rotation = {"x": c["Rot"](Y=90), "y": c["Rot"](X=90), "z": c["Rot"]()}[axis]
    return c["Pos"](x, y, z) * rotation * c["Cylinder"](
        diameter / 2,
        length,
        align=(c["Align"].CENTER, c["Align"].CENTER, c["Align"].CENTER),
    )


def _box_centered(size: Sequence[float], center: Sequence[float]) -> Any:
    c = _cad()
    return c["Pos"](*center) * c["Box"](
        *size,
        align=(c["Align"].CENTER, c["Align"].CENTER, c["Align"].CENTER),
    )


def _union(shapes: Sequence[Any]) -> Any:
    if not shapes:
        raise ValueError("a reference union needs at least one shape")
    result = shapes[0]
    for shape in shapes[1:]:
        result = result + shape
    return result


def _subtract_holes(shape: Any, centers: Sequence[Sequence[float]], diameter: float, depth: float, *, z: float = 0.0) -> Any:
    for x, y in centers:
        shape = shape - _cylinder(diameter, depth, x=x, y=y, z=z)
    return shape


def mounting_plate(
    *,
    width_mm: float,
    depth_mm: float,
    thickness_mm: float,
    hole_diameter_mm: float,
    hole_centers_mm: Sequence[Sequence[float]],
    counterbore_diameter_mm: float | None = None,
    counterbore_depth_mm: float | None = None,
) -> Any:
    """Centered XY plate; underside is z=0."""
    shape = _box_from_base(width_mm, depth_mm, thickness_mm)
    shape = _subtract_holes(
        shape,
        hole_centers_mm,
        hole_diameter_mm,
        thickness_mm + 2,
        z=thickness_mm / 2,
    )
    if counterbore_diameter_mm is not None:
        if counterbore_depth_mm is None:
            raise ValueError("counterbore_depth_mm is required with a counterbore")
        for x, y in hole_centers_mm:
            shape = shape - _cylinder(
                counterbore_diameter_mm,
                counterbore_depth_mm + 0.2,
                x=x,
                y=y,
                z=thickness_mm - counterbore_depth_mm / 2 + 0.1,
            )
    return shape


def slotted_rail(
    *,
    width_mm: float,
    depth_mm: float,
    thickness_mm: float,
    slot_length_mm: float,
    slot_width_mm: float,
    slot_centers_mm: Sequence[Sequence[float]],
) -> Any:
    """Plate with rounded slots whose long axis is X."""
    _cad()
    shape = _box_from_base(width_mm, depth_mm, thickness_mm)
    straight = slot_length_mm - slot_width_mm
    for x, y in slot_centers_mm:
        slot = _box_centered(
            (straight, slot_width_mm, thickness_mm + 2),
            (x, y, thickness_mm / 2),
        )
        end_offset = straight / 2
        slot = slot + _cylinder(slot_width_mm, thickness_mm + 2, x=x - end_offset, y=y, z=thickness_mm / 2)
        slot = slot + _cylinder(slot_width_mm, thickness_mm + 2, x=x + end_offset, y=y, z=thickness_mm / 2)
        shape = shape - slot
    return shape


def l_bracket(
    *,
    width_mm: float,
    depth_mm: float,
    height_mm: float,
    thickness_mm: float,
    wall_hole_diameter_mm: float,
    wall_hole_centers_xz_mm: Sequence[Sequence[float]],
) -> Any:
    """L bracket with an upright along the +Y edge and holes through that wall."""
    wall_y = depth_mm / 2 - thickness_mm / 2
    base = _box_from_base(width_mm, depth_mm, thickness_mm)
    upright = _box_from_base(width_mm, thickness_mm, height_mm, y=wall_y)
    shape = base + upright
    for x, z in wall_hole_centers_xz_mm:
        shape = shape - _cylinder(
            wall_hole_diameter_mm,
            thickness_mm + 2,
            x=x,
            y=wall_y,
            z=z,
            axis="y",
        )
    return shape


def gusseted_bracket(
    *,
    width_mm: float,
    depth_mm: float,
    height_mm: float,
    thickness_mm: float,
    gusset_width_mm: float,
    gusset_thickness_mm: float,
    wall_hole_diameter_mm: float,
    wall_hole_centers_xz_mm: Sequence[Sequence[float]],
) -> Any:
    """L bracket with one triangular web centered on the width."""
    c = _cad()
    shape = l_bracket(
        width_mm=width_mm,
        depth_mm=depth_mm,
        height_mm=height_mm,
        thickness_mm=thickness_mm,
        wall_hole_diameter_mm=wall_hole_diameter_mm,
        wall_hole_centers_xz_mm=wall_hole_centers_xz_mm,
    )
    y_back = depth_mm / 2 - thickness_mm
    y_front = y_back - gusset_width_mm
    z_bottom = thickness_mm
    z_top = height_mm
    x_start = -gusset_thickness_mm / 2
    points = [
        c["Vector"](x_start, y_front, z_bottom),
        c["Vector"](x_start, y_back, z_bottom),
        c["Vector"](x_start, y_back, z_top),
    ]
    wire = c["Wire"].make_polygon(points, close=True)
    web = c["extrude"](c["Face"](wire), amount=gusset_thickness_mm, dir=(1, 0, 0))
    return shape + web


def sleeve_bushing(
    *,
    outer_diameter_mm: float,
    length_mm: float,
    bore_diameter_mm: float,
    counterbore_diameter_mm: float | None = None,
    counterbore_depth_mm: float | None = None,
) -> Any:
    c = _cad()
    shape = c["Cylinder"](outer_diameter_mm / 2, length_mm) - c["Cylinder"](bore_diameter_mm / 2, length_mm + 2)
    if counterbore_diameter_mm is not None:
        if counterbore_depth_mm is None:
            raise ValueError("counterbore_depth_mm is required with a counterbore")
        shape = shape - _cylinder(
            counterbore_diameter_mm,
            counterbore_depth_mm + 0.2,
            z=length_mm / 2 - counterbore_depth_mm / 2 + 0.1,
        )
    return shape


def counterbore_flange(
    *,
    outer_diameter_mm: float,
    thickness_mm: float,
    bore_diameter_mm: float,
    bolt_diameter_mm: float,
    bolt_centers_mm: Sequence[Sequence[float]],
    counterbore_diameter_mm: float | None = None,
    counterbore_depth_mm: float | None = None,
) -> Any:
    c = _cad()
    shape = c["Cylinder"](outer_diameter_mm / 2, thickness_mm)
    shape = shape - c["Cylinder"](bore_diameter_mm / 2, thickness_mm + 2)
    shape = _subtract_holes(shape, bolt_centers_mm, bolt_diameter_mm, thickness_mm + 2)
    if counterbore_diameter_mm is not None:
        if counterbore_depth_mm is None:
            raise ValueError("counterbore_depth_mm is required with a counterbore")
        for x, y in bolt_centers_mm:
            shape = shape - _cylinder(
                counterbore_diameter_mm,
                counterbore_depth_mm + 0.2,
                x=x,
                y=y,
                z=thickness_mm / 2 - counterbore_depth_mm / 2 + 0.1,
            )
    return shape


def step_bushing(
    *,
    body_diameter_mm: float,
    body_length_mm: float,
    shoulder_diameter_mm: float,
    shoulder_length_mm: float,
    bore_diameter_mm: float,
    counterbore_diameter_mm: float,
    counterbore_depth_mm: float,
) -> Any:
    _cad()
    total = body_length_mm + shoulder_length_mm
    body = _cylinder(body_diameter_mm, body_length_mm, z=body_length_mm / 2)
    shoulder = _cylinder(
        shoulder_diameter_mm,
        shoulder_length_mm,
        z=body_length_mm + shoulder_length_mm / 2,
    )
    shape = body + shoulder
    shape = shape - _cylinder(bore_diameter_mm, total + 2, z=total / 2)
    shape = shape - _cylinder(
        counterbore_diameter_mm,
        counterbore_depth_mm + 0.2,
        z=total - counterbore_depth_mm / 2 + 0.1,
    )
    return shape


def clevis(
    *,
    width_mm: float,
    depth_mm: float,
    height_mm: float,
    thickness_mm: float,
    ear_thickness_mm: float,
    pin_diameter_mm: float,
    pin_hole_center_z_mm: float | None = None,
) -> Any:
    ear_x = width_mm / 2 - ear_thickness_mm / 2
    base = _box_from_base(width_mm, depth_mm, thickness_mm)
    ears = [
        _box_from_base(ear_thickness_mm, depth_mm, height_mm - thickness_mm, x=sx * ear_x, z=thickness_mm)
        for sx in (-1, 1)
    ]
    shape = _union([base, *ears])
    hole_z = pin_hole_center_z_mm if pin_hole_center_z_mm is not None else thickness_mm + (height_mm - thickness_mm) * 0.62
    for sx in (-1, 1):
        shape = shape - _cylinder(
            pin_diameter_mm,
            ear_thickness_mm + 2,
            x=sx * ear_x,
            y=0,
            z=hole_z,
            axis="x",
        )
    return shape


def u_channel(
    *,
    width_mm: float,
    depth_mm: float,
    height_mm: float,
    wall_mm: float,
    floor_hole_diameter_mm: float | None = None,
    floor_hole_centers_mm: Sequence[Sequence[float]] | None = None,
) -> Any:
    base = _box_from_base(width_mm, depth_mm, wall_mm)
    side_height = height_mm - wall_mm
    sides = [
        _box_from_base(wall_mm, depth_mm, side_height, x=sx * (width_mm / 2 - wall_mm / 2), z=wall_mm)
        for sx in (-1, 1)
    ]
    shape = _union([base, *sides])
    if floor_hole_diameter_mm is not None:
        if floor_hole_centers_mm is None:
            raise ValueError("floor_hole_centers_mm is required with floor holes")
        shape = _subtract_holes(shape, floor_hole_centers_mm, floor_hole_diameter_mm, wall_mm + 2, z=wall_mm / 2)
    return shape


def c_channel(*, width_mm: float, depth_mm: float, height_mm: float, wall_mm: float) -> Any:
    """C profile: continuous web at -X and flanges projecting in +X."""
    web = _box_from_base(wall_mm, depth_mm, height_mm, x=-width_mm / 2 + wall_mm / 2)
    flange_width = width_mm - wall_mm
    lower = _box_from_base(flange_width, depth_mm, wall_mm, x=wall_mm / 2, z=0)
    upper = _box_from_base(flange_width, depth_mm, wall_mm, x=wall_mm / 2, z=height_mm - wall_mm)
    return _union([web, lower, upper])


def open_enclosure(
    *,
    width_mm: float,
    depth_mm: float,
    height_mm: float,
    wall_mm: float,
    floor_mm: float,
    hole_diameter_mm: float,
    hole_centers_mm: Sequence[Sequence[float]],
) -> Any:
    outer = _box_from_base(width_mm, depth_mm, height_mm)
    cavity = _box_from_base(
        width_mm - 2 * wall_mm,
        depth_mm - 2 * wall_mm,
        height_mm - floor_mm + 1,
        z=floor_mm,
    )
    shape = outer - cavity
    return _subtract_holes(shape, hole_centers_mm, hole_diameter_mm, floor_mm + 2, z=floor_mm / 2)


def enclosure_lid(
    *,
    width_mm: float,
    depth_mm: float,
    thickness_mm: float,
    hole_diameter_mm: float,
    hole_centers_mm: Sequence[Sequence[float]],
) -> Any:
    shape = _box_from_base(width_mm, depth_mm, thickness_mm)
    return _subtract_holes(shape, hole_centers_mm, hole_diameter_mm, thickness_mm + 2, z=thickness_mm / 2)


def hose_adapter(
    *,
    left_diameter_mm: float,
    middle_diameter_mm: float,
    right_diameter_mm: float,
    length_mm: float,
    bore_diameter_mm: float,
    end_length_mm: float,
) -> Any:
    _cad()
    middle_length = length_mm - 2 * end_length_mm
    left = _cylinder(left_diameter_mm, end_length_mm, z=end_length_mm / 2)
    middle = _cylinder(middle_diameter_mm, middle_length, z=end_length_mm + middle_length / 2)
    right = _cylinder(right_diameter_mm, end_length_mm, z=length_mm - end_length_mm / 2)
    shape = _union([left, middle, right])
    return shape - _cylinder(bore_diameter_mm, length_mm + 2, z=length_mm / 2)


def lobe_wheel(
    *,
    disc_diameter_mm: float,
    disc_thickness_mm: float,
    hub_diameter_mm: float,
    hub_length_mm: float,
    bore_diameter_mm: float,
    lobe_count: int,
    lobe_width_mm: float,
    lobe_height_mm: float,
) -> Any:
    c = _cad()
    disc = _cylinder(disc_diameter_mm, disc_thickness_mm, z=disc_thickness_mm / 2)
    hub = _cylinder(hub_diameter_mm, hub_length_mm, z=disc_thickness_mm + hub_length_mm / 2)
    # Extend each rectangular prism into the disc, keeping the requested outer
    # tip exactly disc_radius + projection. An arbitrary overlap must not shorten
    # the requested projection or alter its expected envelope.
    radial_length = disc_diameter_mm / 2 + lobe_height_mm
    radius = radial_length / 2
    lobes = []
    for index in range(lobe_count):
        angle = 360 * index / lobe_count
        lobes.append(
            c["Rot"](Z=angle)
            * c["Pos"](radius, 0, disc_thickness_mm / 2)
            * c["Box"](
                radial_length,
                lobe_width_mm,
                disc_thickness_mm,
                align=(c["Align"].CENTER, c["Align"].CENTER, c["Align"].CENTER),
            )
        )
    shape = _union([disc, hub, *lobes])
    return shape - _cylinder(bore_diameter_mm, disc_thickness_mm + hub_length_mm + 2, z=(disc_thickness_mm + hub_length_mm) / 2)


def spoked_wheel(
    *,
    outer_diameter_mm: float,
    hub_diameter_mm: float,
    width_mm: float,
    rim_thickness_mm: float,
    spoke_count: int,
    bore_diameter_mm: float,
) -> Any:
    c = _cad()
    outer_r = outer_diameter_mm / 2
    hub_r = hub_diameter_mm / 2
    rim = c["Cylinder"](outer_r, width_mm) - c["Cylinder"](outer_r - rim_thickness_mm, width_mm + 2)
    hub = c["Cylinder"](hub_r, width_mm)
    spoke_length = outer_r - rim_thickness_mm - hub_r + 2
    spoke_center = hub_r + spoke_length / 2 - 0.5
    spokes = []
    for index in range(spoke_count):
        angle = 360 * index / spoke_count
        spokes.append(
            c["Rot"](Z=angle)
            * c["Pos"](spoke_center, 0, 0)
            * c["Box"](
                spoke_length,
                rim_thickness_mm * 2,
                width_mm,
                align=(c["Align"].CENTER, c["Align"].CENTER, c["Align"].CENTER),
            )
        )
    shape = _union([rim, hub, *spokes])
    return shape - c["Cylinder"](bore_diameter_mm / 2, width_mm + 2)


def split_clamp(
    *,
    width_mm: float,
    depth_mm: float,
    height_mm: float,
    bore_diameter_mm: float,
    split_width_mm: float,
    bolt_diameter_mm: float | None = None,
) -> Any:
    shape = _box_from_base(width_mm, depth_mm, height_mm)
    shape = shape - _cylinder(bore_diameter_mm, height_mm + 2, z=height_mm / 2)
    split = _box_centered(
        (width_mm / 2 + 1, split_width_mm, height_mm + 2),
        (width_mm / 4, 0, height_mm / 2),
    )
    shape = shape - split
    if bolt_diameter_mm is not None:
        shape = shape - _cylinder(
            bolt_diameter_mm,
            width_mm + 2,
            x=0,
            y=0,
            z=height_mm * 0.72,
            axis="x",
        )
    return shape


def heat_sink(
    *,
    width_mm: float,
    depth_mm: float,
    base_thickness_mm: float,
    fin_height_mm: float,
    fin_thickness_mm: float,
    fin_count: int,
) -> Any:
    base = _box_from_base(width_mm, depth_mm, base_thickness_mm)
    pitch = width_mm / fin_count
    fins = [
        _box_from_base(
            fin_thickness_mm,
            depth_mm,
            fin_height_mm,
            x=-width_mm / 2 + pitch * (index + 0.5),
            z=base_thickness_mm,
        )
        for index in range(fin_count)
    ]
    return _union([base, *fins])


def ribbed_cover(
    *,
    width_mm: float,
    depth_mm: float,
    plate_thickness_mm: float,
    rib_height_mm: float,
    rib_thickness_mm: float,
    rib_count: int,
    hole_diameter_mm: float,
    hole_centers_mm: Sequence[Sequence[float]],
) -> Any:
    c = _cad()
    plate = _box_from_base(width_mm, depth_mm, plate_thickness_mm)
    pitch = width_mm / (rib_count + 1)
    ribs = [
        c["Pos"](-width_mm / 2 + pitch * (index + 1), 0, -rib_height_mm / 2)
        * c["Box"](
            rib_thickness_mm,
            depth_mm,
            rib_height_mm,
            align=(c["Align"].CENTER, c["Align"].CENTER, c["Align"].CENTER),
        )
        for index in range(rib_count)
    ]
    shape = _union([plate, *ribs])
    return _subtract_holes(shape, hole_centers_mm, hole_diameter_mm, plate_thickness_mm + 2, z=plate_thickness_mm / 2)


def pipe_saddle(*, width_mm: float, depth_mm: float, height_mm: float, pipe_diameter_mm: float) -> Any:
    shape = _box_from_base(width_mm, depth_mm, height_mm)
    cutter = _cylinder(pipe_diameter_mm, width_mm + 2, z=height_mm, axis="x")
    return shape - cutter


def pulley(
    *,
    outer_diameter_mm: float,
    thickness_mm: float,
    hub_diameter_mm: float,
    hub_length_mm: float,
    bore_diameter_mm: float,
) -> Any:
    _cad()
    wheel = _cylinder(outer_diameter_mm, thickness_mm, z=thickness_mm / 2)
    hub = _cylinder(hub_diameter_mm, hub_length_mm, z=thickness_mm + hub_length_mm / 2)
    shape = wheel + hub
    return shape - _cylinder(bore_diameter_mm, thickness_mm + hub_length_mm + 2, z=(thickness_mm + hub_length_mm) / 2)


def fan_carrier(
    *,
    fan_width_mm: float,
    fan_depth_mm: float,
    fan_height_mm: float,
    rail_thickness_mm: float,
    clearance_mm: float,
) -> Any:
    inner_w = fan_width_mm + 2 * clearance_mm
    inner_d = fan_depth_mm + 2 * clearance_mm
    outer_d = inner_d + 2 * rail_thickness_mm
    total_h = fan_height_mm + rail_thickness_mm
    rails = [
        _box_from_base(rail_thickness_mm, outer_d, total_h, x=sx * (inner_w / 2 + rail_thickness_mm / 2))
        for sx in (-1, 1)
    ]
    rails += [
        _box_from_base(inner_w, rail_thickness_mm, total_h, y=sy * (inner_d / 2 + rail_thickness_mm / 2))
        for sy in (-1, 1)
    ]
    return _union(rails)


def blocked_counterbore_bracket(
    *,
    width_mm: float,
    depth_mm: float,
    base_thickness_mm: float,
    wall_height_mm: float,
    wall_thickness_mm: float,
    counterbore_center_mm: Sequence[float],
    through_diameter_mm: float,
    counterbore_diameter_mm: float,
    counterbore_depth_mm: float,
) -> Any:
    wall_y = depth_mm / 2 - wall_thickness_mm / 2
    base = _box_from_base(width_mm, depth_mm, base_thickness_mm)
    wall = _box_from_base(width_mm, wall_thickness_mm, wall_height_mm, y=wall_y)
    x, y = counterbore_center_mm
    shape = base + wall
    shape = shape - _cylinder(through_diameter_mm, base_thickness_mm + 2, x=x, y=y, z=base_thickness_mm / 2)
    shape = shape - _cylinder(
        counterbore_diameter_mm,
        counterbore_depth_mm + 0.2,
        x=x,
        y=y,
        z=base_thickness_mm - counterbore_depth_mm / 2 + 0.1,
    )
    return shape


def assembly_primitives(*, components: Sequence[Mapping[str, Any]]) -> Any:
    """Build a compound from explicitly declared primitive components."""
    c = _cad()
    built = []
    for component in components:
        shape_name = component["shape"]
        center = component["center_mm"]
        size = component["size_mm"]
        if shape_name == "box":
            shape = _box_centered(size, center)
        elif shape_name == "cylinder":
            shape = c["Pos"](*center) * c["Cylinder"](
                size[0] / 2,
                size[1],
                align=(c["Align"].CENTER, c["Align"].CENTER, c["Align"].CENTER),
            )
        else:
            raise ValueError(f"unsupported assembly primitive {shape_name!r}")
        shape.label = component["name"]
        built.append(shape)
    return c["Compound"](children=built)


def dxf_profile(*, contours: Sequence[Sequence[Sequence[float]]], circles: Sequence[Mapping[str, Any]]) -> Any:
    """Build a millimetre DXF profile on a single public CUT layer."""
    import ezdxf

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4  # millimetres
    if "CUT" not in doc.layers:
        doc.layers.new("CUT", dxfattribs={"color": 7})
    msp = doc.modelspace()
    for contour in contours:
        msp.add_lwpolyline(contour, close=True, dxfattribs={"layer": "CUT"})
    for circle in circles:
        msp.add_circle(
            circle["center_mm"],
            circle["diameter_mm"] / 2,
            dxfattribs={"layer": "CUT"},
        )
    return doc


def drawing_sheet(*, views: Sequence[str], dimensions: Sequence[Mapping[str, Any]], sheet_mm: Sequence[float]) -> Any:
    """Unavailable until actual projected geometry and measured dimensions exist."""
    raise NotImplementedError(
        "Drawing controls require real projected geometry and geometry-derived dimensions; "
        "view boxes and typed dimension labels are not a positive drawing control"
    )


REFERENCE_FACTORIES: dict[str, Callable[..., Any]] = {
    "mounting_plate": mounting_plate,
    "slotted_rail": slotted_rail,
    "l_bracket": l_bracket,
    "gusseted_bracket": gusseted_bracket,
    "sleeve_bushing": sleeve_bushing,
    "counterbore_flange": counterbore_flange,
    "step_bushing": step_bushing,
    "clevis": clevis,
    "u_channel": u_channel,
    "c_channel": c_channel,
    "open_enclosure": open_enclosure,
    "enclosure_lid": enclosure_lid,
    "hose_adapter": hose_adapter,
    "lobe_wheel": lobe_wheel,
    "spoked_wheel": spoked_wheel,
    "split_clamp": split_clamp,
    "heat_sink": heat_sink,
    "ribbed_cover": ribbed_cover,
    "pipe_saddle": pipe_saddle,
    "pulley": pulley,
    "fan_carrier": fan_carrier,
    "blocked_counterbore_bracket": blocked_counterbore_bracket,
    "assembly_primitives": assembly_primitives,
    "dxf_profile": dxf_profile,
    "drawing_sheet": drawing_sheet,
}


def build_reference(task: Mapping[str, Any], stage_id: str | None = None) -> Any:
    """Build one host-side positive control via a fixed factory allowlist.

    No source string, import path, or callable name from the corpus is executed.
    The selected factory is looked up in ``REFERENCE_FACTORIES``.
    """
    reference = task.get("reference")
    if not isinstance(reference, Mapping):
        raise ValueError(f"task {task.get('id', '<unknown>')!r} has no reference control")
    factory_name = reference.get("factory")
    if factory_name not in REFERENCE_FACTORIES:
        raise ValueError(f"unknown reference factory {factory_name!r}")
    stage_params = reference.get("stages")
    if not isinstance(stage_params, Mapping):
        raise ValueError("reference stages must be an object")
    if stage_id is None:
        if len(stage_params) != 1:
            raise ValueError("stage_id is required for a multi-stage reference")
        stage_id = next(iter(stage_params))
    if stage_id not in stage_params:
        raise ValueError(f"reference has no parameters for stage {stage_id!r}")
    params = stage_params[stage_id]
    if not isinstance(params, Mapping):
        raise ValueError("reference stage parameters must be an object")
    return REFERENCE_FACTORIES[factory_name](**dict(params))


def build_all_references(corpus: Mapping[str, Any]) -> tuple[list[tuple[str, str, Any]], list[tuple[str, str, str]]]:
    """Build every declared artifact-stage control.

    Returns ``(built, unsupported)``.  Import failures and builder exceptions are
    reported as precise unsupported entries; no control is treated as an agent
    result or quality-evaluator result.
    """
    built: list[tuple[str, str, Any]] = []
    unsupported: list[tuple[str, str, str]] = []
    for task in corpus.get("tasks", []):
        if "reference" not in task:
            continue
        for stage in task["stages"]:
            if stage["expected_outcome"] != "artifact":
                continue
            try:
                artifact = build_reference(task, stage["id"])
            except Exception as exc:  # surfaced to the caller; never silently pass
                unsupported.append((task["id"], stage["id"], f"{type(exc).__name__}: {exc}"))
            else:
                built.append((task["id"], stage["id"], artifact))
    return built, unsupported
