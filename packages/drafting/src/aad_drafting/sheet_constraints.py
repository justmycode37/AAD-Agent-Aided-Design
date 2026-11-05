"""Bridge serialized sheet constraints to the independent SciPy solver."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

from .constraints import ConstraintError, ConstraintSketch


def _sketch(sheet):
    sketch = ConstraintSketch()
    names: dict[str, list[str]] = {}

    def register(public, private):
        names.setdefault(public, []).append(private)

    for name, point in sheet.points.items():
        private = f"point:{name}"
        sketch.add_point(private, point.x, point.y)
        register(name, private)
    for name, entity in sheet.entities.items():
        private = f"entity:{name}"
        refs = [f"point:{point}" for point in entity.points]
        if entity.kind == "line":
            sketch.add_line(private, *refs)
            register(name, private)
        elif entity.kind == "circle":
            sketch.add_circle(private, refs[0], entity.radius)
            register(name, private)
        elif entity.kind in {"polyline", "rectangle"}:
            count = len(refs) if entity.closed else len(refs) - 1
            for index in range(count):
                alias = f"{name}.edge{index}"
                # Generated edges need a separate private namespace. Public
                # aliases may be ambiguous without blocking unrelated references.
                edge = f"edge:{name}:{index}"
                sketch.add_line(edge, refs[index], refs[(index + 1) % len(refs)])
                register(alias, edge)
    return sketch, names


def _add(sketch, names, record):
    name = record.get("name")
    references = record.get("entities")
    if not isinstance(references, (list, tuple)):
        raise ConstraintError(f"constraint {name!r}: entities must be an ordered list of names")
    private = []
    for reference in references:
        candidates = names.get(reference, []) if isinstance(reference, str) else []
        if len(candidates) != 1:
            reason = "ambiguous" if candidates else "unknown or unsupported"
            raise ConstraintError(f"constraint {name!r}: {reason} sheet reference {reference!r}")
        private.append(candidates[0])
    constraint = sketch.add_constraint(
        f"constraint:{name}", record.get("kind"), private,
        value=record.get("value"), **record.get("options", {}),
    )
    if "snapshot" in record:
        constraint.snapshot = copy.deepcopy(record["snapshot"])
    if "branch" in record:
        constraint.branch = record["branch"]
    return constraint


def _load(sheet):
    sketch, names = _sketch(sheet)
    seen = set()
    for record in sheet.constraints:
        name = record.get("name")
        if not isinstance(name, str) or not name.strip() or name in seen:
            raise ConstraintError(f"invalid or duplicate sheet constraint name {name!r}")
        seen.add(name)
        _add(sketch, names, record)
    return sketch, names


def add_constraint(sheet, name, kind, entities, value=None, **options) -> dict[str, Any]:
    from .model2d import _json_clone

    if not isinstance(name, str) or not name.strip():
        raise ConstraintError("constraint name must be a non-empty string")
    if any(record.get("name") == name for record in sheet.constraints):
        raise ConstraintError(f"duplicate sheet constraint name {name!r}")
    sketch, names = _load(sheet)
    record = {"name": name, "kind": kind, "entities": entities, "value": value, "options": options}
    constraint = _add(sketch, names, record)
    record.update(kind=constraint.kind, value=constraint.value,
                  snapshot=constraint.snapshot, branch=constraint.branch)
    record = _json_clone(record, f"constraint {name!r}")
    sheet.constraints.append(record)
    sheet.history.append({"operation": "constraint", "constraint": copy.deepcopy(record)})
    return record


def solve(sheet, tolerance=1e-6, inplace=True):
    from .model2d import Point2D

    if not isinstance(inplace, bool):
        raise ValueError("inplace must be a boolean")
    sketch, _ = _load(sheet)
    result = sketch.solve(tolerance=tolerance, inplace=False)

    def public(name):
        for prefix in ("point:", "entity:", "constraint:"):
            if name.startswith(prefix):
                return name[len(prefix):]
        return name

    result = replace(
        result,
        points={public(name): value for name, value in result.points.items()},
        radii={public(name): value for name, value in result.radii.items()},
        constraints={public(name): value for name, value in result.constraints.items()},
        conflicting_constraints=[public(name) for name in result.conflicting_constraints],
        redundant_constraints=[public(name) for name in result.redundant_constraints],
        free_variables=[public(name) for name in result.free_variables],
    )
    if result.ok and inplace:
        sheet.points.update({name: Point2D(name, *xy) for name, xy in result.points.items()})
        for name, radius in result.radii.items():
            sheet.entities[name].radius = radius
        sheet.history.append({"operation": "solve", "tolerance": tolerance,
                              "constraints": copy.deepcopy(sheet.constraints), "result": result.to_dict()})
    return result
