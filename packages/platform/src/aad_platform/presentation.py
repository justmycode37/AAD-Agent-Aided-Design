"""Validate agent-controlled, non-destructive viewer settings."""

from __future__ import annotations

import math
from typing import Any

VIEWS = ("iso", "front", "rear", "left", "right", "top", "bottom")
STYLES = ("shaded-edges", "shaded", "wireframe", "transparent")


def _object(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() - fields:
        raise ValueError(f"{name} must be an object with only {', '.join(sorted(fields))}")
    return value


def _number(value: Any, name: str) -> float:
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be a finite number")
    return value


def normalize_presentation(value: Any, parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve unique part names/group paths to existing leaf paths; never guess."""
    value = _object(value, {"isolate", "hide", "style", "projection", "section", "explode", "grid"}, "presentation")
    result: dict[str, Any] = {}
    for key in ("isolate", "hide"):
        if key not in value:
            continue
        selectors = value[key]
        if not isinstance(selectors, list) or not 1 <= len(selectors) <= 256:
            raise ValueError(f"{key} must contain 1 to 256 part names or paths")
        resolved = []
        for selector in selectors:
            if not isinstance(selector, str) or not selector or len(selector) > 256:
                raise ValueError(f"{key} contains an invalid part selector")
            if selector.startswith("/"):
                matches = [p["path"] for p in parts if p["path"] == selector or p["path"].startswith(selector.rstrip("/") + "/")]
            else:
                matches = [p["path"] for p in parts if p.get("name") == selector]
                if len(matches) > 1:
                    raise ValueError(f"Part name {selector!r} is ambiguous; use a full part path")
            if not matches:
                raise ValueError(f"Unknown part {selector!r}; use a name or path from scene.parts")
            resolved.extend(matches)
        result[key] = list(dict.fromkeys(resolved))
    for key, choices in (("style", STYLES), ("projection", ("orthographic", "perspective"))):
        if key in value:
            if value[key] not in choices:
                raise ValueError(f"{key} must be one of {', '.join(choices)}")
            result[key] = value[key]
    for key in ("explode", "grid"):
        if key in value:
            if not isinstance(value[key], bool):
                raise ValueError(f"{key} must be true or false")
            result[key] = value[key]
    if "section" in value:
        section = _object(value["section"], {"planes", "intersection"}, "section")
        planes = section.get("planes")
        if not isinstance(planes, list) or not 1 <= len(planes) <= 3:
            raise ValueError("section.planes must contain 1 to 3 planes")
        normalized = []
        for plane in planes:
            plane = _object(plane, {"plane", "normal", "offset", "flipped"}, "section plane")
            if ("plane" in plane) == ("normal" in plane):
                raise ValueError("Each section plane needs either plane or normal")
            normal = plane.get("normal")
            if "plane" in plane:
                if plane["plane"] not in ("XY", "XZ", "YZ"):
                    raise ValueError("plane must be XY, XZ, or YZ")
                item = {"plane": plane["plane"]}
            else:
                if not isinstance(normal, list) or len(normal) != 3:
                    raise ValueError("normal must have three finite numbers")
                normal = [_number(n, "normal") for n in normal]
                length = math.hypot(*normal)
                if not math.isfinite(length) or length == 0:
                    raise ValueError("normal must have a finite non-zero length")
                item = {"normal": [n / length for n in normal]}
            item["offset"] = _number(plane.get("offset", 0), "offset")
            if not isinstance(plane.get("flipped", False), bool):
                raise ValueError("flipped must be true or false")
            item["flipped"] = plane.get("flipped", False)
            normalized.append(item)
        if not isinstance(section.get("intersection", False), bool):
            raise ValueError("intersection must be true or false")
        result["section"] = {"planes": normalized, "intersection": section.get("intersection", False)}
    return result
