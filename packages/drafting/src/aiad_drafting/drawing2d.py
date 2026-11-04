"""Prepare actual XY sheet geometry for the shared drawing composer."""

from dataclasses import replace
import math

from .geometry import Bounds3D, Hole, ProjectedBounds, ProjectedView, _curve_from_edge, shape_bounds
from .model2d import _entity_to_native
from .types import DrawingView


def _profile_schedules(evidence, bounds, dimensions, native_sheet):
    from .openings2d import _rectangle, _obround
    from .sheet import _dimension_record
    from .model2d import _edge_record
    from .validation2d import _role
    rows, unknown = [], []
    holes = {hole["contour"] for hole in evidence["holes"]}

    def add_row(measured, angle, identifier, sources, edges):
        values = {"x": measured["center"][0] - bounds.xmin, "y": measured["center"][1] - bounds.ymin,
                  "width": measured["width"], "length": measured["length"], "angle": angle}
        row = {"feature": f"P{len(rows) + 1}", "kind": measured["kind"].upper()}
        ids = [None, None]
        for key, value in values.items():
            if value is None:
                row[key] = "-"
                ids.append(None)
                continue
            dimension = _dimension_record(kind="profile_parameter", value=value, axis=key,
                unit="deg" if key == "angle" else native_sheet.units,
                precision=3 if native_sheet.units == "in" and key != "angle" else 2,
                source={"type": "native_planar_feature", "entities": sources,
                        "feature": identifier, "parameter": key,
                        "datum": [bounds.xmin, bounds.ymin], "native_edges": edges})
            dimension["id"] = f"profile.{identifier}.{key}"
            dimensions.append(dimension)
            row[key] = dimension["display"]
            ids.append(dimension["id"])
        row["dimension_ids"] = ids
        rows.append(row)

    for contour in evidence["closed_contours"]:
        if not contour["valid"] or contour["id"] in holes:
            continue
        edges = contour["edges"]
        tolerance = max(1e-9, max((edge["length"] for edge in edges), default=1) * math.ulp(1.0) * 64)
        measured = _rectangle(edges, tolerance) or _obround(edges, tolerance)
        angle = None
        if measured:
            lines = [edge for edge in edges if edge["geometry"]["kind"] == "line"]
            if lines:
                longest = max(lines, key=lambda edge: edge["length"])["geometry"]
                angle = math.degrees(math.atan2(longest["end"][1] - longest["start"][1],
                                                longest["end"][0] - longest["start"][0])) % 180
        elif len(edges) == 1 and edges[0]["geometry"]["kind"] == "circle":
            circle = edges[0]["geometry"]
            measured = {"kind": "circle", "center": circle["center"],
                        "width": 2 * circle["radius"], "length": 2 * circle["radius"]}
        if not measured:
            unknown.extend(contour["source_entities"])
            continue
        add_row(measured, angle, contour["id"], contour["source_entities"], edges)
    for entity in native_sheet.entities.values():
        role = _role(native_sheet, entity)
        if role not in {"score", "engrave"} or entity.kind == "text":
            continue
        native = _entity_to_native(native_sheet, entity)
        for index, edge in enumerate(native.edges()):
            try:
                geometry = _edge_record(edge)
            except ValueError:
                unknown.append(entity.name)
                continue
            kind = geometry["kind"]
            if kind == "line":
                a, b = geometry["start"], geometry["end"]
                measured = {"kind": f"{role} line", "center": [(a[0]+b[0])/2, (a[1]+b[1])/2],
                            "width": None, "length": float(edge.length)}
                angle = math.degrees(math.atan2(b[1]-a[1], b[0]-a[0])) % 180
            elif kind == "circle":
                measured = {"kind": f"{role} circle", "center": geometry["center"],
                            "width": 2*geometry["radius"], "length": 2*geometry["radius"]}
                angle = None
            else:
                unknown.append(entity.name)
                continue
            add_row(measured, angle, f"{entity.name}.edge{index}", [entity.name],
                    [{"geometry": geometry, "length": float(edge.length)}])
    return rows, sorted(set(unknown))


def prepare(ctx, request):
    from build123d import Compound
    from .validation2d import _context_sheet, _role, measurement_evidence, checks
    from .sheet import _make_dimensions

    native_sheet, units, error = _context_sheet(ctx)
    if error:
        raise ValueError(error)
    names = tuple(view.name for view in request.views)
    if names == ("front", "top", "right", "iso"):
        request = replace(request, views=(DrawingView("top", label="PLAN"),))
    elif len(request.views) != 1 or names != ("top",) or request.views[0].kind != "orthographic":
        raise ValueError("Native 2D sheets support a single top/plan view; extrude the sheet for 3D views.")
    evidence = measurement_evidence(ctx)
    curves, cut_shapes, all_shapes, labels = [], [], [], []
    for entity in native_sheet.entities.values():
        if entity.kind == "text":
            labels.append(entity)
            continue
        native = _entity_to_native(native_sheet, entity)
        all_shapes.append(native)
        if _role(native_sheet, entity) == "cut":
            cut_shapes.append(native)
        for edge in native.edges():
            curve = _curve_from_edge(edge.wrapped, False)
            if curve:
                curves.append(replace(curve, layer=entity.layer))
    if not all_shapes:
        raise ValueError("A native 2D drawing needs geometric entities; annotation text alone has no cut profile.")
    shape = Compound(children=cut_shapes or all_shapes)
    measured = shape_bounds(shape)
    bounds = Bounds3D(measured.xmin, measured.ymin, 0, measured.xmax, measured.ymax, 0)
    visual = shape_bounds(Compound(children=all_shapes))
    from .layout import text_bounds
    from .export import text
    label_bounds = []
    for entity in labels:
        glyphs = text_bounds(text(entity.text, 0, 0, 2.5,
                                 rotation=entity.metadata.get("rotation_deg", 0)))
        point = native_sheet.points[entity.points[0]]
        factor = entity.height / 2.5
        label_bounds.append((point.x + glyphs.xmin * factor, point.y + glyphs.ymin * factor,
                             point.x + glyphs.xmax * factor, point.y + glyphs.ymax * factor))
    visual_bounds = ProjectedBounds(min([visual.xmin, *[box[0] for box in label_bounds]]),
        min([visual.ymin, *[box[1] for box in label_bounds]]),
        max([visual.xmax, *[box[2] for box in label_bounds]]),
        max([visual.ymax, *[box[3] for box in label_bounds]]))
    projection = ProjectedView("top", (0, 0, 1), (1, 0, 0), (0, 1, 0), tuple(curves),
        visual_bounds, len(curves), 0,
        {"kind": "planar", "source": "Sheet2D native XY geometry",
         "dimension_bounds": (bounds.xmin, bounds.ymin, bounds.xmax, bounds.ymax)})
    measured_holes = evidence["holes"] if evidence["status"]["complete"] else []
    measured_holes = sorted(measured_holes, key=lambda hole: (*hole["center"][:2], hole["diameter"]))
    holes = [Hole(index, hole["center"][0], hole["center"][1], hole["diameter"] / 2, (), (0, 0))
             for index, hole in enumerate(measured_holes)]
    if request.dimensions not in ("auto", "none"):
        if any(spec.kind == "overall" and (spec.axis == "z" or spec.source == "bbox.z") for spec in request.dimensions):
            raise ValueError("A native 2D sheet has no thickness dimension.")
        explicit = []
        for spec in request.dimensions:
            explicit.extend([replace(spec, axis="x"), replace(spec, axis="y")]
                if spec.kind == "overall" and spec.axis is None and spec.source is None else [spec])
        request = replace(request, dimensions=tuple(explicit))
    dimensions = _make_dimensions(request.dimensions, bounds, holes, units)
    for dimension in dimensions:
        indices = dimension.get("hole_indices", [])
        old_source = dimension["source"]
        measurement = {"overall": "maximum native cut bound - minimum native cut bound",
                       "hole_diameter": "2 * native circular cutout radius",
                       "hole_coordinate": "native cutout center coordinate - minimum native cut bound",
                       "hole_spacing": "difference between native cutout center coordinates"}[dimension["kind"]]
        dimension["source"] = {"type": "native_sheet_cut_geometry", "measurement": measurement,
            "bounds": [bounds.xmin, bounds.ymin, bounds.xmax, bounds.ymax],
            "cutouts": [measured_holes[index] for index in indices],
            "entities": [entity.name for entity in native_sheet.entities.values() if _role(native_sheet, entity) == "cut"],
            **{key: old_source[key] for key in ("from_coordinate", "to_coordinate") if key in old_source}}
        dimension["planar"] = True
        if dimension["kind"] == "hole_diameter":
            dimension["through"] = False
        if request.dimensions == "auto" and units == "in":
            from .sheet import _display_precision, _format_number
            dimension["precision"] = _display_precision(dimension["value"], 3)
            dimension["display"] = _format_number(dimension["value"], dimension["precision"])
    rows, unknown = _profile_schedules(evidence, bounds, dimensions, native_sheet) if request.dimensions == "auto" else ([], [])
    native_checks = checks(ctx)
    native_checks.append({"id": "2d.undimensioned_features", "suite": "2d", "level": "error",
        "status": "fail" if unknown or (request.dimensions == "auto" and not evidence["status"]["complete"]) else "pass",
        "message": "Some cut, score or engrave paths have no supported dimension schedule." if unknown else
                   "Automatic cut-profile dimension coverage is incomplete." if request.dimensions == "auto" and not evidence["status"]["complete"] else
                   "Every supported cut contour is dimensioned, or automatic dimensions were not requested.",
        "actual": ", ".join(unknown)})
    return {"request": request, "sheet": native_sheet, "shape": shape, "bounds": bounds, "holes": holes,
            "dimensions": dimensions, "projection": projection, "profiles": rows, "checks": native_checks, "labels": labels,
            "geometry_status": evidence["status"]}


def emit_labels(entities, prepared, placed):
    from .export import text
    from .sheet import _sheet_point
    for entity in prepared["labels"]:
        point = prepared["sheet"].points[entity.points[0]]
        height = entity.height * placed.factor
        if height < 2.5:
            raise ValueError(f"Model text {entity.name!r} would be below 2.5 mm on this sheet; use a larger sheet/view scale.")
        x, y = _sheet_point(placed, point.as_tuple())
        entities.append(replace(text(entity.text, x, y, height, entity.layer,
                                     rotation=entity.metadata.get("rotation_deg", 0)), view_name="top"))
