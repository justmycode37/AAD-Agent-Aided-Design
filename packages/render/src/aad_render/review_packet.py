"""Build the bounded, geometry-only render plan for an external reviewer."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .agent_render import prepare_render
from .render_limits import MAX_REVIEW_PACKET_CASES, MAX_REVIEW_PACKET_PARTS

_ORTHOGRAPHIC = ("front", "back", "left", "right", "top", "bottom")
_PLANE_AXIS = {"XY": 2, "XZ": 1, "YZ": 0}
_SAFE_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def prepare_review_packet(ctx: Any) -> dict[str, Any]:
    """Prepare review images and measured component records without source text.

    The returned ``renders`` contain only tessellated geometry and view data.
    Request text and check results are revision data and are added by the
    platform operation, which owns the revision files and packet directory.
    """
    shape = getattr(ctx, "shape", None)
    if shape is None:
        raise ValueError("review packets require a 3D revision with a shape")

    components = _components(ctx, shape)
    parts = [_part_record(index, item, str(getattr(ctx, "units", "model units")))
             for index, item in enumerate(components, 1)]
    if not parts:
        raise ValueError("review packet could not measure any parts")

    # Largest volume components are the most useful targets for the bounded
    # component-centered sections and isolated views. Keep original order in
    # the part list so names match the assembly record.
    major_indexes = sorted(
        range(len(components)),
        key=lambda index: (-float(parts[index]["volume"]), index),
    )[:MAX_REVIEW_PACKET_PARTS]

    low, high = _bounds(shape)
    center = tuple((low[axis] + high[axis]) / 2.0 for axis in range(3))
    cases: list[dict[str, Any]] = []

    def add_case(
        case_id: str,
        purpose: str,
        request: dict[str, Any],
        *,
        render_ctx: Any = ctx,
        **details: Any,
    ) -> None:
        if len(cases) >= MAX_REVIEW_PACKET_CASES:
            raise ValueError("review packet view plan exceeds its case limit")
        if not _SAFE_CASE_ID.fullmatch(case_id):
            raise ValueError("review packet generated an invalid image case ID")
        prepared = prepare_render(render_ctx, request)
        render = prepared.get("render")
        if not isinstance(render, dict):
            raise ValueError("render preparation returned no mesh data for the review packet")
        views = request["views"]
        cases.append({
            "id": case_id,
            "purpose": purpose,
            "images": [f"review-{case_id}-{view}.png" for view in views],
            "render": render,
            **details,
        })

    add_case(
        "overview",
        "Standard orthographic views of the complete assembly.",
        {"views": [*_ORTHOGRAPHIC, "iso"], "size": 512, "style": "shaded", "dimensions": "none"},
    )

    for plane, axis in _PLANE_AXIS.items():
        add_case(
            f"section-assembly-{plane.lower()}",
            "Section through the complete assembly bounding-box center.",
            {"views": ["iso"], "section": {"plane": plane, "offset": center[axis]},
             "size": 512, "style": "shaded", "dimensions": "none"},
            section={"plane": plane, "offset": center[axis], "center": list(center)},
        )

    selected_ids = {parts[index]["id"] for index in major_indexes}
    for index in major_indexes:
        part = parts[index]
        size = part["size"]
        axis = max(range(3), key=lambda value: (size[value], -value))
        plane = {0: "YZ", 1: "XZ", 2: "XY"}[axis]
        component_center = part["center"]
        offset = component_center[axis]
        add_case(
            f"section-part-{index + 1:02d}",
            "Section through this major part's bounding-box center, viewed in the complete assembly.",
            {"views": ["iso"], "section": {"plane": plane, "offset": offset},
             "size": 512, "style": "shaded", "dimensions": "none"},
            part_id=part["id"],
            section={"plane": plane, "offset": offset, "center": list(component_center)},
        )

    for index in major_indexes:
        component = components[index]
        isolated_ctx = copy.copy(ctx)
        isolated_ctx.shape = component["shape"]
        part = parts[index]
        add_case(
            f"isolated-part-{index + 1:02d}",
            "Isolated geometry for this major part.",
            {"views": ["iso"], "size": 512, "style": "shaded", "dimensions": "none"},
            render_ctx=isolated_ctx,
            part_id=part["id"],
        )

    omitted = [part["id"] for part in parts if part["id"] not in selected_ids]
    return {
        "renders": cases,
        "parts": parts,
        "coverage": {
            "component_count": len(parts),
            "included_component_ids": [parts[index]["id"] for index in major_indexes],
            "omitted_component_ids": omitted,
            "omission_reason": (
                f"The packet includes at most {MAX_REVIEW_PACKET_PARTS} major components for "
                "part-centered sections and isolated views."
                if omitted else None
            ),
        },
    }


def _components(ctx: Any, shape: Any) -> list[dict[str, Any]]:
    meta = getattr(ctx, "meta", None)
    assembly = meta.get("assembly") if isinstance(meta, Mapping) else None
    names = assembly.get("components") if isinstance(assembly, Mapping) else None
    try:
        children = list(shape)
    except TypeError:
        children = []
    if children and isinstance(names, list) and len(names) == len(children):
        return [
            {"shape": child, "name": str(name), "part_number": getattr(child, "part_number", None)}
            for name, child in zip(names, children, strict=True)
        ]
    if children and getattr(ctx, "kind", "part") == "assembly":
        return [
            {
                "shape": child,
                "name": str(getattr(child, "label", "") or f"part-{index:02d}"),
                "part_number": getattr(child, "part_number", None),
            }
            for index, child in enumerate(children, 1)
        ]

    meta_name = meta.get("name") if isinstance(meta, Mapping) else None
    name = str(meta_name or getattr(shape, "label", "") or getattr(getattr(ctx, "project_dir", None), "name", "part"))
    return [{"shape": shape, "name": name, "part_number": None}]


def _part_record(index: int, component: dict[str, Any], units: str) -> dict[str, Any]:
    shape = component["shape"]
    low, high = _bounds(shape)
    size = [high[axis] - low[axis] for axis in range(3)]
    volume = float(shape.volume)
    if not math.isfinite(volume) or volume < 0 or not all(math.isfinite(value) for value in size):
        raise ValueError("review packet measured non-finite part dimensions")
    center = [(low[axis] + high[axis]) / 2.0 for axis in range(3)]
    return {
        "id": f"part-{index:03d}",
        "name": component["name"][:256],
        "part_number": str(component["part_number"] or component["name"])[:128],
        "size": size,
        "center": center,
        "bbox": {"min": list(low), "max": list(high)},
        "volume": volume,
        "units": units,
    }


def _bounds(shape: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    bbox = shape.bounding_box()
    low = _vector(bbox.min)
    high = _vector(bbox.max)
    if any(low[axis] > high[axis] for axis in range(3)):
        raise ValueError("review packet encountered an invalid part bounding box")
    return low, high


def _vector(value: Any) -> tuple[float, float, float]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result = tuple(float(value[index]) for index in range(3))
    else:
        result = tuple(float(getattr(value, axis)) for axis in ("X", "Y", "Z"))
    if not all(math.isfinite(item) for item in result):
        raise ValueError("review packet encountered a non-finite bounding box")
    return result  # type: ignore[return-value]
