"""Small, on-demand adapters for selected ``bd_warehouse`` parts.

Catalog reads are static and do not import build123d, OCP, or bd_warehouse.
Part construction is deliberately limited to the closed registry below.
"""
from __future__ import annotations

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
        },
        "fixed": {"fastener_type": "iso4762", "hand": "right", "simple": False, "mode": "private"},
        "example": (
            "from aiad_quality.standard_parts import make_part\n"
            "screw = make_part(\n"
            "    'fastener.iso4762.socket_head_cap_screw', size='M6-1', length=20\n"
            ")"
        ),
        "limitations": [
            "Uses full helical thread geometry: upstream simple=True models only "
            "a minor-diameter core and understates the thread clearance envelope. "
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
            "from aiad_quality.standard_parts import make_part\n"
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
        "title": "ISO metric external thread form",
        "summary": "External right-hand helical thread; default M6-1 x 8 mm.",
        "source": {
            **_UPSTREAM_SOURCE,
            "constructor": "bd_warehouse.thread.IsoThread",
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
        },
        "fixed": {
            "external": True,
            "hand": "right",
            "end_finishes": ["square", "square"],
            "simple": False,
            "mode": "private",
        },
        "example": (
            "from aiad_quality.standard_parts import make_part\n"
            "thread = make_part(\n"
            "    'thread.iso_metric_external', size='M6-1', length=8\n"
            ")"
        ),
        "limitations": [
            "Returns the thread helix only, with no shaft or mating hole; nominal "
            "geometry is not proof of fit or load."
        ],
    },
)

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
    result["requires"] = "aiad-quality[parts]"
    result["builder_usage"] = "Construct outside every active builder; then use add(prebuilt_part) explicitly."
    return result


def _require_pinned_backend() -> None:
    for package, expected in (("bd_warehouse", "0.4.0"), ("build123d", "0.13.0"),
                              ("cadquery-ocp-novtk", "8.0.1.1.0")):
        try:
            actual = version(package)
        except PackageNotFoundError as exc:
            raise RuntimeError("Standard-part construction requires aiad-quality[parts]; catalog lookup works without it") from exc
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

    if id == _FASTENER_ID:
        size = _select_size(
            id, params.get("size", "M6-1"), tuple(_FASTENER_LENGTHS_BY_SIZE)
        )
        length = _nominal_fastener_length(id, size, params.get("length", 20))
        _require_pinned_backend()
        from bd_warehouse.fastener import SocketHeadCapScrew
        from build123d import Mode

        part = SocketHeadCapScrew(
            size=size,
            length=length,
            fastener_type="iso4762",
            hand="right",
            simple=False,
            mode=Mode.PRIVATE,
        )
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
        _require_pinned_backend()
        from bd_warehouse.thread import IsoThread
        from build123d import Mode

        return IsoThread(
            major_diameter=major_diameter,
            pitch=pitch,
            length=length,
            external=True,
            hand="right",
            end_finishes=("square", "square"),
            simple=False,
            mode=Mode.PRIVATE,
        )

    # The ID check above and the fixed branches form a closed registry.
    raise AssertionError(f"catalog entry has no constructor: {id!r}")
