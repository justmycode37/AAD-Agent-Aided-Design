"""Validate agent-controlled, non-destructive viewer settings."""

from __future__ import annotations

import math
import re
import json
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
    value = _object(value, {"isolate", "hide", "style", "projection", "section", "explode", "grid", "inside", "materials", "environment"}, "presentation")
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
    if "materials" in value:
        rows = value["materials"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
            raise ValueError("materials must contain 1 to 256 part material overrides")
        normalized, assigned = [], set()
        for row in rows:
            row = _object(row, {"parts", "color", "metalness", "roughness", "opacity"}, "material")
            selectors = row.get("parts")
            if not isinstance(selectors, list) or not 1 <= len(selectors) <= 256:
                raise ValueError("material.parts must contain 1 to 256 part names or paths")
            paths = normalize_presentation({"isolate": selectors}, parts)["isolate"]
            if assigned.intersection(paths):
                raise ValueError("A part may have only one material override")
            assigned.update(paths)
            item = {"parts": paths}
            if "color" in row:
                color = row["color"]
                if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                    raise ValueError("material.color must be a #RRGGBB color")
                item["color"] = color
            for key in ("metalness", "roughness", "opacity"):
                if key in row:
                    number = _number(row[key], f"material.{key}")
                    if not 0 <= number <= 1:
                        raise ValueError(f"material.{key} must be between 0 and 1")
                    item[key] = number
            if len(item) == 1:
                raise ValueError("Each material needs at least one display property")
            normalized.append(item)
        result["materials"] = normalized
    if "environment" in value:
        if value["environment"] != "studio":
            raise ValueError('environment must be "studio"')
        result["environment"] = "studio"
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
    if "inside" in value:
        inside = _object(value["inside"], {"hide", "section"}, "inside")
        if not inside:
            raise ValueError("inside needs cover parts to hide or a section through an existing cavity")
        prepared = normalize_presentation(inside, parts)
        if "hide" in prepared and not {p["path"] for p in parts} - set(prepared["hide"]):
            raise ValueError("inside must leave at least one interior part visible")
        result["inside"] = prepared
    return result


def normalize_views(value: Any, parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate named, reproducible views of the same original scene."""
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError("views must be an array of at most 24 named views")
    result, seen, labels = [], {"original"}, {"original"}
    for item in value:
        item = _object(item, {"id", "label", "description", "view", "presentation", "analysis"}, "view preset")
        key, label = item.get("id"), item.get("label")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or key in seen:
            raise ValueError("View IDs must be unique; 'original' is reserved")
        if not isinstance(label, str) or not label.strip() or len(label) > 48 or label.strip().casefold() in labels:
            raise ValueError("Each view needs a label of 1 to 48 characters")
        seen.add(key)
        labels.add(label.strip().casefold())
        prepared = {"id": key, "label": label, "presentation": normalize_presentation(item.get("presentation", {}), parts)}
        if "inside" in prepared["presentation"]:
            raise ValueError("Named views use hide or section directly, not the legacy inside field")
        if "description" in item:
            if not isinstance(item["description"], str) or len(item["description"]) > 240:
                raise ValueError("View descriptions must be at most 240 characters")
            prepared["description"] = item["description"]
        if "view" in item:
            if item["view"] not in VIEWS:
                raise ValueError("Unknown camera view")
            prepared["view"] = item["view"]
        if "analysis" in item:
            analysis = _object(item["analysis"], {"quantity", "units", "source", "values", "range"}, "analysis")
            normalized = {}
            for field, maximum in (("quantity", 80), ("units", 24), ("source", 512)):
                text = analysis.get(field)
                if not isinstance(text, str) or not text.strip() or len(text) > maximum:
                    raise ValueError(f"analysis.{field} must be a non-empty string of at most {maximum} characters")
                normalized[field] = text
            rows = analysis.get("values")
            if not isinstance(rows, list) or not 1 <= len(rows) <= 2048:
                raise ValueError("analysis.values needs 1 to 2048 sourced part values")
            values, assigned = [], set()
            for row in rows:
                row = _object(row, {"part", "value"}, "analysis value")
                paths = normalize_presentation({"isolate": [row.get("part")]}, parts)["isolate"]
                number = _number(row.get("value"), "analysis value")
                for path in paths:
                    if path in assigned:
                        raise ValueError("Each analysis part may have only one value")
                    assigned.add(path)
                    values.append({"part": path, "value": number})
            limits = analysis.get("range", [min(r["value"] for r in values), max(r["value"] for r in values)])
            if not isinstance(limits, list) or len(limits) != 2:
                raise ValueError("analysis.range needs two finite limits")
            low, high = [_number(n, "analysis range") for n in limits]
            if low > high or not math.isfinite(high - low) or any(not low <= row["value"] <= high for row in values):
                raise ValueError("analysis.range must contain every supplied value")
            normalized.update(values=values, range=[low, high])
            prepared["analysis"] = normalized
        result.append(prepared)
    if len(json.dumps(result, ensure_ascii=True).encode("utf-8")) > 64 * 1024:
        raise ValueError("View metadata exceeds 64 KiB; provide fewer views or analysis values")
    return result
