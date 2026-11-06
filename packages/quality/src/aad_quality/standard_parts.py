"""Small, on-demand adapters for selected ``bd_warehouse`` parts.

Catalog reads are static and do not import build123d, OCP, or bd_warehouse.
Part construction is deliberately limited to the closed registry below.
"""
from __future__ import annotations

import importlib

import math
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
from typing import Any

__all__ = ["list_parts", "get_part", "make_part"]

_UPSTREAM_SOURCE = {
    "package": "bd_warehouse",
    "version": "0.4.0",
    "url": "https://github.com/gumyr/bd_warehouse/tree/v0.4.0",
    "commit": "8e9be0645b53db3d7414f74828c2d67b0da21138",
    "license": "Apache-2.0",
}

# These sizes and the per-size nominal length ranges follow bd_warehouse 0.4.0's
# ISO 4762 fastener data and nominal_screw_lengths.csv.
_FASTENER_LENGTHS_BY_SIZE: dict[str, tuple[float, ...]] = {
    "M3-0.5": (5, 6, 8, 10, 12, 16, 20, 25, 30),
    "M4-0.7": (6, 8, 10, 12, 16, 20, 25, 30, 35, 40),
    "M5-0.8": (8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50),
    "M6-1": (8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60),
    "M8-1.25": (10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80),
    "M10-1.5": (16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100),
}

_BEARING_SIZES = (
    "M3-10-4",
    "M4-12-4",
    "M5-16-5",
    "M6-15-5",
    "M8-22-7",
    "M10-30-9",
)

# IsoThread takes numeric dimensions, not a size code. Keep the accepted codes
# explicit and map them to the metric diameter/pitch pairs supported here.
_THREAD_SIZES: dict[str, tuple[float, float]] = {
    "M3-0.5": (3.0, 0.5),
    "M4-0.7": (4.0, 0.7),
    "M5-0.8": (5.0, 0.8),
    "M6-1": (6.0, 1.0),
    "M8-1.25": (8.0, 1.25),
    "M10-1.5": (10.0, 1.5),
}

_FASTENER_ID = "fastener.iso4762.socket_head_cap_screw"
_NUT_ID = "nut.iso4032.hex_nut"
_BEARING_ID = "bearing.deep_groove_ball.skt"
_THREAD_ID = "thread.iso_metric_external"

_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": _FASTENER_ID,
        "family": "fastener",
        "title": "ISO 4762 socket-head cap screw",
        "summary": (
            "Metric socket-head cap screw; length is measured from under the head. "
            "Default: M6-1 x 20 mm."
        ),
        "source": {
            **_UPSTREAM_SOURCE,
            "constructor": "bd_warehouse.fastener.SocketHeadCapScrew",
        },
        "parameters": {
            "size": {
                "type": "enum",
                "default": "M6-1",
                "choices": list(_FASTENER_LENGTHS_BY_SIZE),
            },
            "length": {
                "type": "number",
                "unit": "mm",
                "default": 20.0,
                "constraint": "Nominal ISO 4762 length for the selected size.",
            },
            "thread": {
                "type": "enum",
                "default": "cosmetic",
                "choices": ["envelope", "cosmetic"],
            },
        },
        "fixed": {"fastener_type": "iso4762", "hand": "right", "mode": "private"},
        "example": (
            "from aad_quality.standard_parts import make_part\n"
            "screw = make_part(\n"
            "    'fastener.iso4762.socket_head_cap_screw', size='M6-1', length=20\n"
            ")"
        ),
        "limitations": [
            "thread='envelope' or thread='cosmetic' models the threaded length as a plain "
            "cylinder at the major diameter, which holds the true clearance envelope. "
            "A helical solid fails the unchanged hard gate's edge_quality and face_quality "
            "checks because its trimmed sweep creates sub-threshold sliver faces and edges. "
            "Use the cosmetic mode; AAD records the thread designation and adds it to drawings. "
            "Nominal geometry does not prove purchased-hardware fit or load capacity."
        ],
    },
    {
        "id": _BEARING_ID,
        "family": "bearing",
        "title": "Single-row deep-groove ball bearing",
        "summary": (
            "SKT-table bearing; default size key M8-22-7 "
            "(bore x outside diameter x width, mm)."
        ),
        "source": {
            **_UPSTREAM_SOURCE,
            "constructor": "bd_warehouse.bearing.SingleRowDeepGrooveBallBearing",
        },
        "parameters": {
            "size": {
                "type": "enum",
                "default": "M8-22-7",
                "choices": list(_BEARING_SIZES),
            }
        },
        "fixed": {"bearing_type": "SKT"},
        "example": (
            "from aad_quality.standard_parts import make_part\n"
            "bearing = make_part(\n"
            "    'bearing.deep_groove_ball.skt', size='M8-22-7'\n"
            ")"
        ),
        "limitations": [
            "The upstream model simplifies internal structure; it does not establish "
            "a load rating or fit."
        ],
    },
    {
        "id": _THREAD_ID,
        "family": "thread",
        "title": "ISO metric external thread (cosmetic representation)",
        "summary": "External right-hand thread envelope with a drawing note; default M6-1 x 8 mm.",
        "source": {
            **_UPSTREAM_SOURCE,
            "constructor": "AAD.cosmetic_external_thread_envelope",
        },
        "parameters": {
            "size": {
                "type": "enum",
                "default": "M6-1",
                "choices": list(_THREAD_SIZES),
            },
            "length": {
                "type": "number",
                "unit": "mm",
                "default": 8.0,
                "constraint": "Positive finite axial length.",
            },
            "representation": {
                "type": "enum",
                "default": "cosmetic",
                "choices": ["cosmetic"],
            },
        },
        "fixed": {
            "external": True,
            "hand": "right",
            "end_finishes": ["square", "square"],
        },
        "example": (
            "from aad_quality.standard_parts import make_part\n"
            "thread = make_part(\n"
            "    'thread.iso_metric_external', size='M6-1', length=8\n"
            ")"
        ),
        "limitations": [
            "Returns a smooth major-diameter envelope, with the thread designation "
            "recorded as cosmetic in model metadata and shown in AAD drawings. It has "
            "no helical B-rep, shaft, or mating hole; nominal geometry is not proof of fit or load."
        ],
    },
)

_FAST_THREAD = "Uses upstream's simple form: threads are not modeled, so check thread fit separately."
_NOMINAL = "Nominal catalog geometry. It does not prove purchased-hardware fit, rating or load capacity."


def _family(
    id: str, family: str, title: str, summary: str, module: str, cls: str, defaults: dict[str, Any],
    fixed: dict[str, Any] | None = None, limitation: str = _NOMINAL,
) -> dict[str, Any]:
    """One catalog record. Size names are validated by the pinned upstream constructor."""
    arguments = ", ".join(f"{key}={value!r}" for key, value in defaults.items())
    return {
        "id": id,
        "family": family,
        "title": title,
        "summary": summary,
        "source": {**_UPSTREAM_SOURCE, "constructor": f"{module}.{cls}"},
        "parameters": {key: {"type": "number" if isinstance(value, int | float) else "text", "default": value}
                       for key, value in defaults.items()},
        "fixed": fixed or {},
        "example": (
            "from aad_quality.standard_parts import make_part\n"
            f"item = make_part({id!r}, {arguments})"
        ),
        "limitations": [limitation],
    }


_FAMILIES: tuple[dict[str, Any], ...] = (
    _family("fastener.iso4017.hex_head_screw", "fastener", "ISO 4017 hex head screw", "Metric fully threaded hex head screw.",
            "bd_warehouse.fastener", "HexHeadScrew", {"size": "M6-1", "length": 20.0},
            {"fastener_type": "iso4017", "simple": True}, _FAST_THREAD),
    _family("fastener.iso10642.countersunk_screw", "fastener", "ISO 10642 countersunk screw", "Metric hexagon socket countersunk screw.",
            "bd_warehouse.fastener", "CounterSunkScrew", {"size": "M6-1", "length": 20.0},
            {"fastener_type": "iso10642", "simple": True}, _FAST_THREAD),
    _family("fastener.iso7380.button_head_screw", "fastener", "ISO 7380 button head screw", "Metric hexagon socket button head screw.",
            "bd_warehouse.fastener", "ButtonHeadScrew", {"size": "M6-1", "length": 20.0},
            {"fastener_type": "iso7380_1", "simple": True}, _FAST_THREAD),
    _family("fastener.iso4026.set_screw", "fastener", "ISO 4026 set screw", "Metric hexagon socket flat point set screw.",
            "bd_warehouse.fastener", "SetScrew", {"size": "M6-1", "length": 10.0},
            {"fastener_type": "iso4026", "simple": True}, _FAST_THREAD),
    _family("nut.iso4032.hex_nut", "fastener", "ISO 4032 hex nut", "Metric hex nut, style 1.",
            "bd_warehouse.fastener", "HexNut", {"size": "M6-1"}, {"fastener_type": "iso4032"},
            "Built by AAD from upstream's ISO 4032 dimension table with a 0.95 s chamfer and a plain bore at the major diameter. Threads are not modeled, so check thread fit separately."),
    _family("washer.iso7089.plain", "fastener", "ISO 7089 plain washer", "Metric plain washer, normal series.",
            "bd_warehouse.fastener", "PlainWasher", {"size": "M6"}, {"fastener_type": "iso7089"}),
    _family("bearing.angular_contact.skt", "bearing", "Angular contact ball bearing", "Single-row angular contact ball bearing.",
            "bd_warehouse.bearing", "SingleRowAngularContactBallBearing", {"size": "M10-30-9"}, {"bearing_type": "SKT"}),
    _family("bearing.cylindrical_roller.skt", "bearing", "Cylindrical roller bearing", "Single-row cylindrical roller bearing.",
            "bd_warehouse.bearing", "SingleRowCylindricalRollerBearing", {"size": "M17-40-12"}, {"bearing_type": "SKT"}),
    _family("seal.iso3601.o_ring", "seal", "ISO 3601 O-ring", "Toroidal seal by three-digit size code.",
            "bd_warehouse.o_rings", "ORing", {"size": "210"}),
    _family("ring.external_snap", "retaining", "External retaining ring", "External snap ring by shaft size.",
            "bd_warehouse.retaining_ring", "ExternalSnapRing", {"size": "25"}),
    _family("ring.internal_snap", "retaining", "Internal retaining ring", "Internal snap ring by bore size.",
            "bd_warehouse.retaining_ring", "InternalSnapRing", {"size": "25"}),
    _family("key.parallel", "power_transmission", "Parallel shaft key", "Parallel key sized from the shaft diameter.",
            "bd_warehouse.shaft_key", "ShaftKey", {"shaft_diameter": 20.0, "length": 30.0}),
    _family("gear.spur", "power_transmission", "Involute spur gear", "Spur gear by module and tooth count.",
            "bd_warehouse.gear", "SpurGear", {"module": 2.0, "tooth_count": 20, "pressure_angle": 20.0, "thickness": 10.0}),
    _family("gear.helical", "power_transmission", "Involute helical gear", "Helical gear by module, tooth count and helix angle.",
            "bd_warehouse.gear", "HelicalGear",
            {"module": 2.0, "tooth_count": 20, "pressure_angle": 20.0, "helix_angle": 15.0, "thickness": 10.0}),
    _family("gear.rack", "power_transmission", "Gear rack", "Straight rack by module and tooth count.",
            "bd_warehouse.gear", "RackGear", {"module": 2.0, "tooth_count": 20, "pressure_angle": 20.0, "thickness": 10.0}),
    _family("gear.worm", "power_transmission", "Worm", "Worm by module, starts and diametral quotient.",
            "bd_warehouse.gear", "Worm", {"module": 2.0, "starts": 1, "diametral_quotient": 10.0, "length": 40.0}),
    _family("sprocket.roller_chain", "power_transmission", "Roller chain sprocket", "Sprocket by tooth count. Defaults fit 1/2 inch pitch chain.",
            "bd_warehouse.sprocket", "Sprocket", {"num_teeth": 16}),
    _family("flange.weld_neck", "piping", "Weld neck flange", "Pipe flange by nominal pipe size and pressure class.",
            "bd_warehouse.flange", "WeldNeckFlange", {"nps": "2", "flange_class": 150}),
    _family("flange.blind", "piping", "Blind flange", "Blind pipe flange by nominal pipe size and pressure class.",
            "bd_warehouse.flange", "BlindFlange", {"nps": "2", "flange_class": 150}),
)
_CATALOG = (*_CATALOG, *_FAMILIES)
_FAMILY_IDS = {part["id"] for part in _FAMILIES}
_BY_ID = {part["id"]: part for part in _CATALOG}


def list_parts() -> list[dict[str, Any]]:
    """Return the available standard-part records without importing a CAD kernel."""
    return [get_part(entry["id"]) for entry in _CATALOG]


def get_part(id: str) -> dict[str, Any] | None:
    """Return one catalog record, or ``None`` when its ID is not registered."""
    if not isinstance(id, str):
        return None
    part = _BY_ID.get(id)
    if part is None:
        return None
    result = deepcopy(part)
    result["units"] = "mm"
    result["requires"] = "aad-quality[parts]"
    result["builder_usage"] = "Construct outside every active builder; then use add(prebuilt_part) explicitly."
    return result


def _require_pinned_backend() -> None:
    for package, expected in (("bd_warehouse", "0.4.0"), ("build123d", "0.13.0"),
                              ("cadquery-ocp-novtk", "8.0.1.1.0")):
        try:
            actual = version(package)
        except PackageNotFoundError as exc:
            raise RuntimeError("Standard-part construction requires aad-quality[parts]; catalog lookup works without it") from exc
        if actual != expected:
            raise RuntimeError(f"Standard-part backend requires {package}=={expected}; installed {actual}")
    from build123d import BuildPart
    if BuildPart._get_context(log=False) is not None:
        raise ValueError("Construct standard parts outside an active builder, then add the prebuilt geometry explicitly")


def _reject_unknown_parameters(
    part_id: str, entry: dict[str, Any], params: dict[str, Any]
) -> None:
    unknown = sorted(set(params).difference(entry["parameters"]))
    if unknown:
        names = ", ".join(unknown)
        raise TypeError(f"unsupported parameter(s) for {part_id!r}: {names}")


def _select_size(part_id: str, value: Any, choices: tuple[str, ...] | list[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        allowed = ", ".join(choices)
        raise ValueError(
            f"size for {part_id!r} must be one of {allowed}; got {value!r}"
        )
    return value


def _positive_mm(part_id: str, value: Any, name: str) -> float:
    message = f"{name} for {part_id!r} must be a positive finite number in mm"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(message)
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(message) from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(message)
    return number


def _nominal_fastener_length(part_id: str, size: str, value: Any) -> float:
    length = _positive_mm(part_id, value, "length")
    allowed = _FASTENER_LENGTHS_BY_SIZE[size]
    if length not in allowed:
        choices = ", ".join(f"{item:g}" for item in allowed)
        raise ValueError(
            f"length for {part_id!r} with size {size!r} must be a nominal "
            f"ISO 4762 length in mm; allowed: {choices}"
        )
    return length


def _hex_nut(size: str) -> Any:
    """An ISO 4032 style 1 nut from upstream's dimension table, with a plain major-diameter bore.

    The bore matches the envelope screw, so the two touch without overlapping.

    Upstream's chamfer cone stops just short of the hexagon corners and leaves
    0.003 mm edges there, which the hard gate rightly rejects. Here the chamfer
    starts at 0.95 s, the usual washer-face diameter, and so clears each corner.
    """
    from bd_warehouse.fastener import HexNut
    from build123d import Axis, Face, Plane, RegularPolygon, Solid, Vector, Wire

    row = HexNut.fastener_data.get(size)
    if row is None or not row.get("iso4032:s"):
        raise ValueError(f"nut.iso4032.hex_nut has no size {size!r}")
    across_flats, height = float(row["iso4032:s"]), float(row["iso4032:m"])
    major = float(size[1:].split("-")[0])
    hexagon = RegularPolygon(radius=across_flats / 2, side_count=6, major_radius=False)
    prism = Solid.extrude(hexagon.faces()[0], Vector(0, 0, height))

    start = 0.475 * across_flats
    corner = across_flats / math.sqrt(3) + 1.0
    rise = (corner - start) * math.tan(math.radians(30))
    outline = [(0, 0), (start, 0), (corner, rise), (corner, height - rise), (start, height), (0, height)]
    profile = Face(Wire.make_polygon([Plane.XZ.from_local_coords((r, z)) for r, z in outline], close=True))
    chamfered = prism.intersect(Solid.revolve(profile, 360, Axis.Z))[0]

    bore = Solid.make_cylinder(major / 2, height + 2, Plane((0, 0, -1)))
    nut = chamfered.cut(bore)
    if not nut.is_valid or len(nut.solids()) != 1:
        raise RuntimeError(f"The {size} hex nut did not form one valid solid")
    solid = nut.solids()[0]
    solid.label = f"ISO 4032 {size} hex nut"
    return solid


def make_part(id: str, **params: Any) -> Any:
    """Build a registered part, importing bd_warehouse only for construction.

    ``size`` and dimensions are validated before any optional geometry import.
    No user-provided text is evaluated or used to select an import or constructor.
    """
    if not isinstance(id, str):
        raise TypeError("standard part id must be a string")
    entry = _BY_ID.get(id)
    if entry is None:
        raise ValueError(f"unknown standard part id: {id!r}")
    _reject_unknown_parameters(id, entry, params)

    if id == _NUT_ID:
        _require_pinned_backend()
        return _hex_nut(params.get("size", "M6-1"))

    if id in _FAMILY_IDS:
        _require_pinned_backend()
        source = entry["source"]["constructor"]
        module_name, class_name = source.rsplit(".", 1)
        arguments = {key: spec["default"] for key, spec in entry["parameters"].items()}
        arguments.update(params)
        return getattr(importlib.import_module(module_name), class_name)(**arguments, **entry["fixed"])

    if id == _FASTENER_ID:
        size = _select_size(
            id, params.get("size", "M6-1"), tuple(_FASTENER_LENGTHS_BY_SIZE)
        )
        length = _nominal_fastener_length(id, size, params.get("length", 20))
        thread = params.get("thread", "cosmetic")
        if thread == "helical":
            raise ValueError(
                "thread='helical' fails the unchanged edge_quality and face_quality hard gates "
                "because its trimmed sweep creates sub-threshold sliver faces and edges; "
                "use thread='cosmetic'"
            )
        if thread not in ("envelope", "cosmetic"):
            raise ValueError(f"thread for {id!r} must be 'envelope' or 'cosmetic'")
        _require_pinned_backend()
        from bd_warehouse.fastener import SocketHeadCapScrew
        from build123d import Mode, Plane, Solid

        part = SocketHeadCapScrew(
            size=size,
            length=length,
            fastener_type="iso4762",
            hand="right",
            simple=True,
            mode=Mode.PRIVATE,
        )
        if thread in {"envelope", "cosmetic"}:
            major = float(size[1:].split("-")[0])
            shank = Solid.make_cylinder(major / 2, length, Plane((0, 0, -length)))
            fused = part.solids()[0].fuse(shank, *part.solids()[1:])
            if not fused.is_valid or len(fused.solids()) != 1:
                raise RuntimeError("The screw head and shank could not be united into one valid solid")
            fused.label = f"ISO 4762 {size} x {length:g} mm"
            if thread == "cosmetic":
                from .components.detailed import _record_model_metadata

                diameter, pitch = _THREAD_SIZES[size]
                _record_model_metadata({
                    "component_id": _FASTENER_ID,
                    "catalog_evidence": {
                        "source": _UPSTREAM_SOURCE["package"] + " " + _UPSTREAM_SOURCE["version"] + " fastener table",
                        "status": "source_table",
                        "url": _UPSTREAM_SOURCE["url"],
                    },
                    "dimensions_mm": {"major_diameter": diameter, "pitch": pitch, "length": length},
                    "geometry_source": "catalog_dimensions_plus_cosmetic_thread",
                    "level_of_detail": "cosmetic",
                    "representative_features": ["helical thread form omitted from the B-rep"],
                    "thread": {"designation": size.replace("-", " × "), "representation": "cosmetic", "hand": "right"},
                })
            return fused
        solids = list(part.solids())
        fused = solids[0].fuse(*solids[1:]) if len(solids) > 1 else part
        if not fused.is_valid or len(fused.solids()) != 1:
            raise RuntimeError("The full screw thread could not be united with its core into one valid solid")
        fused.label = f"ISO 4762 {size} x {length:g} mm"
        return fused

    if id == _BEARING_ID:
        size = _select_size(id, params.get("size", "M8-22-7"), _BEARING_SIZES)
        _require_pinned_backend()
        from bd_warehouse.bearing import SingleRowDeepGrooveBallBearing
        return SingleRowDeepGrooveBallBearing(size=size, bearing_type="SKT")

    if id == _THREAD_ID:
        size = _select_size(id, params.get("size", "M6-1"), tuple(_THREAD_SIZES))
        length = _positive_mm(id, params.get("length", 8), "length")
        major_diameter, pitch = _THREAD_SIZES[size]
        representation = params.get("representation", "cosmetic")
        if representation != "cosmetic":
            raise ValueError("thread.iso_metric_external supports representation='cosmetic' only")
        from build123d import Align, Cylinder

        solid = Cylinder(major_diameter / 2, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
        solid.label = f"M{major_diameter:g} x {pitch:g} external right-hand thread (cosmetic)"
        from .components.detailed import _record_model_metadata

        _record_model_metadata({
            "component_id": _THREAD_ID,
            "catalog_evidence": {
                "source": "bd_warehouse 0.4.0 ISO metric thread parameter table",
                "status": "source_table",
                "url": _UPSTREAM_SOURCE["url"],
            },
            "dimensions_mm": {"major_diameter": major_diameter, "pitch": pitch, "length": length},
            "geometry_source": "catalog_dimensions_plus_cosmetic_thread",
            "level_of_detail": "cosmetic",
            "representative_features": ["helical thread form omitted from the B-rep"],
            "thread": {
                "designation": f"M{major_diameter:g} x {pitch:g}",
                "representation": "cosmetic",
                "hand": "right",
            },
        })
        return solid

    # The ID check above and the fixed branches form a closed registry.
    raise AssertionError(f"catalog entry has no constructor: {id!r}")
