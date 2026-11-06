"""Catalog-bounded detailed component geometry.

All dimensions used for the fit envelope come from the component record.
Features absent from that record are explicitly marked representative in the
model metadata; missing fit dimensions are errors, never guessed values.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any


_LODS = {"fit", "full"}
_SCREW_CONSTRUCTORS = {
    "ISO 4762": "socket_head",
    "ASME B18.3": "socket_head",
    "ISO 10642": "countersunk",
}


def make_detailed(
    component_id: str,
    *,
    units: str = "mm",
    lod: str = "full",
    vendor_step: str | None = None,
    origin_mm: Sequence[float] = (0.0, 0.0, 0.0),
    vendor_evidence: Mapping[str, Any] | None = None,
    contact_count: int | None = None,
    thread: str = "cosmetic",
    **selection: Any,
) -> Any:
    """Build detailed geometry for selected catalog parts.

    ``lod`` is ``fit`` or ``full``. A supplied vendor STEP asset replaces the
    representative construction and is translated so ``origin_mm`` in that
    file lands at the model origin. ``vendor_evidence`` is copied verbatim into
    metadata and never alters the catalog evidence status.
    """
    from . import _catalog, _check_selection

    if units not in {"mm", "in"}:
        raise ValueError("units must be 'mm' or 'in'")
    if lod not in _LODS:
        raise ValueError("lod must be 'fit' or 'full'")
    record = _catalog().get(component_id)
    if record is None:
        raise KeyError(f"Unknown component {component_id!r}")

    selection = dict(selection)
    if contact_count is not None and (
        isinstance(contact_count, bool) or not isinstance(contact_count, int) or contact_count < 1
    ):
        raise ValueError("contact_count must be a positive whole number")
    if vendor_step is not None:
        if selection or contact_count is not None or thread != "cosmetic":
            raise ValueError("Do not combine vendor_step geometry with component selection, contact_count, or thread options")
        if not isinstance(vendor_step, str) or not vendor_step.strip():
            raise ValueError("vendor_step must be an asset filename added with aad_import")
        if not isinstance(vendor_evidence, Mapping) or not any(
            isinstance(vendor_evidence.get(key), str) and vendor_evidence[key].strip()
            for key in ("source", "url", "document")
        ):
            raise ValueError("vendor_step requires vendor_evidence with a non-empty source, url, or document")
        origin = _vector3(origin_mm, "origin_mm")
        from aad import asset
        from build123d import Pos

        shape = Pos(*(-coordinate / (25.4 if units == "in" else 1.0) for coordinate in origin)) * asset(vendor_step)
        item_metadata = {
            "component_id": component_id,
            "catalog_evidence": deepcopy(record["evidence"]),
            "dimensions_mm": deepcopy(record.get("dimensions_mm", {})),
            "selection": deepcopy(selection),
            "geometry_source": "vendor_step",
            "vendor_import": {
                "asset": vendor_step,
                "origin_mm": list(origin),
                "evidence": deepcopy(dict(vendor_evidence)),
            },
            "level_of_detail": "vendor_step",
            "representative_features": [],
        }
    else:
        if vendor_evidence is not None or tuple(origin_mm) != (0.0, 0.0, 0.0):
            raise ValueError("origin_mm and vendor_evidence apply only when vendor_step is supplied")
        if contact_count is not None and (record.get("category") != "connector" or "ways" in record.get("specs", {})):
            raise ValueError("contact_count is only for connector records without catalog contact-count data")
        if record.get("category") == "bearing":
            if record.get("family") not in {
                "Deep groove ball bearing",
                "Capped deep groove ball bearing",
                "Radial ball bearing (ISO series)",
            }:
                raise ValueError(
                    f"{component_id} is not a registered radial deep-groove bearing family; "
                    "use a dimensioned vendor STEP asset rather than substituting internal geometry"
                )
            if "variant" in record.get("choices", {}) and "variant" not in selection:
                selection["variant"] = "open"
        if record.get("category") == "fastener":
            if thread not in {"cosmetic", "envelope"}:
                if thread == "helical":
                    raise ValueError(
                        "thread='helical' fails the unchanged edge_quality and face_quality gates; "
                        "use thread='cosmetic' and let the drawing note identify the thread"
                    )
                raise ValueError("thread must be 'cosmetic' or 'envelope'")
        elif thread != "cosmetic":
            raise ValueError("thread is only applicable to screw records")

        problems = _check_selection(record, selection, complete=False)
        if problems:
            raise ValueError("; ".join(problems))
        from build123d import scale

        category = record.get("category")
        representative: list[str] = []
        if category == "bearing":
            shape, representative = _bearing(record, selection, lod)
        elif category == "connector":
            shape, representative = _connector(record, selection, contact_count, lod)
        elif category == "motor":
            shape, representative = _motor(record, selection, lod)
        elif category == "fastener" and record.get("envelope", {}).get("shape") == "screw":
            shape, representative = _screw(record, selection, thread)
        else:
            raise ValueError(
                f"Detailed geometry is not registered for {component_id}; supported categories are "
                "radial bearings, connectors with catalog fit dimensions, motors with shaft dimensions, "
                "and socket screws with catalog drive dimensions."
            )

        if units == "in":
            shape = scale(shape, 1 / 25.4)
        selection_metadata = deepcopy(selection)
        if category == "bearing":
            selection_metadata.setdefault("variant", _default_bearing_variant(record))
        item_metadata = {
            "component_id": component_id,
            "catalog_evidence": deepcopy(record["evidence"]),
            "dimensions_mm": deepcopy(record.get("dimensions_mm", {})),
            "selection": selection_metadata,
            "geometry_source": "catalog_dimensions_plus_representative_details",
            "level_of_detail": lod,
            "representative_features": representative,
        }
        if category == "fastener":
            item_metadata["thread"] = {
                "designation": _thread_designation(record),
                "representation": "cosmetic" if thread == "cosmetic" else "major_diameter_envelope",
            }

    _record_model_metadata(item_metadata)
    return shape


def _vector3(value: Sequence[float], name: str) -> tuple[float, float, float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        raise ValueError(f"{name} must contain three finite millimetre coordinates")
    result = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in result):
        raise ValueError(f"{name} must contain three finite millimetre coordinates")
    return result  # type: ignore[return-value]


def _record_model_metadata(item: dict[str, Any]) -> None:
    try:
        from aad.modeling import meta
    except ImportError:
        return
    current = meta().get("aad_component_geometries", [])
    if not isinstance(current, list):
        raise ValueError("metadata aad_component_geometries must be a list")
    meta(aad_component_geometries=[*current, item])


def _need_dimensions(record: dict[str, Any], *keys: str) -> dict[str, float]:
    dimensions = record.get("dimensions_mm") or {}
    missing = [key for key in keys if not isinstance(dimensions.get(key), int | float)]
    if missing:
        raise ValueError(
            f"{record['id']} lacks catalog dimensions {', '.join(missing)} required for detailed geometry; "
            "supply a vendor STEP asset with vendor_evidence or use make_envelope"
        )
    return dimensions


def _bearing(record: dict[str, Any], selection: dict[str, Any], lod: str) -> tuple[Any, list[str]]:
    from build123d import Align, Compound, Cylinder, Pos, Sphere, Torus

    dims = _need_dimensions(record, "bore_d", "outer_D", "width_B")
    bore_d, outer_d, width = (float(dims[key]) for key in ("bore_d", "outer_D", "width_B"))
    if not 0 < bore_d < outer_d or width <= 0:
        raise ValueError(f"{record['id']} has invalid radial-bearing fit dimensions")
    variant = selection.get("variant", _default_bearing_variant(record))
    if variant not in {"open", "ZZ", "2RS"}:
        raise ValueError(f"{record['id']} variant must be open, ZZ, or 2RS")

    # Raceway, ball diameter/count, cage and closure details are not cataloged
    # for these records. They are intentionally labelled representative.
    ball_d = min(width * 0.6, (outer_d - bore_d) * 0.3)
    ball_r = ball_d / 2
    pitch_radius = (bore_d + outer_d) / 4
    race_depth = ball_d * 0.08
    outer_inner = pitch_radius + ball_r - race_depth
    inner_outer = pitch_radius - ball_r + race_depth
    outer = Cylinder(outer_d / 2, width, align=(Align.CENTER, Align.CENTER, Align.MIN))
    outer -= Cylinder(outer_inner, width + 2, align=(Align.CENTER, Align.CENTER, Align.CENTER)).moved(Pos(0, 0, width / 2))
    inner = Cylinder(inner_outer, width, align=(Align.CENTER, Align.CENTER, Align.MIN))
    inner -= Cylinder(bore_d / 2, width + 2, align=(Align.CENTER, Align.CENTER, Align.CENTER)).moved(Pos(0, 0, width / 2))
    outer -= Torus(pitch_radius, ball_r).moved(Pos(0, 0, width / 2))
    inner -= Torus(pitch_radius, ball_r).moved(Pos(0, 0, width / 2))

    ball_count = 8 if lod == "fit" else max(8, min(16, round(math.pi * pitch_radius / ball_d)))
    pieces = [outer, inner]
    pieces.extend(
        Sphere(ball_r).moved(Pos(pitch_radius * math.cos(2 * math.pi * index / ball_count),
                                 pitch_radius * math.sin(2 * math.pi * index / ball_count), width / 2))
        for index in range(ball_count)
    )
    if lod == "full":
        cage_height = max(ball_d * 0.12, 0.2)
        cage_radius = ball_d * 0.12
        cage_z = width / 2 - ball_r - cage_height - 0.05
        cage = Cylinder(pitch_radius + cage_radius, cage_height,
                        align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Pos(0, 0, cage_z))
        cage -= Cylinder(pitch_radius - cage_radius, cage_height + 0.2,
                         align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Pos(0, 0, cage_z - 0.1))
        pieces.append(cage)
        if variant != "open":
            closure_h = min(width * 0.08, 0.5)
            closure_outer = outer_d * 0.46
            closure_inner = bore_d * 0.55
            for z in (closure_h, width - 2 * closure_h):
                closure = Cylinder(closure_outer, closure_h, align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Pos(0, 0, z))
                closure -= Cylinder(closure_inner, closure_h + 0.2,
                                   align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Pos(0, 0, z - 0.1))
                pieces.append(closure)
    return Compound(children=pieces), ["raceway profile", "ball diameter/count", "cage"] + ([] if variant == "open" else [f"{variant} closures"])


def _default_bearing_variant(record: dict[str, Any]) -> str:
    return "ZZ" if record.get("family") == "Capped deep groove ball bearing" else "open"


def _connector(
    record: dict[str, Any], selection: dict[str, Any], contact_count: int | None, lod: str,
) -> tuple[Any, list[str]]:
    from build123d import Align, Box, Compound, Pos

    dims = _need_dimensions(record, "x", "y", "z")
    x, y, z = (float(dims[key]) for key in ("x", "y", "z"))
    specs = record.get("specs", {})
    ways = specs.get("ways")
    if isinstance(ways, int) and ways > 0:
        count = ways
        if contact_count is not None and contact_count != ways:
            raise ValueError(f"{record['id']} catalog specifies {ways} contacts; contact_count cannot override it")
    elif contact_count is not None:
        count = contact_count
    else:
        raise ValueError(
            f"{record['id']} has no catalog contact count; pass contact_count for a representative layout "
            "or provide vendor_step and vendor_evidence"
        )
    pitch = specs.get("pitch_mm")
    rows = int(specs.get("rows", 1))
    columns = math.ceil(count / rows)
    if isinstance(pitch, int | float) and pitch > 0:
        step_x = float(pitch)
        step_y = float(pitch)
    else:
        step_x = min(x / max(columns, 2), y / max(rows, 2))
        step_y = step_x
    pin_size = min(step_x, step_y, x, y) * 0.28
    pin_z = max(0.0, (z - min(z * 0.45, 3.0)) / 2)
    housing_z = min(z * 0.45, 3.0)
    housing = Box(x, y, housing_z, align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Pos(0, 0, pin_z))
    centers: list[tuple[float, float]] = []
    for index in range(count):
        row, column = divmod(index, columns)
        centers.append(((column - (columns - 1) / 2) * step_x, (row - (rows - 1) / 2) * step_y))
    for cx, cy in centers:
        if abs(cx) + pin_size / 2 > x / 2 or abs(cy) + pin_size / 2 > y / 2:
            raise ValueError(f"{record['id']} contact layout exceeds its catalog envelope")
        if lod == "full":
            housing -= Box(pin_size * 1.1, pin_size * 1.1, housing_z + 0.2,
                           align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Pos(cx, cy, pin_z - 0.1))
    if lod == "fit":
        # Keep the envelope while using a plain housing and external pin stubs.
        fit_housing_z = z - pin_z
        housing = Box(x, y, fit_housing_z, align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Pos(0, 0, pin_z))
    pieces = [housing]
    pieces.extend(
        Box(pin_size, pin_size, z if lod == "full" else pin_z,
            align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Pos(cx, cy, 0))
        for cx, cy in centers
    )
    representative = ["contact body dimensions", "pin cross-section"]
    if lod == "fit":
        representative.append("fit LOD housing and external pin-stub proportions")
    if not isinstance(pitch, int | float):
        representative.append("contact pitch and layout")
    elif not isinstance(ways, int):
        representative.append("contact count")
    return Compound(children=pieces), representative


def _motor(record: dict[str, Any], selection: dict[str, Any], lod: str) -> tuple[Any, list[str]]:
    from build123d import Align, Box, Compound, Cylinder, Pos

    envelope = record.get("envelope") or {}
    dims = record.get("dimensions_mm") or {}
    if envelope.get("shape") == "motor":
        body_length = dims.get("body_length", dims.get("h", envelope.get("z")))
        if not isinstance(body_length, int | float):
            raise ValueError(f"{record['id']} lacks a catalog motor-body length")
        if "d" in envelope:
            body = Cylinder(float(envelope["d"]) / 2, float(body_length), align=(Align.CENTER, Align.CENTER, Align.MAX))
        else:
            body = Box(float(envelope["x"]), float(envelope["y"]), float(body_length),
                       align=(Align.CENTER, Align.CENTER, Align.MAX))
        face_z = 0.0
    elif envelope.get("shape") == "box":
        dims = _need_dimensions(record, "width", "height", "length") if "length" in dims else _need_dimensions(record, "x", "y", "z")
        if {"width", "height", "length"} <= set(dims):
            body_length = float(dims["length"])
            body = Box(float(dims["width"]), float(dims["height"]), body_length,
                       align=(Align.CENTER, Align.CENTER, Align.MIN))
        else:
            body_length = float(dims["z"])
            body = Box(float(dims["x"]), float(dims["y"]), body_length,
                       align=(Align.CENTER, Align.CENTER, Align.MIN))
        face_z = body_length
    else:
        raise ValueError(f"{record['id']} has no supported motor body envelope")

    shaft_d = dims.get("shaft_d", dims.get("shaft_diameter", envelope.get("shaft_d")))
    shaft_length = dims.get("shaft_length", dims.get("shaft_l", envelope.get("shaft_l")))
    if not isinstance(shaft_d, int | float) or not isinstance(shaft_length, int | float):
        raise ValueError(
            f"{record['id']} lacks catalog output-shaft diameter/length; detailed shaft geometry would require invented dimensions. "
            "Supply a dimensioned vendor STEP asset with vendor_evidence."
        )
    offset_x = float(dims.get("shaft_offset_x", 0.0))
    offset_y = float(dims.get("shaft_offset_y", dims.get("shaft_offset", 0.0)))
    shaft = Cylinder(float(shaft_d) / 2, float(shaft_length),
                     align=(Align.CENTER, Align.CENTER, Align.MIN)).moved(Pos(offset_x, offset_y, face_z))

    representative = ["motor winding, bearing, and internal construction geometry omitted"]
    if "gear" in record.get("family", "").lower():
        representative.append("geartrain and motor/gearbox split unavailable in the catalog; not modeled")
    spacing = dims.get("mount_hole_spacing")
    pieces = [body, shaft]
    pilot_d, pilot_h = envelope.get("pilot_d"), envelope.get("pilot_h")
    if pilot_d and pilot_h:
        pieces.append(Cylinder(float(pilot_d) / 2, float(pilot_h),
                               align=(Align.CENTER, Align.CENTER, Align.MIN)))
    if isinstance(spacing, int | float) and spacing > 0 and lod == "full":
        hole_d = 3.5
        hole_depth = min(float(body_length) * 0.15, 5.0)
        mount_face_z = 0.0 if envelope.get("shape") == "motor" else float(body_length)
        fits_body = True
        if "d" in envelope:
            fits_body = math.sqrt(2) * float(spacing) / 2 + hole_d / 2 <= float(envelope["d"]) / 2
        else:
            fits_body = float(spacing) / 2 + hole_d / 2 <= min(float(envelope["x"]), float(envelope["y"])) / 2
        if fits_body:
            cutters = [Cylinder(hole_d / 2, hole_depth, align=(Align.CENTER, Align.CENTER, Align.MAX)).moved(
                Pos(sx * float(spacing) / 2, sy * float(spacing) / 2, mount_face_z))
                for sx in (-1, 1) for sy in (-1, 1)]
            body = body.cut(*cutters)
            pieces[0] = body
            representative.append("mounting-hole diameter and tapped depth")
        else:
            representative.append("mounting flange and holes not cataloged; no mounting pattern modeled")
    elif isinstance(spacing, int | float) and spacing > 0 and lod == "fit":
        representative.append("representative motor mounting-hole pattern omitted at fit LOD")
    elif record.get("family", "").lower().find("servo") < 0:
        representative.append("mounting holes unavailable in catalog; body mounting detail omitted")
    if not dims.get("shaft_offset_x") and not dims.get("shaft_offset_y") and not dims.get("shaft_offset"):
        representative.append("output shaft centered on catalog body face")
    return Compound(children=pieces), representative


def _screw(record: dict[str, Any], selection: dict[str, Any], thread: str) -> tuple[Any, list[str]]:
    from build123d import Align, Cone, Cylinder, Pos, RegularPolygon, extrude

    standard = record.get("standard")
    head_style = _SCREW_CONSTRUCTORS.get(standard)
    if head_style is None:
        raise ValueError(
            f"{record['id']} has no registered detailed head/drive constructor for {standard!r}; "
            "use a dimensioned vendor STEP asset rather than guessing the drive form"
        )
    if "length_mm" in selection:
        length = selection["length_mm"]
    elif "length_in" in selection:
        length = float(selection["length_in"]) * 25.4
    else:
        raise ValueError(f"{record['id']} needs its catalog length choice for detailed screw geometry")
    if not isinstance(length, int | float):
        raise ValueError(f"{record['id']} screw length must be numeric")
    dims = _need_dimensions(record, "head_d", "head_h", "across_flats_or_drive", "drive_depth")
    head_d = float(dims["head_d"])
    head_h = float(dims["head_h"])
    thread_d = record.get("envelope", {}).get("thread_d")
    if not isinstance(thread_d, int | float):
        raise ValueError(f"{record['id']} lacks catalog thread diameter required for detailed geometry")
    thread_d = float(thread_d)
    shank_length = float(length) - head_h if record["envelope"].get("length_includes_head") else float(length)
    if shank_length <= 0:
        raise ValueError(
            f"{record['id']} selected length is no greater than its catalog head height; "
            "there is no positive shank length to model"
        )
    shank = Cylinder(thread_d / 2, shank_length, align=(Align.CENTER, Align.CENTER, Align.MAX))
    if head_h > 0:
        if head_style == "countersunk":
            head = Cone(thread_d / 2, head_d / 2, head_h, align=(Align.CENTER, Align.CENTER, Align.MIN))
        else:
            head = Cylinder(head_d / 2, head_h, align=(Align.CENTER, Align.CENTER, Align.MIN))
        result = shank + head
    else:
        result = shank

    drive_af = float(dims["across_flats_or_drive"])
    drive_depth = float(dims["drive_depth"])
    socket = extrude(RegularPolygon(drive_af / 2, 6, major_radius=False), amount=drive_depth)
    top_z = head_h
    result -= socket.moved(Pos(0, 0, top_z - drive_depth))
    representative = ["thread form omitted from the B-rep"] if thread == "cosmetic" else []
    return result, representative


def _thread_designation(record: dict[str, Any]) -> str:
    designation = record.get("designation")
    if not isinstance(designation, str) or not designation.strip():
        component_id = record["id"]
        designation = (
            component_id.rsplit(".", 1)[-1]
            if str(record.get("standard", "")).startswith("ASME")
            else component_id.split(".", 2)[-1]
        )
    designation = designation.strip()
    return designation.replace("-", " × ", 1) if designation.startswith("M") else designation
