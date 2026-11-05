"""Closed catalog of real, buyable engineering parts.

Agents invent the custom geometry. The elemental parts inside it, such as
screws, bearings, transistors, motors and connectors, come from these records so a bill of
materials names things that can be bought in the stated size.

Lookups are kernel-free. Each record carries an ``evidence`` status, and that
status is the claim: a record is never proof of current stock or a price.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json
import math
from typing import Any

from ..schema import COMPONENT_ITEM_LIMIT as _ITEM_LIMIT
from . import electromechanical, electronics, mechanical

__all__ = ["list_components", "get_component", "check_items", "make_envelope", "catalog_index", "INDEX_ID", "EVIDENCE_STATUSES"]

INDEX_ID = "component.index"
EVIDENCE_STATUSES = {
    "web_checked": "A manufacturer or supplier page naming this part was read on the recorded date. Stock and price change.",
    "symbol_library": "The part number appears in KiCad's official symbol library. This shows it exists; dimensions are typical outlines.",
    "footprint_library": "The part number or series appears in KiCad's official footprint library. This shows it exists; confirm the mating part.",
    "source_table": "Dimensions come from the bd_warehouse 0.4.0 data tables, not a licensed copy of the standard.",
    "standard_series": "A commonly stocked standard or generic series entered from trade knowledge. Confirm the exact size with a supplier.",
    "author_recall": "Entered from trade knowledge and not independently checked. Confirm against a datasheet before relying on it.",
}
_SOURCE_TABLE_EVIDENCE = {"status": "source_table", "source": "bd_warehouse 0.4.0 data tables (Apache-2.0)",
                          "url": "https://github.com/gumyr/bd_warehouse/tree/v0.4.0"}
_LIMITATION = ("Record values are nominal; the exact datasheet or supplier drawing governs the design. "
               "The catalog does not prove stock, price, lifecycle, or fit for a duty.")
_E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)
_E24 = (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1)
_E_SERIES: dict[str, tuple[float, ...]] = {
    "E6": _E12[::2],
    "E12": _E12,
    "E24": _E24,
    "E96": tuple(round(10 ** (index / 96), 2) for index in range(96)),
}
# Options that define what is bought, unlike a defaulted package or tolerance.
_REQUIRED_OPTIONS = {"length_mm", "length_in", "resistance_ohm", "capacitance_f", "inductance_h", "bore_mm", "teeth", "voltage_v", "width_mm", "thread", "current_a"}
_NON_SELECTION_KEYS = {"id", "qty", "ref", "note", "custom", "reason"}


def _load_json(name: str) -> Any:
    return json.loads(files(__name__).joinpath(name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict[str, Any]]:
    evidence = _load_json("component_evidence.json")
    records: dict[str, dict[str, Any]] = {}
    for record in _load_json("standard_tables.json"):
        record["evidence"] = dict(_SOURCE_TABLE_EVIDENCE)
        records[record["id"]] = record
    for module in (mechanical, electronics, electromechanical):
        for record in module.records():
            if record["id"] in records:
                raise ValueError(f"duplicate component id {record['id']}")
            status = "standard_series" if record.get("source_type") in {"standard_series", "generic_series"} else "author_recall"
            record["evidence"] = {"status": status, "source": "AAD hand-entered catalog"}
            records[record["id"]] = record
    for record in records.values():
        if record["id"] in evidence:
            record["evidence"] = evidence[record["id"]]
        record["limitations"] = _LIMITATION
    return records


def list_components() -> list[dict[str, Any]]:
    """Return the shared records. Callers must not mutate them."""
    return [_index_record(), *_catalog().values()]


def catalog_index() -> dict[str, Any]:
    return deepcopy(_index_record())


def get_component(component_id: str) -> dict[str, Any] | None:
    if component_id == INDEX_ID:
        return catalog_index()
    record = _catalog().get(component_id)
    return deepcopy(record) if record else None


@lru_cache(maxsize=1)
def _index_record() -> dict[str, Any]:
    categories: dict[str, Counter[str]] = {}
    for record in _catalog().values():
        categories.setdefault(record["category"], Counter())[record["family"]] += 1
    evidence = Counter(record["evidence"]["status"] for record in _catalog().values())
    return {
        "id": INDEX_ID,
        "title": "Component catalog index",
        "category": "index",
        "family": "index",
        "tags": ["index", "categories", "catalog", "families"],
        "summary": f"Index of {len(_catalog())} real part records by category and family, plus evidence status counts.",
        "total_records": len(_catalog()),
        "categories": {name: dict(sorted(families.items())) for name, families in sorted(categories.items())},
        "evidence_counts": dict(sorted(evidence.items())),
        "evidence_statuses": EVIDENCE_STATUSES,
        "how_to_use": "Search by function plus size (for example 'M3 socket head', '608 bearing', 'logic mosfet', 'nema 17'). "
                      "Then get one id, and validate your bill of materials with action='check'.",
    }


def _nearest(values: list[float], value: float) -> list[float]:
    return sorted(sorted(values, key=lambda item: abs(item - value))[:2])


def _on_series(value: float, spec: dict[str, Any]) -> tuple[bool, list[float]]:
    if value in spec.get("also", ()):
        return True, []
    if value <= 0 or not math.isfinite(value) or not spec["min"] * (1 - 1e-9) <= value <= spec["max"] * (1 + 1e-9):
        return False, []
    exponent = math.floor(math.log10(value))
    mantissa = value / 10 ** exponent
    candidates = sorted({item for name in spec["e_series"] for item in _E_SERIES[name]} | {10.0})
    if any(abs(mantissa - item) <= 1e-6 * item for item in candidates):
        return True, []
    best = sorted(candidates, key=lambda item: abs(item - mantissa))[:2]
    return False, sorted(float(f"{item * 10 ** exponent:.6g}") for item in best)


def _check_selection(record: dict[str, Any], selection: dict[str, Any], *, complete: bool = True) -> list[str]:
    problems: list[str] = []
    choices = record.get("choices", {})
    for key, value in selection.items():
        if key not in choices:
            problems.append(f"{key!r} is not a selectable option for {record['id']}; options: {sorted(choices) or 'none'}.")
            continue
        allowed = choices[key]
        if isinstance(value, bool):
            problems.append(f"{key} must be a value from its choices, not {value}.")
            continue
        if isinstance(value, int) and abs(value) > 1e300:
            problems.append(f"{key} is out of range.")
            continue
        if isinstance(allowed, dict) and "e_series" not in allowed:
            # A cut-to-length part is bought in any length within the range.
            if not isinstance(value, (int, float)) or not allowed["min"] <= value <= allowed["max"]:
                problems.append(f"{key} must be a number from {allowed['min']:g} to {allowed['max']:g}.")
        elif isinstance(allowed, dict):
            if not isinstance(value, (int, float)):
                problems.append(f"{key} must be a number.")
                continue
            valid, nearest = _on_series(float(value), allowed)
            if not valid:
                series = "/".join(allowed["e_series"])
                hint = f"; nearest {series} values: {nearest}" if nearest else f"; range {allowed['min']:g} to {allowed['max']:g}"
                problems.append(f"{key}={value:g} is not a {series} value{hint}.")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = [item for item in allowed if isinstance(item, (int, float))]
            if not any(abs(float(value) - item) < 1e-9 for item in numeric):
                problems.append(f"{key}={value:g} is not stocked for {record['id']}; nearest: {_nearest(numeric, float(value)) if numeric else allowed}.")
        elif value not in allowed:
            problems.append(f"{key}={value!r} is not available for {record['id']}; choices: {allowed}.")
    for key, allowed in choices.items() if complete else ():
        if key not in selection and key in _REQUIRED_OPTIONS and not isinstance(allowed, str):
            problems.append(f"{record['id']} needs {key} selected to name a buyable part.")
    return problems


def _qty_problem(item: dict[str, Any]) -> list[str]:
    qty = item.get("qty", 1)
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        return [f"qty must be a positive whole number, not {qty!r}."]
    return []


def check_items(items: Any) -> dict[str, Any]:
    """Validate a bill of materials against the catalog.

    Each item is ``{"id": <component id>, <option>: <value>, "qty": n}``, or
    ``{"custom": <name>, "reason": <why>}`` for a part designed in this project.
    """
    if not isinstance(items, list) or not items or len(items) > _ITEM_LIMIT:
        raise ValueError(f"items must be a non-empty list of at most {_ITEM_LIMIT} entries")
    results = []
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"items[{position}] must be a mapping")
        if "custom" in item and "id" in item:
            results.append({"id": item["id"], "ok": False, "status": "invalid", "problems": ["An item needs either id or custom, not both."]})
            continue
        if "custom" in item:
            name, reason = item.get("custom"), item.get("reason")
            ok = isinstance(name, str) and bool(name.strip()) and isinstance(reason, str) and bool(reason.strip())
            problems = ([] if ok else ["A custom part needs a name and a reason: it must be something this project designs, not a purchased part."]) + _qty_problem(item)
            results.append({"custom": name, "ok": not problems, "status": "custom" if not problems else "invalid", "problems": problems})
            continue
        component_id = item.get("id")
        if not isinstance(component_id, str):
            results.append({"id": component_id, "ok": False, "status": "invalid", "problems": ["The item needs an id, or custom plus reason."]})
            continue
        record = _catalog().get(component_id)
        if record is None:
            results.append({"id": component_id, "ok": False, "status": "unknown", "problems": [f"{component_id!r} is not in the catalog. Search for a real alternative, or get a datasheet and report it as unverified."]})
            continue
        problems = _check_selection(record, {key: value for key, value in item.items() if key not in _NON_SELECTION_KEYS}) + _qty_problem(item)
        results.append({"id": component_id, "ok": not problems, "status": "valid" if not problems else "invalid", "title": record["title"], "evidence": record["evidence"]["status"], "problems": problems})
    return {"ok": all(result["ok"] for result in results), "items": results}


def make_envelope(component_id: str, *, units: str = "mm", **selection: Any) -> Any:
    """Build a simplified nominal solid for fit and clearance checks.

    Geometry is an outline, not the real part. Axis along Z. Screws have the
    under-head plane at Z=0 with the shank below it; countersunk screws and set
    screws measure length from the top. Everything else starts at Z=0. A motor
    with shaft data has its mounting face at Z=0; gearmotors and servos are
    plain box outlines. Pass units="in" in an inch project, because project
    units do not rescale coordinates.
    """
    if units not in {"mm", "in"}:
        raise ValueError("units must be 'mm' or 'in'")
    solid = _envelope_mm(component_id, selection)
    if units == "in":
        from build123d import scale
        return scale(solid, 1 / 25.4)
    return solid


def _envelope_mm(component_id: str, selection: dict[str, Any]) -> Any:
    record = _catalog().get(component_id)
    if record is None:
        raise KeyError(f"Unknown component {component_id!r}")
    package = selection.pop("package", None)
    # A resistor's value does not change its outline, so only given options are checked.
    problems = _check_selection(record, selection, complete=False)
    envelope = dict(record.get("envelope") or {})
    if package is not None:
        outline = record.get("packages", {}).get(package)
        if outline is None:
            raise ValueError(f"{component_id} has no package {package!r}; choices: {sorted(record.get('packages', {}))}")
        envelope = {"shape": "box", **outline}
    if problems:
        raise ValueError("; ".join(problems))
    if not envelope:
        raise ValueError(f"{component_id} has no envelope data; model it from its datasheet.")
    from build123d import Cylinder, Align, Box, RegularPolygon, Pos, extrude

    length = selection.get("length_mm")
    if "length_in" in selection:
        length = selection["length_in"] * 25.4
    bottom = (Align.CENTER, Align.CENTER, Align.MIN)
    shape = envelope["shape"]
    if shape == "box":
        z = envelope.get("z") or length
        if z is None:
            raise ValueError(f"{component_id} needs length_mm")
        return Box(envelope["x"], envelope["y"], z, align=bottom)
    if shape == "cylinder":
        return Cylinder(envelope["d"] / 2, envelope["h"], align=bottom)
    if shape == "pin":
        if length is None:
            raise ValueError(f"{component_id} needs length_mm")
        return Cylinder(envelope["d"] / 2, length, align=bottom)
    if shape == "ring":
        height = envelope.get("h") or length
        if height is None:
            raise ValueError(f"{component_id} needs length_mm")
        solid = Cylinder(envelope["D"] / 2, height, align=bottom)
        return solid - Cylinder(envelope["d"] / 2, height + 1, align=(Align.CENTER, Align.CENTER, Align.CENTER)).moved(Pos(0, 0, height / 2)) if envelope.get("d") else solid
    if shape == "hex":
        height = envelope.get("h") or length
        if height is None:
            raise ValueError(f"{component_id} needs length_mm")
        # RegularPolygon's major radius runs to the corners.
        solid = extrude(RegularPolygon(envelope["af"] / math.sqrt(3), 6), amount=height)
        return solid - Cylinder(envelope["bore"] / 2, height + 1).moved(Pos(0, 0, height / 2)) if envelope.get("bore") else solid
    if shape == "screw":
        if length is None:
            raise ValueError(f"{component_id} needs a length")
        head_h = envelope["head_h"]
        if envelope.get("length_includes_head"):
            shank = max(length - head_h, 0.1)
        else:
            shank = length
        d = envelope["thread_d"]
        result = Cylinder(d / 2, shank, align=(Align.CENTER, Align.CENTER, Align.MAX))
        if envelope.get("shoulder_d"):
            # A shoulder screw's nominal length is its shoulder; the thread continues below.
            thread_length = record["dimensions_mm"]["thread_length"]
            result = Cylinder(envelope["shoulder_d"] / 2, shank, align=(Align.CENTER, Align.CENTER, Align.MAX))
            result = result + Cylinder(d / 2, thread_length, align=(Align.CENTER, Align.CENTER, Align.MAX)).moved(Pos(0, 0, -shank))
        if head_h:
            result = result + Cylinder(envelope["head_d"] / 2, head_h, align=bottom)
        return result
    if shape == "motor":
        z = envelope["z"]
        body = Cylinder(envelope["d"] / 2, z, align=(Align.CENTER, Align.CENTER, Align.MAX)) if "d" in envelope else Box(envelope["x"], envelope["y"], z, align=(Align.CENTER, Align.CENTER, Align.MAX))
        body = body + Cylinder(envelope["shaft_d"] / 2, envelope["shaft_l"], align=bottom).moved(Pos(0, record.get("dimensions_mm", {}).get("shaft_offset", 0), 0))
        if envelope.get("pilot_d"):
            body = body + Cylinder(envelope["pilot_d"] / 2, envelope["pilot_h"], align=bottom)
        return body
    raise ValueError(f"Unsupported envelope shape {shape!r}")
