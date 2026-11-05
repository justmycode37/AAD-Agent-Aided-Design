"""Deterministic orthographic renders for agents reviewing a CAD model.

This module imports no engine package. It uses the public shape operations
(``faces``, ``edges``, ``tessellate`` and ``bounding_box``) through duck typing.
VTK itself is loaded by the selected backend on the first call to ``render``.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import exact
from .thickness import EXACT_REFINEMENTS, WallSamples, scene_triangles, visible_from, wall_samples
from .backends import CameraSpec, Frame, RenderFace, RenderLine, RenderScene, get_backend

_LOG = logging.getLogger(__name__)
_VIEWS = {"iso", "front", "back", "left", "right", "top", "bottom"}
_SHEET_VIEWS = ("front", "right", "top", "iso")
_STYLES = {"shaded", "lines", "xray"}
_LABELS = {"none", "features", "faces", "edges"}
_HEATMAPS = {None, "thickness", "overhang", "draft", "curvature"}
_DIMENSIONS = {"none", "overall", "auto"}
_AXIS_VIEWS = ("front", "back", "left", "right", "top", "bottom")
_BASE_COLORS = {
    "shaded": (194, 201, 208),
    "lines": (248, 249, 250),
    "xray": (157, 191, 211),
}
_HIGHLIGHT = (244, 48, 91)
_SECTION_CAP = (224, 182, 128)
_NO_READING = (154, 160, 166)
_BELOW_LIMIT = (178, 0, 122)  # Thinner than the wall thickness the request asked for.
_FRAME_MARGIN = 1.22  # Room around the part for rulers, dimensions, triad and pane titles.
_ISO_MARGIN = 1.12  # Isometric views carry no rulers or dimension lines.
_ANGULAR_DEFLECTION = 0.08  # Radians between facets on curved faces.
_PIXEL_DEFLECTION = 0.25  # Largest chord error, in pixels of a view at full image size.
_MAX_EXACT_EDGE_CLASSIFICATIONS = 2048
_INK = (28, 34, 41)
_INK_SOFT = (82, 90, 99)
_DIMENSION_INK = (22, 74, 146)
_MEASURE_INK = (176, 28, 112)
_GRID = (226, 231, 236)
_GRID_MAJOR = (209, 215, 222)
_BACKGROUND = (244, 246, 248)

Vec3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class _Section:
    plane: str
    point: Vec3
    normal: Vec3
    label: str


@dataclass(slots=True)
class _Label:
    number: int
    selector: str
    feature: str | None
    kind: str
    index: int | None
    anchor: Vec3


@dataclass(slots=True)
class _Overlay:
    """Everything drawn on top of a view besides its labels."""

    dimensions: str = "none"
    round_faces: list[dict[str, Any]] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    measures: list[dict[str, Any]] = field(default_factory=list)
    marks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class _Pane:
    view: str
    camera: CameraSpec
    x: int
    y: int
    width: int
    height: int
    frame: Frame | None = None
    drawn: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _HeatLegend:
    kind: str
    minimum: float
    maximum: float
    units: str
    heuristic: str
    reverse: bool = False  # Low values are the warning color, as with thin walls.
    limit: float | None = None


def render(ctx: Any, req: Any, out: Path) -> list[Path]:
    """Render ``ctx.shape`` to deterministic PNGs under ``out``, with an ``inspection.json`` beside them.

    Supported section planes are ``XY``, ``XZ`` and ``YZ`` (offset is along
    their positive normal), plus general strings such as
    ``point=(0, 0, 0);normal=(1, 1, 0)``. General-plane offsets move the point
    along the normalized normal. The section is an OCCT boolean cut, so the
    new section face is capped; diagonal hatch lines are clipped to its mesh.

    The images show where to look. ``inspection.json`` says what is there, from the B-rep rather than
    the mesh: every face and edge with its type and dimensions, how world points map to each view's
    pixels, which faces each view shows, exact section outlines, and the answers to ``probes``,
    ``measure`` and thickness requests. See this package's README for the request fields.
    """
    request = _read_request(req)
    shape = _field(ctx, "shape", None)
    if shape is None:
        raise ValueError("aad_render needs a 3D context with ctx.shape")

    output = Path(out)
    output.mkdir(parents=True, exist_ok=True)
    original_faces = list(shape.faces())
    original_edges = list(shape.edges())
    if not original_faces:
        raise ValueError("aad_render needs a non-empty 3D shape with renderable faces")
    classify_edges = len(original_edges) <= _MAX_EXACT_EDGE_CLASSIFICATIONS
    bounds = _shape_bounds(shape)
    diagonal = _bounds_diagonal(bounds)
    units = str(_field(ctx, "units", "model units") or "model units")
    topology = _topology(shape)

    focus = _read_focus(ctx, request["focus"], topology, shape, bounds)
    span = _box_span(focus["box"]) if focus else diagonal
    tolerance = max(diagonal * _FRAME_MARGIN / request["size"] * _PIXEL_DEFLECTION, diagonal * 1.0e-5, 1.0e-6)
    fine = None
    if focus:
        fine = (focus["box"], max(span * _FRAME_MARGIN / request["size"] * _PIXEL_DEFLECTION, 1.0e-6))

    section = _parse_section(request["section"], units) if request["section"] else None
    section_history: dict[str, Any] = {}
    render_shape = _section_shape(shape, section, diagonal, history=section_history) if section else shape
    shape_faces = list(render_shape.faces())
    shape_edges = list(render_shape.edges())
    render_topology = (_topology(render_shape) if section else topology) if shape_faces else None
    source_face_indexes = _source_indexes(shape_faces, original_faces, section is None, section_history.get("operation"))
    source_edge_indexes = _source_indexes(shape_edges, original_edges, section is None, section_history.get("operation"))
    feature_face_map, feature_edge_map = _feature_maps(_field(ctx, "features", ()))
    selected_faces, selected_edges, selected_features = _resolve_highlights(
        ctx,
        request["highlight"],
        len(original_faces),
        len(original_edges),
    )

    scene = _make_scene(
        render_shape,
        shape_faces,
        shape_edges,
        source_face_indexes,
        source_edge_indexes,
        feature_face_map,
        feature_edge_map,
        selected_faces,
        selected_edges,
        selected_features,
        bounds=_shape_bounds(render_shape) if shape_faces else bounds,
        units=units,
        section=section,
        tolerance=tolerance,
        fine=fine,
        topology=render_topology,
        hatch_span=span,
        classify_edges=classify_edges,
    )
    if section and not shape_faces:
        scene.section_label = section.label + " | EMPTY: no geometry on retained side"

    report: dict[str, Any] = {
        "version": 1,
        "units": units,
        "mesh": {"linear_deflection": exact._clean(tolerance), "angular_deflection_rad": _ANGULAR_DEFLECTION},
        "note": "face:N and edge:N are this revision's indexes, for inspecting it only. Do not store them in model code.",
    }
    if fine is not None:
        report["mesh"]["focus_linear_deflection"] = exact._clean(fine[1])
        report["focus"] = {"min": list(focus["box"][0]), "max": list(focus["box"][1]), "clip": focus["clip"]}
    overlay = _Overlay(dimensions=request["dimensions"])
    if topology is not None:
        report.update(
            _shape_report(
                ctx,
                shape,
                topology,
                feature_face_map,
                feature_edge_map,
                include_edge_classes=classify_edges,
            )
        )
        report["edge_classification"] = {
            "status": "complete" if classify_edges else "skipped",
            "classified_edges": len(original_edges) if classify_edges else 0,
            "skipped_edges": 0 if classify_edges else len(original_edges),
            "total_edges": len(original_edges),
            **({} if classify_edges else {
                "limit": _MAX_EXACT_EDGE_CLASSIFICATIONS,
                "reason": "Exact sharp, tangent, and seam classification is omitted above the configured edge limit.",
            }),
        }
        overlay.round_faces = _round_faces(report["faces"], scene)
    if section:
        report["section"] = _section_report(section, scene, shape_faces, source_face_indexes)

    heat_legend, heat_report = _apply_heatmap(
        request, scene, shape, shape_faces, topology, overlay, bounds=bounds, units=units, section=section,
        tolerance=tolerance, feature_maps=(feature_face_map, feature_edge_map),
    )
    if heat_report:
        report["heatmap"] = heat_report

    labels, legend = _make_labels(
        request["labels"],
        _field(ctx, "features", ()),
        scene,
        bounds=scene.bounds,
    )
    backend = get_backend(request["backend"])
    result_paths: list[Path] = []
    pictures = {}
    box = focus["box"] if focus else scene.bounds
    for view in request["views"]:
        pictures[view] = _layout(view, box, request["size"], request["camera"])
        for pane in pictures[view][1]:
            pane.camera = _outside_camera(pane.camera, scene.bounds)

    probes_needed = request["probes"] or request["measure"]
    rays = exact.Rays(render_shape.wrapped, render_topology, diagonal) if probes_needed and render_topology else None
    clip_box = focus["box"] if focus and focus["clip"] else None
    if request["probes"]:
        solid_rays = exact.Rays(shape.wrapped, topology, diagonal) if topology is not None else None
        overlay.probes = _run_probes(request["probes"], pictures, rays, solid_rays, source_face_indexes, units, clip_box)
        report["probes"] = [probe["result"] for probe in overlay.probes]
    if request["measure"]:
        overlay.measures = _run_measures(ctx, request["measure"], shape, topology)
        report["measurements"] = overlay.measures

    report["images"] = {}
    for view, (size, panes) in pictures.items():
        image = _draw_picture(backend, scene, view, size, panes, request["style"], labels, heat_legend, overlay, clip_box)
        path = output / f"{view}.png"
        _save_png(image, path)
        result_paths.append(path)
        report["images"][path.name] = _image_report(size, panes, scene)

    inspection_path = output / "inspection.json"
    inspection_path.write_bytes(
        (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    )
    result_paths.append(inspection_path)

    if request["labels"] != "none":
        legend_path = output / "labels.json"
        legend_bytes = (json.dumps(legend, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        legend_path.write_bytes(legend_bytes)
        result_paths.append(legend_path)
    return result_paths


def _topology(shape: Any) -> exact.Topology | None:
    """Exact lookups need a real OCCT shape; plain stand-ins still get images."""
    if getattr(shape, "wrapped", None) is None:
        return None
    return exact.Topology.of(shape)


def _read_request(req: Any) -> dict[str, Any]:
    raw_views = _field(req, "views", None)
    camera = _read_camera(_field(req, "camera", None))
    if raw_views is None or (camera and list(raw_views) == ["sheet"]):
        # RenderRequest defaults views to the sheet, so a camera alone means its own view.
        views = ["custom"] if camera else ["sheet"]
    elif isinstance(raw_views, str):
        views = [raw_views]
    else:
        views = list(raw_views)
    if not views:
        views = ["sheet"]
    normalized_views = []
    for view in views:
        key = str(view).strip().lower()
        if key == "custom" and not camera:
            raise ValueError("the custom view needs a camera: {direction: [x, y, z]}")
        if key not in ("sheet", "custom") and key not in _VIEWS:
            raise ValueError(f"unsupported render view {view!r}")
        if key not in normalized_views:
            normalized_views.append(key)

    style = str(_field(req, "style", "shaded") or "shaded").lower()
    if style not in _STYLES:
        raise ValueError(f"unsupported render style {style!r}; use shaded, lines or xray")
    labels = str(_field(req, "labels", "none") or "none").lower()
    if labels not in _LABELS:
        raise ValueError(f"unsupported label mode {labels!r}")
    heatmap = _field(req, "heatmap", None)
    heatmap = str(heatmap).lower() if heatmap is not None else None
    if heatmap not in _HEATMAPS:
        raise ValueError("heatmap must be thickness, overhang, draft, curvature or None")

    size = _field(req, "size", 1024)
    if size is None:
        size = 1024
    if isinstance(size, bool) or not isinstance(size, int) or size < 1 or size > 2048:
        raise ValueError("render size must be an integer between 1 and 2048 pixels")

    highlight = _field(req, "highlight", ()) or ()
    if isinstance(highlight, str):
        highlight = [highlight]
    dimensions = str(_field(req, "dimensions", "auto") or "none").lower()
    if dimensions not in _DIMENSIONS:
        raise ValueError("dimensions must be none, overall or auto")
    heat_range = _field(req, "heatmap_range", None)
    if heat_range is not None:
        heat_range = [float(value) for value in heat_range]
        if len(heat_range) != 2 or not all(map(math.isfinite, heat_range)) or heat_range[0] >= heat_range[1]:
            raise ValueError("heatmap_range must be [low, high] with low below high")
    limit = _field(req, "min_wall", None)
    if limit is not None:
        limit = float(limit)
        if not math.isfinite(limit) or limit <= 0.0:
            raise ValueError("min_wall must be a positive number")
    direction = _field(req, "direction", None)
    return {
        "camera": camera,
        "focus": _field(req, "focus", None),
        "dimensions": dimensions,
        "probes": list(_field(req, "probes", ()) or ()),
        "measure": list(_field(req, "measure", ()) or ()),
        "heatmap_range": heat_range,
        "min_wall": limit,
        "direction": _unit_vector(direction, "direction") if direction is not None else (0.0, 0.0, 1.0),
        "views": normalized_views,
        "style": style,
        "section": _field(req, "section", None),
        "labels": labels,
        "heatmap": heatmap,
        "highlight": [str(selector) for selector in highlight],
        "size": size,
        "backend": str(_field(req, "backend", "vtk") or "vtk"),
    }


def _unit_vector(value: Any, name: str) -> Vec3:
    try:
        vector = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be three numbers") from None
    if len(vector) != 3 or not all(map(math.isfinite, vector)) or not any(vector):
        raise ValueError(f"{name} must be three finite numbers, not all zero")
    return _normalize(vector)


def _read_camera(raw: Any) -> dict[str, Any] | None:
    """A camera names the direction toward the viewer, with optional up, target and view height."""
    if raw is None:
        return None
    direction = _field(raw, "direction", None)
    if direction is None:
        raise ValueError("camera needs direction: [x, y, z], pointing from the part toward the viewer")
    camera: dict[str, Any] = {"direction": _unit_vector(direction, "camera direction")}
    up = _field(raw, "up", None)
    camera["up"] = _unit_vector(up, "camera up") if up is not None else None
    if camera["up"] is not None and abs(_dot(camera["up"], camera["direction"])) > 1.0 - 1.0e-9:
        raise ValueError("camera up must not be parallel to camera direction")
    target = _field(raw, "target", None)
    if target is not None:
        target = tuple(float(component) for component in target)
        if len(target) != 3 or not all(map(math.isfinite, target)):
            raise ValueError("camera target must be three finite numbers")
    camera["target"] = target
    span = _field(raw, "span", None)
    if span is not None:
        span = float(span)
        if not math.isfinite(span) or span <= 0.0:
            raise ValueError("camera span must be a positive number of model units")
    camera["span"] = span
    return camera


def _field(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _shape_bounds(shape: Any) -> tuple[Vec3, Vec3]:
    bbox = shape.bounding_box()
    minimum = _vector(bbox.min)
    maximum = _vector(bbox.max)
    if any(not math.isfinite(component) for component in minimum + maximum):
        raise ValueError("shape has a non-finite bounding box")
    return minimum, maximum


def _vector(value: Any) -> Vec3:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return (float(value[0]), float(value[1]), float(value[2]))
    return (
        float(getattr(value, "X", getattr(value, "x", 0.0))),
        float(getattr(value, "Y", getattr(value, "y", 0.0))),
        float(getattr(value, "Z", getattr(value, "z", 0.0))),
    )


def _bounds_diagonal(bounds: tuple[Vec3, Vec3]) -> float:
    minimum, maximum = bounds
    result = math.sqrt(sum((maximum[i] - minimum[i]) ** 2 for i in range(3)))
    return max(result, 1.0e-9)


def _parse_section(section: Any, units: str) -> _Section:
    plane = str(_field(section, "plane", "XY") or "XY").strip()
    offset = float(_field(section, "offset", 0.0) or 0.0)
    if not math.isfinite(offset):
        raise ValueError("section offset must be finite")
    canonical = plane.upper()
    axis_sections = {
        "XY": ((0.0, 0.0, 1.0), "z"),
        "XZ": ((0.0, 1.0, 0.0), "y"),
        "YZ": ((1.0, 0.0, 0.0), "x"),
    }
    if canonical in axis_sections:
        normal, axis = axis_sections[canonical]
        point = tuple(component * offset for component in normal)
        return _Section(
            canonical,
            point,
            normal,
            f"SECTION {canonical} | {axis.upper()}={_fmt(offset)} {_unit_for_label(units)}",
        )

    point = _named_vector(plane, "point") or (0.0, 0.0, 0.0)
    normal = _named_vector(plane, "normal") or _named_vector(plane, "n")
    if normal is None:
        raise ValueError(
            "general section planes use 'point=(x,y,z);normal=(nx,ny,nz)'"
        )
    normal = _normalize(normal)
    point = tuple(point[i] + offset * normal[i] for i in range(3))
    return _Section(
        plane,
        point,
        normal,
        "SECTION n=(" + ",".join(_fmt(v) for v in normal) + ") | point=("
        + ",".join(_fmt(v) for v in point)
        + f") {_unit_for_label(units)}",
    )


def _named_vector(text: str, name: str) -> Vec3 | None:
    match = re.search(rf"\b{name}\s*=\s*\(?\s*([^);]+)", text, flags=re.IGNORECASE)
    if match is None:
        return None
    numbers = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", match.group(1))
    if len(numbers) != 3:
        raise ValueError(f"section plane {name}= must contain three numbers")
    vector = tuple(float(number) for number in numbers)
    if any(not math.isfinite(component) for component in vector):
        raise ValueError("section plane values must be finite")
    return vector  # type: ignore[return-value]


def _section_shape(shape: Any, section: _Section, diagonal: float, *, history: dict[str, Any] | None = None) -> Any:
    """Cut away the positive-normal side with an OCCT half-space boolean."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from build123d import Shape

    minimum, maximum = _shape_bounds(shape)
    center = tuple((minimum[i] + maximum[i]) * 0.5 for i in range(3))
    extent = max(diagonal * 8.0 + math.dist(center, section.point) + 1.0, 1.0)
    plane = gp_Pln(gp_Pnt(*section.point), gp_Dir(*section.normal))
    face_builder = BRepBuilderAPI_MakeFace(plane, -extent, extent, -extent, extent)
    if not face_builder.IsDone():
        raise ValueError("OCCT could not construct the requested section plane")
    positive = tuple(section.point[i] + section.normal[i] * extent for i in range(3))
    half_space = BRepPrimAPI_MakeHalfSpace(face_builder.Face(), gp_Pnt(*positive)).Solid()
    operation = BRepAlgoAPI_Cut(shape.wrapped, half_space)
    operation.Build()
    if not operation.IsDone() or operation.Shape().IsNull():
        raise ValueError("OCCT failed to make a capped section cut")
    result = Shape.cast(operation.Shape())
    if history is not None:
        history["operation"] = operation
    return result


def _source_indexes(result_entities: list[Any], source_entities: list[Any], same_shape: bool, operation: Any = None) -> list[int | None]:
    if same_shape:
        return list(range(len(result_entities)))
    # A boolean trims faces and edges, changing their topology identity. OCCT's
    # Modified history retains their origin; generated cap entities have none.
    descendants = [
        [source.wrapped, *operation.Modified(source.wrapped)] if operation is not None else [source.wrapped]
        for source in source_entities
    ]
    indexes: list[int | None] = []
    for result in result_entities:
        source_index = None
        result_wrapped = getattr(result, "wrapped", None)
        if result_wrapped is not None:
            for index, candidates in enumerate(descendants):
                try:
                    if any(result_wrapped.IsSame(candidate) for candidate in candidates):
                        if source_index is not None:
                            source_index = None  # Ambiguous history must not invent a selector.
                            break
                        source_index = index
                except Exception:
                    continue
        indexes.append(source_index)
    return indexes


def _feature_maps(features: Any) -> tuple[dict[int, str], dict[int, str]]:
    face_map: dict[int, str] = {}
    edge_map: dict[int, str] = {}
    for fallback_name, feature in _feature_records(features):
        name = str(_field(feature, "name", fallback_name or "main"))
        for key, entity in _entity_records(_field(feature, "faces", ())):
            index = _entity_index(entity, key)
            if index is not None:
                face_map[int(index)] = name
        for key, entity in _entity_records(_field(feature, "edges", ())):
            index = _entity_index(entity, key)
            if index is not None:
                edge_map[int(index)] = name
    return face_map, edge_map


def _feature_records(features: Any) -> list[tuple[Any, Any]]:
    if isinstance(features, Mapping):
        return list(features.items())
    return [(None, feature) for feature in features or ()]


def _entity_records(entities: Any) -> list[tuple[Any, Any]]:
    if isinstance(entities, Mapping):
        return list(entities.items())
    return [(None, entity) for entity in entities or ()]


def _entity_index(entity: Any, key: Any = None) -> int | None:
    index = _field(entity, "index", _field(entity, "face_index", None))
    if index is None and isinstance(entity, int) and not isinstance(entity, bool):
        index = entity
    if index is None:
        index = key
    if index is None:
        return None
    try:
        return int(index)
    except (TypeError, ValueError):
        return None


def _resolve_highlights(
    ctx: Any,
    selectors: list[str],
    face_count: int,
    edge_count: int,
) -> tuple[set[int], set[int], set[str]]:
    face_indexes: set[int] = set()
    edge_indexes: set[int] = set()
    feature_names: set[str] = set()
    if not selectors:
        return face_indexes, edge_indexes, feature_names

    engine_resolve = _engine_resolver()
    for selector in selectors:
        local = _local_selector(selector)
        if local is not None:
            kind, value = local
            if kind == "feature":
                if not any(str(_field(feature, "name", key)) == value
                           for key, feature in _feature_records(_field(ctx, "features", ()))):
                    raise ValueError(f"no feature named {value!r} in this revision")
                feature_names.add(str(value))
            elif kind in ("face", "edge"):
                _add_highlight_index(kind, int(value), face_count, edge_count, face_indexes, edge_indexes, selector)
            else:
                raise ValueError(f"highlight {selector!r}: only faces, edges and features can be highlighted")
            continue
        if engine_resolve is None:
            raise ValueError(
                f"cannot resolve highlight {selector!r}; without aad.engine.select.resolve "
                "use face:<index>, edge:<index> or feature:<name>"
            )
        entities = _entity_list(engine_resolve(ctx, selector))
        if not entities:
            raise ValueError(f"highlight selector {selector!r} resolved to no geometry")
        for entity in entities:
            kind = str(_field(entity, "kind", "")).lower()
            index = _field(entity, "index", None)
            feature = _field(entity, "feature", None)
            if index is not None:
                _add_highlight_index(kind, int(index), face_count, edge_count, face_indexes, edge_indexes, selector)
            elif feature:
                feature_names.add(str(feature))
    return face_indexes, edge_indexes, feature_names


def _engine_resolver() -> Any:
    try:
        from aad.engine.select import resolve
    except ImportError:
        return None
    return resolve


def _entity_list(resolved: Any) -> list[Any]:
    if resolved is None:
        return []
    return list(resolved) if isinstance(resolved, (list, tuple, set)) else [resolved]


def _local_selector(selector: str) -> tuple[str, Any] | None:
    """The renderer's own formats. They name this revision's entities and never reach the engine."""
    match = re.fullmatch(r"\s*(face|edge|vertex)\s*:\s*(\d+)\s*", selector, re.IGNORECASE)
    if match:
        return match.group(1).lower(), int(match.group(2))
    match = re.fullmatch(r"\s*feature\s*:\s*(.+?)\s*", selector, re.IGNORECASE)
    if match:
        return "feature", match.group(1)
    match = re.fullmatch(r"\s*point\s*:\s*\(?([^()]+)\)?\s*", selector, re.IGNORECASE)
    if match:
        numbers = [float(number) for number in match.group(1).split(",")]
        if len(numbers) != 3 or not all(map(math.isfinite, numbers)):
            raise ValueError(f"point selector {selector!r} needs three finite numbers")
        return "point", tuple(numbers)
    return None


def _shapes_for(ctx: Any, selector: str, shape: Any, topology: exact.Topology | None) -> list[Any]:
    """OCCT shapes that a selector names."""
    if topology is None:
        raise ValueError(f"selector {selector!r} needs a real OCCT shape")
    vertices = [vertex.wrapped for vertex in shape.vertices()]
    local = _local_selector(selector)
    if local is not None:
        kind, value = local
        if kind == "point":
            return [exact.point_shape(value)]
        if kind == "feature":
            face_map, edge_map = _feature_maps(_field(ctx, "features", ()))
            found = [topology.faces[index] for index, name in sorted(face_map.items()) if name == value and index < len(topology.faces)]
            found += [topology.edges[index] for index, name in sorted(edge_map.items()) if name == value and index < len(topology.edges)]
            if not found:
                raise ValueError(f"no feature named {value!r} in this revision")
            return found
        pool = {"face": topology.faces, "edge": topology.edges, "vertex": vertices}[kind]
        if value >= len(pool):
            raise ValueError(f"selector {selector!r} has no matching {kind}; this revision has {len(pool)}")
        return [pool[value]]
    resolver = _engine_resolver()
    if resolver is None:
        raise ValueError(f"cannot resolve {selector!r}; use face:<i>, edge:<i>, vertex:<i>, feature:<name> or point:(x,y,z)")
    found = []
    pools = {"face": topology.faces, "edge": topology.edges, "vertex": vertices}
    for entity in _entity_list(resolver(ctx, selector)):
        pool = pools.get(str(_field(entity, "kind", "")).lower())
        index = _field(entity, "index", None)
        if pool is not None and index is not None and 0 <= int(index) < len(pool):
            found.append(pool[int(index)])
    if not found:
        raise ValueError(f"selector {selector!r} matched nothing in this revision")
    return found


def _read_focus(ctx: Any, raw: Any, topology: Any, shape: Any, bounds: tuple[Vec3, Vec3]) -> dict[str, Any] | None:
    """Turn a focus request into the world box that views should fill."""
    if raw is None:
        return None
    settings: Any = {"selector": raw} if isinstance(raw, (str, list, tuple)) else raw
    selector = _field(settings, "selector", None)
    low, high = _field(settings, "min", None), _field(settings, "max", None)
    if selector is not None:
        selectors = [selector] if isinstance(selector, str) else list(selector)
        if not selectors:
            raise ValueError("focus needs at least one selector")
        shapes = [item for name in selectors for item in _shapes_for(ctx, str(name), shape, topology)]
        low, high = exact.box_of(shapes)
    elif low is None or high is None:
        raise ValueError("focus needs a selector, or min and max corners of a box")
    low = tuple(float(value) for value in low)
    high = tuple(float(value) for value in high)
    if len(low) != 3 or len(high) != 3 or not all(map(math.isfinite, low + high)) or any(a > b for a, b in zip(low, high)):
        raise ValueError("focus min and max must be three finite numbers with min at or below max")
    margin = float(_field(settings, "margin", 0.25))
    if not math.isfinite(margin) or margin < 0.0:
        raise ValueError("focus margin must be a non-negative fraction of the box size")
    size = max(max(b - a for a, b in zip(low, high)), _bounds_diagonal(bounds) * 1.0e-3)
    pad = size * margin + size * 0.02
    box = (tuple(value - pad for value in low), tuple(value + pad for value in high))
    return {"box": box, "clip": bool(_field(settings, "clip", False))}


def _box_span(box: tuple[Vec3, Vec3]) -> float:
    return max(max(box[1][axis] - box[0][axis] for axis in range(3)), 1.0e-9)


def _add_highlight_index(
    kind: str,
    index: int,
    face_count: int,
    edge_count: int,
    face_indexes: set[int],
    edge_indexes: set[int],
    selector: str,
) -> None:
    if kind == "face" and 0 <= index < face_count:
        face_indexes.add(index)
    elif kind == "edge" and 0 <= index < edge_count:
        edge_indexes.add(index)
    else:
        raise ValueError(f"highlight selector {selector!r} has no matching {kind or 'entity'}")


def _make_scene(
    shape: Any,
    faces: list[Any],
    edges: list[Any],
    source_face_indexes: list[int | None],
    source_edge_indexes: list[int | None],
    feature_face_map: dict[int, str],
    feature_edge_map: dict[int, str],
    selected_faces: set[int],
    selected_edges: set[int],
    selected_features: set[str],
    *,
    bounds: tuple[Vec3, Vec3],
    units: str,
    section: _Section | None,
    tolerance: float | None = None,
    fine: tuple[tuple[Vec3, Vec3], float] | None = None,
    topology: exact.Topology | None = None,
    hatch_span: float | None = None,
    classify_edges: bool = True,
) -> RenderScene:
    diagonal = _bounds_diagonal(bounds)
    if tolerance is None:
        tolerance = max(diagonal * 3.0e-4, 1.0e-6)
    scene = RenderScene(bounds=bounds, units=units, section_label=section.label if section else None)
    cap_face_indexes: list[int] = []
    tessellations = _tessellate_faces(shape, faces, tolerance, _ANGULAR_DEFLECTION, fine)
    for index, face in enumerate(faces):
        uv = None
        if tessellations is None:
            try:
                vertices, triangles = face.tessellate(tolerance, _ANGULAR_DEFLECTION)
            except TypeError:
                vertices, triangles = face.tessellate(tolerance)
            points = np.asarray([_vector(point) for point in vertices], dtype=np.float64).reshape(-1, 3)
            triangle_indexes = np.asarray(
                [list(triangle)[:3] for triangle in triangles if len(triangle) == 3], dtype=np.int64
            ).reshape(-1, 3)
        else:
            points, triangle_indexes, uv = tessellations[index]
        if len(triangle_indexes):
            valid = (triangle_indexes >= 0).all(axis=1) & (triangle_indexes < len(points)).all(axis=1)
            triangle_indexes = triangle_indexes[valid]
        if not len(points) or not len(triangle_indexes):
            continue
        try:
            normal = _normalize(_vector(face.normal_at()))
        except Exception:
            normal = _mesh_normal(points, triangle_indexes)
        anchor = _surface_anchor(face, points, triangle_indexes)
        source_index = source_face_indexes[index] if index < len(source_face_indexes) else None
        feature = feature_face_map.get(source_index) if source_index is not None else None
        section_cap = bool(section and source_index is None and _is_section_cap(face, anchor, normal, section, diagonal))
        if section_cap:
            cap_face_indexes.append(len(scene.faces))
        scene.faces.append(
            RenderFace(
                index=index,
                source_index=source_index,
                feature=feature,
                points=points,
                triangles=triangle_indexes,
                normal=normal,
                anchor=anchor,
                section_cap=section_cap,
                highlighted=(
                    (source_index is not None and source_index in selected_faces)
                    or (feature is not None and feature in selected_features)
                ),
                color=_BASE_COLORS["shaded"],
                uv=uv,
            )
        )

    for index, edge in enumerate(edges):
        wrapped = getattr(edge, "wrapped", None)
        sampled = exact.edge_polyline(wrapped, tolerance, _ANGULAR_DEFLECTION) if topology is not None else None
        if sampled is not None and fine is not None:
            sampled = exact.edge_polyline(wrapped, fine[1], _ANGULAR_DEFLECTION) or sampled
        points = tuple(sampled) if sampled is not None else _edge_points(edge, diagonal, tolerance)
        if len(points) < 2:
            continue
        source_index = source_edge_indexes[index] if index < len(source_edge_indexes) else None
        feature = feature_edge_map.get(source_index) if source_index is not None else None
        try:
            anchor = _vector(edge.position_at(0.5))
        except Exception:
            anchor = points[len(points) // 2]
        role = "edge"
        if topology is not None and classify_edges:
            name = exact.edge_class(wrapped, topology)[0]
            role = name if name in ("tangent", "seam") else "edge"
        scene.lines.append(
            RenderLine(
                points=points,
                role=role,
                index=index,
                source_index=source_index,
                feature=feature,
                anchor=anchor,
                highlighted=(
                    (source_index is not None and source_index in selected_edges)
                    or (feature is not None and feature in selected_features)
                ),
            )
        )

    if section:
        for face_index in cap_face_indexes:
            scene.lines.extend(_hatch_lines(scene.faces[face_index], section, hatch_span or diagonal))
    return scene


def _mesh_normal(points: np.ndarray, triangles: np.ndarray) -> Vec3:
    corners = points[triangles]
    total = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]).sum(axis=0)
    return _normalize(total.tolist())


def _surface_anchor(face: Any, points: np.ndarray, triangles: np.ndarray) -> Vec3:
    """A point on the face itself. A curved face's center of mass is often out in empty space."""
    centers = points[triangles].mean(axis=1)
    try:
        center = np.asarray(_vector(face.center()))
    except Exception:
        return tuple(centers[0].tolist())
    return tuple(centers[int(np.argmin(np.linalg.norm(centers - center, axis=1)))].tolist())


def _is_section_cap(face: Any, anchor: Vec3, normal: Vec3, section: _Section, diagonal: float) -> bool:
    if abs(abs(_dot(normal, section.normal)) - 1.0) > 1.0e-5:
        return False
    try:
        center = _vector(face.center())
    except Exception:
        center = anchor
    return abs(_dot(_subtract(center, section.point), section.normal)) <= max(diagonal * 1.0e-5, 1.0e-6)


def _edge_points(edge: Any, diagonal: float, tolerance: float) -> tuple[Vec3, ...]:
    samples = [_vector(edge.position_at(index / 8.0)) for index in range(9)]
    start, end = samples[0], samples[-1]
    deviation = max(
        math.dist(samples[index], tuple(start[axis] + (end[axis] - start[axis]) * index / 8.0 for axis in range(3)))
        for index in range(1, 8)
    )
    if deviation <= max(diagonal * 1.0e-5, tolerance * 0.05):
        return (start, end)
    try:
        length = float(edge.length)
    except Exception:
        length = sum(math.dist(samples[i], samples[i + 1]) for i in range(8))
    target_step = max(diagonal / 48.0, tolerance * 3.0, 1.0e-6)
    count = max(8, min(64, int(math.ceil(length / target_step))))
    return tuple(_vector(edge.position_at(index / count)) for index in range(count + 1))


def _tessellate_faces(
    shape: Any,
    faces: list[Any],
    tolerance: float,
    angular_tolerance: float,
    fine: tuple[tuple[Vec3, Vec3], float] | None = None,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray | None]] | None:
    """Mesh afresh and read OCCT's triangulations directly, without build123d's slow triangle iterator.

    Clearing first keeps output repeatable: OCCT would otherwise reuse any finer mesh that an earlier
    request left on the shape. Faces inside a focus box get the focus view's finer tolerance.
    """
    if not faces:
        return []
    wrapped_shape = getattr(shape, "wrapped", None)
    if wrapped_shape is None or any(getattr(face, "wrapped", None) is None for face in faces):
        return None
    try:
        from OCP.BRep import BRep_Tool
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.BRepTools import BRepTools
        from OCP.TopAbs import TopAbs_Orientation
        from OCP.TopLoc import TopLoc_Location

        BRepTools.Clean_s(wrapped_shape)
        if fine is not None:
            box, fine_tolerance = fine
            for face in faces:
                low, high = exact.box_of([face.wrapped])
                if all(low[axis] <= box[1][axis] and high[axis] >= box[0][axis] for axis in range(3)):
                    BRepMesh_IncrementalMesh(face.wrapped, fine_tolerance, False, angular_tolerance, False)
        BRepMesh_IncrementalMesh(wrapped_shape, tolerance, False, angular_tolerance, True)
    except (ImportError, AttributeError, TypeError, RuntimeError):
        return None

    tessellations = []
    for face in faces:
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face.wrapped, location)
        if triangulation is None:
            return None
        transform = location.Transformation()
        count = triangulation.NbNodes()
        nodes = [triangulation.Node(node_index).Transformed(transform) for node_index in range(1, count + 1)]
        points = np.asarray([(node.X(), node.Y(), node.Z()) for node in nodes], dtype=np.float64).reshape(-1, 3)
        uv = None
        if triangulation.HasUVNodes():
            pairs = [triangulation.UVNode(node_index) for node_index in range(1, count + 1)]
            uv = np.asarray([(pair.X(), pair.Y()) for pair in pairs], dtype=np.float64).reshape(-1, 2)
        triangles = np.asarray(
            [triangulation.Triangle(row).Get() for row in range(1, triangulation.NbTriangles() + 1)],
            dtype=np.int64,
        ).reshape(-1, 3) - 1
        if face.wrapped.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
            triangles = triangles[:, [0, 2, 1]]
        tessellations.append((points, triangles, uv))
    return tessellations


def _hatch_lines(face: RenderFace, section: _Section, diagonal: float) -> list[RenderLine]:
    normal = section.normal
    reference = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (0.0, 1.0, 0.0)
    axis_u = np.asarray(_normalize(_cross(normal, reference)))
    axis_v = np.asarray(_normalize(_cross(normal, tuple(axis_u))))
    origin = np.asarray(section.point)
    points = np.asarray(face.points, dtype=np.float64)
    triangles = np.asarray(face.triangles, dtype=np.int64)
    if not len(points) or not len(triangles):
        return []
    relative = points - origin
    flat = np.stack([relative @ axis_u, relative @ axis_v], axis=1)
    root = math.sqrt(0.5)
    along = flat @ np.asarray((root, root))
    across = flat @ np.asarray((-root, root))
    spacing = max(diagonal * 0.022, 1.0e-4)
    hatch: list[RenderLine] = []
    corner_across = across[triangles]
    corner_along = along[triangles]
    for step in range(math.ceil(across.min() / spacing), math.floor(across.max() / spacing) + 1):
        offset = step * spacing
        crossing = (corner_across.min(axis=1) <= offset) & (corner_across.max(axis=1) >= offset)
        if not crossing.any():
            continue
        values = corner_across[crossing] - offset
        positions = corner_along[crossing]
        following_values = np.roll(values, -1, axis=1)
        following_positions = np.roll(positions, -1, axis=1)
        spans = []
        for row_values, row_positions, next_values, next_positions in zip(values, positions, following_values, following_positions):
            found = []
            for a, b, pa, pb in zip(row_values, next_values, row_positions, next_positions):
                if abs(a) <= 1.0e-10:
                    found.append(pa)
                if a * b < -1.0e-18:
                    found.append(pa + (pb - pa) * a / (a - b))
            if len(found) >= 2:
                spans.append((min(found), max(found)))
        spans.sort()
        merged: list[list[float]] = []
        for low, high in spans:
            if merged and low <= merged[-1][1] + 1.0e-9:
                merged[-1][1] = max(merged[-1][1], high)
            elif high - low > 1.0e-9:
                merged.append([low, high])
        direction_u = (axis_u * root + axis_v * root)
        normal_u = (-axis_u * root + axis_v * root)
        for low, high in merged:
            start = origin + direction_u * low + normal_u * offset
            end = origin + direction_u * high + normal_u * offset
            hatch.append(RenderLine(points=(tuple(start.tolist()), tuple(end.tolist())), role="hatch"))
    return hatch


def _make_labels(
    mode: str,
    features: Any,
    scene: RenderScene,
    *,
    bounds: tuple[Vec3, Vec3],
) -> tuple[list[_Label], dict[str, dict[str, Any]]]:
    if mode == "none":
        return [], {}
    candidates: list[tuple[str, str | None, str, int | None, Vec3]] = []
    if mode == "features":
        for fallback_name, feature in _feature_records(features):
            name = str(_field(feature, "name", fallback_name or "main"))
            anchors = [face.anchor for face in scene.faces if face.feature == name]
            anchors.extend(
                line.anchor
                for line in scene.lines
                if line.role != "hatch" and line.feature == name and line.anchor is not None
            )
            if not anchors:
                continue
            anchor = _average(anchors)
            candidates.append((f"feature:{name}", name, "feature", None, anchor))
    elif mode == "faces":
        seen: set[str] = set()
        for face in scene.faces:
            index = face.source_index
            if index is None:
                continue  # New section caps are not entities in the source revision.
            selector = f"face:{index}"
            if selector in seen:
                continue
            seen.add(selector)
            candidates.append((selector, face.feature, "face", index, face.anchor))
    elif mode == "edges":
        seen = set()
        for line in scene.lines:
            if line.role == "hatch" or line.anchor is None:
                continue
            index = line.source_index
            if index is None:
                continue
            selector = f"edge:{index}"
            if selector in seen:
                continue
            seen.add(selector)
            candidates.append((selector, line.feature, "edge", int(index), line.anchor))

    labels: list[_Label] = []
    legend: dict[str, dict[str, Any]] = {}
    for number, (selector, feature, kind, index, anchor) in enumerate(candidates, start=1):
        labels.append(_Label(number, selector, feature, kind, index, anchor))
        legend[str(number)] = {
            "selector": selector,
            "feature": feature,
            "kind": kind,
            "index": index,
        }
    return labels, legend


def _camera(
    view: str,
    bounds: tuple[Vec3, Vec3],
    width: int,
    height: int,
    *,
    settings: dict[str, Any] | None = None,
) -> CameraSpec:
    directions: dict[str, tuple[Vec3, Vec3]] = {
        "iso": (_normalize((1.0, -1.0, 1.0)), (0.0, 0.0, 1.0)),
        "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
        "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
        "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
    }
    span = None
    center = _bounds_center(bounds)
    if settings is not None:
        toward_camera = settings["direction"]
        tentative_up = settings["up"] or ((0.0, 0.0, 1.0) if abs(toward_camera[2]) < 0.99 else (0.0, 1.0, 0.0))
        center = settings["target"] or center
        span = settings["span"]
    else:
        toward_camera, tentative_up = directions[view]
    forward = tuple(-value for value in toward_camera)
    right = _normalize(_cross(forward, tentative_up))
    up = _normalize(_cross(right, forward))
    corners = [
        (x, y, z)
        for x in (bounds[0][0], bounds[1][0])
        for y in (bounds[0][1], bounds[1][1])
        for z in (bounds[0][2], bounds[1][2])
    ]
    half_width = max(abs(_dot(_subtract(corner, center), right)) for corner in corners)
    half_height = max(abs(_dot(_subtract(corner, center), up)) for corner in corners)
    aspect = max(width / max(height, 1), 1.0e-6)
    axis_aligned = settings is None and view != "iso"
    parallel_scale = max(half_height, half_width / aspect, 1.0e-6) * (_FRAME_MARGIN if axis_aligned else _ISO_MARGIN)
    if span is not None:
        parallel_scale = span * 0.5
    distance = max(_bounds_diagonal(bounds) * 4.0, 1.0)
    position = tuple(center[i] + toward_camera[i] * distance for i in range(3))
    return CameraSpec(
        view,
        position,
        center,
        up,
        right,
        up,
        parallel_scale,
        distance,
    )


def _layout(
    view: str,
    box: tuple[Vec3, Vec3],
    size: int,
    camera_settings: dict[str, Any] | None,
) -> tuple[int, list[_Pane]]:
    if view != "sheet":
        camera = _camera(view, box, size, size, settings=camera_settings if view == "custom" else None)
        return size, [_Pane(view, camera, 0, 0, size, size)]
    if size < 64:
        return size, [_Pane("iso", _camera("iso", box, size, size), 0, 0, size, size)]
    padding = max(8, size // 64)
    gutter = max(8, size // 72)
    pane = max(1, (size - 2 * padding - gutter) // 2)
    origins = (
        (padding, padding),
        (padding + pane + gutter, padding),
        (padding, padding + pane + gutter),
        (padding + pane + gutter, padding + pane + gutter),
    )
    cameras = [_camera(name, box, pane, pane) for name in _SHEET_VIEWS]
    # The three orthographic panes share one scale, so sizes compare directly between them.
    common = max(camera.parallel_scale for camera in cameras[:3])
    cameras[:3] = [replace(camera, parallel_scale=common) for camera in cameras[:3]]
    return size, [
        _Pane(camera.name, camera, x, y, pane, pane)
        for camera, (x, y) in zip(cameras, origins, strict=True)
    ]


def _outside_camera(camera: CameraSpec, bounds: tuple[Vec3, Vec3]) -> CameraSpec:
    """Zoom changes the image scale, but must never move the camera inside the uncut part."""
    toward = _normalize(_subtract(camera.position, camera.focal_point))
    front = sum(max((low - target) * normal, (high - target) * normal)
                for low, high, target, normal in zip(*bounds, camera.focal_point, toward))
    distance = max(camera.distance, front + max(_bounds_diagonal(bounds) * 0.1, 1.0))
    position = tuple(camera.focal_point[i] + toward[i] * distance for i in range(3))
    return replace(camera, distance=distance, position=position)


def _frames_for(backend: Any, scene: RenderScene, panes: list[_Pane], style: str, clip_box: Any) -> None:
    width, height = panes[0].width, panes[0].height
    cameras = [pane.camera for pane in panes]
    render_frames = getattr(backend, "render_frames", None)
    if callable(render_frames):
        frames = render_frames(scene, cameras, width=width, height=height, style=style, clip_box=clip_box)
    else:
        frames = [Frame(backend.render(scene, camera, width=width, height=height, style=style)) for camera in cameras]
    for pane, frame in zip(panes, frames, strict=True):
        pane.frame = frame


def _draw_picture(
    backend: Any,
    scene: RenderScene,
    view: str,
    size: int,
    panes: list[_Pane],
    style: str,
    labels: list[_Label],
    heat_legend: _HeatLegend | None,
    overlay: _Overlay,
    clip_box: Any,
) -> Image.Image:
    _frames_for(backend, scene, panes, style, clip_box)
    if len(panes) == 1:
        pane = panes[0]
        if view == "sheet":  # Too small for four panes or any annotation.
            return pane.frame.image
        _annotate(pane, scene, labels, heat_legend, overlay)
        return pane.frame.image
    canvas = Image.new("RGB", (size, size), (235, 239, 243))
    draw = ImageDraw.Draw(canvas)
    for pane in panes:
        _annotate(pane, scene, labels, heat_legend, overlay)
        canvas.paste(pane.frame.image, (pane.x, pane.y))
        draw.rectangle(
            (pane.x, pane.y, pane.x + pane.width - 1, pane.y + pane.height - 1),
            outline=(185, 192, 199),
            width=1,
        )
    return canvas


@lru_cache(maxsize=16)
def _font(size: int) -> Any:
    try:
        return ImageFont.load_default(size=size)
    except (ImportError, OSError, TypeError):
        return ImageFont.load_default()


def _annotate(
    pane: _Pane,
    scene: RenderScene,
    labels: list[_Label],
    heat_legend: _HeatLegend | None,
    overlay: _Overlay,
) -> None:
    image = pane.frame.image
    camera = pane.camera
    width, height = image.size
    if min(width, height) < 64:
        return
    axis_h = _dominant_axis(camera.right)
    axis_v = _dominant_axis(camera.up)
    aligned = axis_h is not None and axis_v is not None
    if aligned and min(width, height) >= 200:
        _draw_grid(pane, axis_h, axis_v)
    draw = ImageDraw.Draw(image)
    text_size = max(10, min(15, width // 40))
    font = _font(text_size)
    small = _font(max(9, text_size - 2))
    margin = max(8, min(16, width // 48))
    draw.rectangle((0, 0, width - 1, height - 1), outline=(207, 213, 219), width=1)
    per_pixel = 2.0 * camera.parallel_scale / height
    title = f"{pane.view.upper()}   1 px = {_fmt_length(per_pixel)} {_unit_for_label(scene.units)}"
    if draw.textlength(title, font=font) > width - 2 * margin:
        title = pane.view.upper()
    draw.text((margin, margin), title, fill=_INK, font=font)
    y_text = margin + text_size + 3
    if scene.section_label:
        section_text = scene.section_label
        while len(section_text) > 3 and draw.textlength(section_text, font=small) > width - 2 * margin:
            section_text = section_text[:-4] + "..."
        draw.text((margin, y_text), section_text, fill=_INK_SOFT, font=small)
        y_text += text_size + 2
        if not scene.faces:
            text = "No geometry retained"
            text_width = draw.textlength(text, font=font)
            draw.text(((width - text_width) / 2, height / 2), text, fill=_INK_SOFT, font=font)
    if aligned and min(width, height) >= 200:
        _draw_rulers(draw, camera, width, height, axis_h, axis_v, small)
    else:
        _draw_scale_bar(draw, camera, scene, width, height, margin, font)
    _draw_axis_triad(draw, camera, width, height, margin, small)
    taken: list[tuple[float, float, float, float]] = []
    if scene.faces and overlay.dimensions != "none" and min(width, height) >= 200:
        if aligned:
            _draw_overall_dimensions(draw, pane, scene, axis_h, axis_v, small, taken)
        if overlay.dimensions == "auto":
            _draw_round_callouts(draw, pane, overlay, scene, small, taken)
    if heat_legend:
        _draw_heat_legend(draw, heat_legend, width, height, margin, small)
        note = heat_legend.heuristic
        if draw.textlength(note, font=small) > width - 2 * margin:
            note = note.split(";")[0]
        if width >= 200:
            draw.text((margin, y_text), note, fill=_INK_SOFT, font=small)
    if labels:
        _draw_labels(draw, pane, labels, scene, margin, small, taken)
    _draw_marks(draw, pane, overlay, small, taken)


def _dominant_axis(direction: Vec3) -> tuple[int, float] | None:
    axis = max(range(3), key=lambda index: abs(direction[index]))
    if abs(abs(direction[axis]) - 1.0) > 1.0e-9:
        return None
    return axis, math.copysign(1.0, direction[axis])


def _nice_step(minimum: float) -> float:
    """The smallest of 1, 2 or 5 times a power of ten at or above ``minimum``."""
    magnitude = 10.0 ** math.floor(math.log10(max(minimum, 1.0e-12)))
    for base in (1.0, 2.0, 5.0, 10.0):
        if base * magnitude >= minimum * (1.0 - 1.0e-9):
            return base * magnitude
    return 10.0 * magnitude


def _axis_ticks(
    camera_axis: Vec3,
    axis: tuple[int, float],
    focal: Vec3,
    view_span: float,
    pixels: int,
    *,
    flip: bool = False,
) -> tuple[float, list[tuple[int, float, bool]]]:
    """World-aligned ticks across a pane: (pixel, world coordinate, is_major)."""
    index, sign = axis
    center = focal[index]
    per_pixel = view_span / max(pixels, 1)
    major = _nice_step(per_pixel * max(64.0, pixels / 9.0))
    minor = major / 5.0
    half = view_span * 0.5
    low, high = center - half, center + half
    ticks = []
    for step in range(math.ceil(low / minor - 1.0e-9), math.floor(high / minor + 1.0e-9) + 1):
        value = step * minor
        offset = (value - center) * sign / view_span + 0.5
        pixel = (1.0 - offset) * pixels if flip else offset * pixels
        ticks.append((int(round(pixel)), value, step % 5 == 0))
    return major, ticks


def _draw_grid(pane: _Pane, axis_h: tuple[int, float], axis_v: tuple[int, float]) -> None:
    """World grid behind the part, so a coordinate can be read off anywhere in a view."""
    frame = pane.frame
    camera = pane.camera
    width, height = frame.image.size
    if frame.face_ids is None:
        return
    pixels = np.asarray(frame.image).copy()
    empty = (frame.face_ids < 0) & (pixels.min(axis=2) >= 240)
    view_height = 2.0 * camera.parallel_scale
    view_width = view_height * width / max(height, 1)
    _, ticks_h = _axis_ticks(camera.right, axis_h, camera.focal_point, view_width, width)
    _, ticks_v = _axis_ticks(camera.up, axis_v, camera.focal_point, view_height, height, flip=True)
    for is_major_pass, color in ((False, _GRID), (True, _GRID_MAJOR)):
        for pixel, _, major in ticks_h:
            if major == is_major_pass and 0 <= pixel < width:
                column = empty[:, pixel]
                pixels[column, pixel] = color
        for pixel, _, major in ticks_v:
            if major == is_major_pass and 0 <= pixel < height:
                row = empty[pixel, :]
                pixels[pixel, row] = color
    frame.image.paste(Image.fromarray(pixels, mode="RGB"))


def _draw_rulers(
    draw: ImageDraw.ImageDraw,
    camera: CameraSpec,
    width: int,
    height: int,
    axis_h: tuple[int, float],
    axis_v: tuple[int, float],
    font: Any,
) -> None:
    """Real model coordinates along the bottom and left of an axis-aligned view."""
    view_height = 2.0 * camera.parallel_scale
    view_width = view_height * width / max(height, 1)
    _, ticks_h = _axis_ticks(camera.right, axis_h, camera.focal_point, view_width, width)
    _, ticks_v = _axis_ticks(camera.up, axis_v, camera.focal_point, view_height, height, flip=True)
    names = "XYZ"
    for pixel, value, major in ticks_h:
        if 0 <= pixel < width:
            draw.line((pixel, height - 1, pixel, height - (7 if major else 4)), fill=_INK_SOFT)
            if major and 24 < pixel < width - 62:
                text = _fmt_length(value)
                draw.text((pixel - draw.textlength(text, font=font) / 2, height - 21), text, fill=_INK_SOFT, font=font)
    for pixel, value, major in ticks_v:
        if 0 <= pixel < height:
            draw.line((0, pixel, 6 if major else 3, pixel), fill=_INK_SOFT)
            if major and 44 < pixel < height - 26:
                draw.text((9, pixel - 6), _fmt_length(value), fill=_INK_SOFT, font=font)
    draw.text((width - 58, height - 21), names[axis_h[0]], fill=_INK, font=font)
    draw.text((9, 30), names[axis_v[0]], fill=_INK, font=font)


def _text_box(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, font: Any) -> tuple[float, float, float, float]:
    box = draw.textbbox((x, y), text, font=font)
    return (box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1)


def _overlaps(box: tuple[float, float, float, float], taken: list[tuple[float, float, float, float]]) -> bool:
    return any(box[0] < t[2] and box[2] > t[0] and box[1] < t[3] and box[3] > t[1] for t in taken)


def _plate_text(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    font: Any,
    color: tuple[int, int, int],
    taken: list[tuple[float, float, float, float]],
) -> None:
    box = _text_box(draw, x, y, text, font)
    draw.rectangle(box, fill=(255, 255, 255))
    draw.text((x, y), text, fill=color, font=font)
    taken.append(box)


def _arrow(draw: ImageDraw.ImageDraw, tip: tuple[float, float], back: tuple[float, float], color: tuple[int, int, int]) -> None:
    dx, dy = back[0] - tip[0], back[1] - tip[1]
    length = max(math.hypot(dx, dy), 1.0e-6)
    ux, uy = dx / length, dy / length
    size = 6.0
    draw.polygon(
        (
            tip,
            (tip[0] + ux * size - uy * size * 0.32, tip[1] + uy * size + ux * size * 0.32),
            (tip[0] + ux * size + uy * size * 0.32, tip[1] + uy * size - ux * size * 0.32),
        ),
        fill=color,
    )


def _draw_overall_dimensions(
    draw: ImageDraw.ImageDraw,
    pane: _Pane,
    scene: RenderScene,
    axis_h: tuple[int, float],
    axis_v: tuple[int, float],
    font: Any,
    taken: list[tuple[float, float, float, float]],
) -> None:
    """Part extents as dimension lines, from the exact bounding box."""
    camera = pane.camera
    width, height = pane.frame.image.size
    low, high = scene.bounds
    a = _project(low, camera, width, height)
    b = _project(high, camera, width, height)
    x0, x1 = sorted((a[0], b[0]))
    y0, y1 = sorted((a[1], b[1]))
    units = _unit_for_label(scene.units)
    gap = 16.0
    size_h = high[axis_h[0]] - low[axis_h[0]]
    size_v = high[axis_v[0]] - low[axis_v[0]]
    color = _DIMENSION_INK
    if x1 - x0 > 28 and y1 + gap + 20 < height:
        y = y1 + gap
        draw.line((x0, y1 + 3, x0, y + 4), fill=color)
        draw.line((x1, y1 + 3, x1, y + 4), fill=color)
        draw.line((x0, y, x1, y), fill=color)
        _arrow(draw, (x0, y), (x1, y), color)
        _arrow(draw, (x1, y), (x0, y), color)
        text = f"{_fmt_length(size_h)} {units}"
        _plate_text(draw, (x0 + x1 - draw.textlength(text, font=font)) / 2, y - 7, text, font, color, taken)
    if y1 - y0 > 28 and x0 - gap - 8 > 0:
        x = x0 - gap
        draw.line((x0 - 3, y0, x - 4, y0), fill=color)
        draw.line((x0 - 3, y1, x - 4, y1), fill=color)
        draw.line((x, y0, x, y1), fill=color)
        _arrow(draw, (x, y0), (x, y1), color)
        _arrow(draw, (x, y1), (x, y0), color)
        text = f"{_fmt_length(size_v)} {units}"
        text_width = draw.textlength(text, font=font)
        _plate_text(draw, max(2.0, x - text_width / 2), (y0 + y1) / 2 - 7, text, font, color, taken)


def _pixel_groups(face_ids: np.ndarray) -> dict[int, dict[str, Any]]:
    """Per visible face: pixel count, pixel box, and an interior pixel that a label can point at."""
    if face_ids is None:
        return {}
    rows, cols = np.nonzero(face_ids >= 0)
    if not len(rows):
        return {}
    interior = face_ids.copy()
    # A pixel with the same face on all four sides, twice over, is safely inside that face.
    for _ in range(2):
        same = np.zeros_like(interior, dtype=bool)
        same[1:-1, 1:-1] = (
            (interior[1:-1, 1:-1] == interior[:-2, 1:-1])
            & (interior[1:-1, 1:-1] == interior[2:, 1:-1])
            & (interior[1:-1, 1:-1] == interior[1:-1, :-2])
            & (interior[1:-1, 1:-1] == interior[1:-1, 2:])
        )
        interior = np.where(same, interior, -1)
    ids = face_ids[rows, cols]
    order = np.argsort(ids, kind="stable")
    rows, cols, ids = rows[order], cols[order], ids[order]
    starts = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1]])
    stops = np.r_[starts[1:], len(ids)]
    groups = {}
    for start, stop in zip(starts.tolist(), stops.tolist()):
        face_rows, face_cols = rows[start:stop], cols[start:stop]
        mid_r, mid_c = float(face_rows.mean()), float(face_cols.mean())
        inside = interior[face_rows, face_cols] >= 0
        pool_r, pool_c = (face_rows[inside], face_cols[inside]) if inside.any() else (face_rows, face_cols)
        best = int(np.argmin((pool_r - mid_r) ** 2 + (pool_c - mid_c) ** 2))
        groups[int(ids[start])] = {
            "pixels": stop - start,
            "box": [int(face_cols.min()), int(face_rows.min()), int(face_cols.max()), int(face_rows.max())],
            "point": (int(pool_c[best]), int(pool_r[best])),
        }
    return groups


def _is_visible_point(pane: _Pane, point: Vec3) -> bool | None:
    """Whether nothing covers a world point in this view, from the depth buffer."""
    frame = pane.frame
    if frame is None or frame.height is None:
        return None
    camera = pane.camera
    height_px, width_px = frame.height.shape
    x, y = _project(point, camera, width_px, height_px)
    if not (0 <= x < width_px and 0 <= y < height_px):
        return False
    toward = _normalize(_subtract(camera.position, camera.focal_point))
    own = _dot(_subtract(point, camera.focal_point), toward)
    surface = float(frame.height[int(y), int(x)])
    per_pixel = 2.0 * camera.parallel_scale / height_px
    return math.isnan(surface) or surface <= own + per_pixel * 4.0


def _rim_visible(pane: _Pane, item: dict[str, Any], toward: Vec3) -> bool:
    """Whether the near end of a cylinder can be seen. Its wall has no pixels when viewed end-on."""
    sign = 1.0 if _dot(item["axis"], toward) > 0.0 else -1.0
    end = tuple(item["center"][i] + item["axis"][i] * sign * item["length"] * 0.5 for i in range(3))
    camera = pane.camera
    for across, rise in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
        rim = tuple(end[i] + item["radius"] * (camera.right[i] * across + camera.up[i] * rise) for i in range(3))
        if _is_visible_point(pane, rim):
            return True
    return False


def _draw_round_callouts(
    draw: ImageDraw.ImageDraw,
    pane: _Pane,
    overlay: _Overlay,
    scene: RenderScene,
    font: Any,
    taken: list[tuple[float, float, float, float]],
) -> None:
    """Diameters and radii of round faces seen end-on, where their true size shows."""
    camera = pane.camera
    frame = pane.frame
    if frame.face_ids is None:
        return
    width, height = frame.image.size
    toward = _normalize(_subtract(camera.position, camera.focal_point))
    per_unit = height / (2.0 * camera.parallel_scale)
    done: set[tuple[int, int, int]] = set()
    drawn = 0
    for item in overlay.round_faces:
        if drawn >= 14:
            break
        if abs(_dot(item["axis"], toward)) < 0.9999 or not _rim_visible(pane, item, toward):
            continue
        radius_px = item["radius"] * per_unit
        center_x, center_y = _project(item["center"], camera, width, height)
        if radius_px < 3.0 or not (0 <= center_x < width and 0 <= center_y < height):
            continue
        key = (int(round(center_x)), int(round(center_y)), int(round(radius_px)))
        if key in done:
            continue
        done.add(key)
        text = item["text"]
        directions = [
            (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
            for angle in (-45.0, -135.0, 45.0, 135.0, -90.0, 0.0, 180.0, 90.0)
        ]
        if item["arc_toward"] is not None:
            toward_x, toward_y = _project(item["arc_toward"], camera, width, height)
            length = math.hypot(toward_x - center_x, toward_y - center_y)
            if length < 1.0e-6:
                continue
            directions = [((toward_x - center_x) / length, (toward_y - center_y) / length)]
        for cos, sin in directions:
            rim = (center_x + cos * radius_px, center_y + sin * radius_px)
            elbow = (rim[0] + cos * 16.0, rim[1] + sin * 16.0)
            text_width = draw.textlength(text, font=font)
            x = elbow[0] + 2 if cos >= 0 else elbow[0] - text_width - 2
            y = elbow[1] - 6
            box = _text_box(draw, x, y, text, font)
            if box[0] >= 2 and box[2] <= width - 2 and box[1] >= 30 and box[3] <= height - 24 and not _overlaps(box, taken):
                draw.line((rim, elbow), fill=_DIMENSION_INK)
                _arrow(draw, rim, elbow, _DIMENSION_INK)
                _plate_text(draw, x, y, text, font, _DIMENSION_INK, taken)
                drawn += 1
                break


def _draw_marks(
    draw: ImageDraw.ImageDraw,
    pane: _Pane,
    overlay: _Overlay,
    font: Any,
    taken: list[tuple[float, float, float, float]],
) -> None:
    """Measurements, probes and the thinnest-wall marker."""
    camera = pane.camera
    width, height = pane.frame.image.size
    for measure in overlay.measures:
        a = _project(measure["point_a"], camera, width, height)
        b = _project(measure["point_b"], camera, width, height)
        draw.line((a, b), fill=_MEASURE_INK, width=2)
        for tip, back in ((a, b), (b, a)):
            if math.dist(a, b) > 14:
                _arrow(draw, tip, back, _MEASURE_INK)
            else:
                draw.ellipse((tip[0] - 2, tip[1] - 2, tip[0] + 2, tip[1] + 2), fill=_MEASURE_INK)
        text = _fmt_length(measure["distance"])
        x, y = (a[0] + b[0]) / 2 + 5, (a[1] + b[1]) / 2 - 7
        if 0 <= x < width - 10 and 0 <= y < height - 10:
            _plate_text(draw, x, y, text, font, _MEASURE_INK, taken)
    for mark in overlay.marks:
        x, y = _project(mark["point"], camera, width, height)
        if not (0 <= x < width and 0 <= y < height):
            continue
        visible = _is_visible_point(pane, mark["point"])
        draw.line((x - 9, y, x + 9, y), fill=_BELOW_LIMIT, width=2)
        draw.line((x, y - 9, x, y + 9), fill=_BELOW_LIMIT, width=2)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), outline=_BELOW_LIMIT, width=2)
        suffix = "" if visible is not False else " (behind)"
        _plate_text(draw, x + 11, y - 18, mark["text"] + suffix, font, _BELOW_LIMIT, taken)
    for probe in overlay.probes:
        where = probe.get("pixel")
        if where is not None and where[0] != pane.view:
            continue
        if where is not None:
            x, y = where[1], where[2]
        else:
            point = probe["result"].get("point")
            if point is None:
                continue
            x, y = _project(point, camera, width, height)
        if not (0 <= x < width and 0 <= y < height):
            continue
        draw.line((x - 8, y, x - 3, y), fill=_MEASURE_INK)
        draw.line((x + 3, y, x + 8, y), fill=_MEASURE_INK)
        draw.line((x, y - 8, x, y - 3), fill=_MEASURE_INK)
        draw.line((x, y + 3, x, y + 8), fill=_MEASURE_INK)
        _plate_text(draw, x + 9, y + 3, f"P{probe['result']['id']}", font, _MEASURE_INK, taken)


def _draw_scale_bar(
    draw: ImageDraw.ImageDraw,
    camera: CameraSpec,
    scene: RenderScene,
    width: int,
    height: int,
    margin: int,
    font: Any,
) -> None:
    pixels_per_unit = height / max(2.0 * camera.parallel_scale, 1.0e-12)
    target = min(120.0, max(35.0, width * 0.20))
    raw_length = target / max(pixels_per_unit, 1.0e-12)
    magnitude = 10.0 ** math.floor(math.log10(max(raw_length, 1.0e-12)))
    length = min((1.0, 2.0, 5.0, 10.0), key=lambda base: abs(base * magnitude - raw_length)) * magnitude
    pixels = max(1, int(round(length * pixels_per_unit)))
    x0 = margin
    y = height - margin - 12
    draw.line((x0, y, x0 + pixels, y), fill=_INK, width=2)
    draw.line((x0, y - 4, x0, y + 4), fill=_INK, width=2)
    draw.line((x0 + pixels, y - 4, x0 + pixels, y + 4), fill=_INK, width=2)
    draw.text((x0, y - 18), f"{_fmt(length)} {_unit_for_label(scene.units)}", fill=_INK, font=font)


def _draw_axis_triad(
    draw: ImageDraw.ImageDraw,
    camera: CameraSpec,
    width: int,
    height: int,
    margin: int,
    font: Any,
) -> None:
    origin = (width - margin - 34, height - margin - 40)
    axis_length = max(15, min(28, width // 18))
    axes = (
        ((1.0, 0.0, 0.0), (198, 44, 52), "X"),
        ((0.0, 1.0, 0.0), (38, 139, 85), "Y"),
        ((0.0, 0.0, 1.0), (43, 97, 184), "Z"),
    )
    for direction, color, label in axes:
        x = _dot(direction, camera.right)
        y = -_dot(direction, camera.up)
        magnitude = math.hypot(x, y)
        if magnitude < 0.08:
            continue
        end = (
            int(round(origin[0] + axis_length * x / magnitude)),
            int(round(origin[1] + axis_length * y / magnitude)),
        )
        draw.line((origin[0], origin[1], end[0], end[1]), fill=color, width=2)
        draw.ellipse((end[0] - 2, end[1] - 2, end[0] + 2, end[1] + 2), fill=color)
        draw.text((end[0] + 2, end[1] - 5), label, fill=color, font=font)
    draw.ellipse(
        (origin[0] - 2, origin[1] - 2, origin[0] + 2, origin[1] + 2),
        fill=(42, 48, 54),
    )


def _draw_heat_legend(
    draw: ImageDraw.ImageDraw,
    legend: _HeatLegend,
    width: int,
    height: int,
    margin: int,
    font: Any,
) -> None:
    bar_width = max(96, min(190, width // 3))
    x0 = (width - bar_width) // 2
    y0 = height - margin - 40
    for offset in range(bar_width):
        fraction = offset / max(bar_width - 1, 1)
        color = _heat_color(1.0 - fraction if legend.reverse else fraction)
        draw.line((x0 + offset, y0, x0 + offset, y0 + 7), fill=color)
    draw.rectangle((x0 - 1, y0 - 1, x0 + bar_width, y0 + 8), outline=_INK_SOFT)
    title = legend.kind
    if draw.textlength(title, font=font) > bar_width + 40:
        title = title.split()[0]
    draw.text((x0 + (bar_width - draw.textlength(title, font=font)) / 2, y0 - 14), title, fill=_INK, font=font)
    low = f"{_fmt_length(legend.minimum)} {legend.units if legend.units == 'deg' else _unit_for_label(legend.units)}"
    high = f"{_fmt_length(legend.maximum)} {legend.units if legend.units == 'deg' else _unit_for_label(legend.units)}"
    draw.text((x0, y0 + 10), low, fill=_INK, font=font)
    draw.text((x0 + bar_width - draw.textlength(high, font=font), y0 + 10), high, fill=_INK, font=font)


def _visible_labels(labels: list[_Label], camera: CameraSpec, scene: RenderScene) -> list[_Label]:
    """Without a pixel map, face and edge labels are kept only where a ray reaches their anchor."""
    checked = [label for label in labels if label.kind in {"face", "edge"}]
    if not checked:
        return labels
    toward = np.asarray(_normalize(_subtract(camera.position, camera.focal_point)), dtype=np.float64)
    flags = visible_from(
        scene_triangles(scene),
        np.asarray([label.anchor for label in checked], dtype=np.float64),
        toward,
    )
    hidden = {label.number for label, flag in zip(checked, flags, strict=True) if not flag}
    return [label for label in labels if label.number not in hidden]


def _label_targets(pane: _Pane, labels: list[_Label], scene: RenderScene) -> list[tuple[_Label, float, float, int]]:
    """Where each label should point in this view, biggest visible region first.

    A pixel inside the visible part of the entity, so that a label can never point at something
    the camera cannot see, even when the entity's middle is hidden.
    """
    camera = pane.camera
    width, height = pane.frame.image.size
    if pane.frame.face_ids is None:
        return [
            (label, *_project(label.anchor, camera, width, height), 0)
            for label in _visible_labels(labels, camera, scene)
        ]
    groups = _pixel_groups(pane.frame.face_ids)
    by_source = {face.source_index: group for face in scene.faces if (group := groups.get(face.index)) and face.source_index is not None}
    targets = []
    lines = {line.source_index: line for line in scene.lines if line.role != "hatch" and line.source_index is not None}
    for label in labels:
        if label.kind == "face":
            group = by_source.get(label.index)
            if group is not None:
                targets.append((label, float(group["point"][0]), float(group["point"][1]), group["pixels"]))
        elif label.kind == "edge":
            line = lines.get(label.index)
            if line is None:
                continue
            count = len(line.points)
            for offset in sorted(range(count), key=lambda position: abs(position - (count - 1) / 2)):
                point = line.points[offset]
                if _is_visible_point(pane, point):
                    x, y = _project(point, camera, width, height)
                    targets.append((label, x, y, count))
                    break
        else:
            members = [group for face in scene.faces if face.feature == label.feature and (group := groups.get(face.index))]
            if members:
                largest = max(members, key=lambda group: group["pixels"])
                targets.append((label, float(largest["point"][0]), float(largest["point"][1]), sum(g["pixels"] for g in members)))
    targets.sort(key=lambda item: (-item[3], item[0].number))
    return targets


def _draw_labels(
    draw: ImageDraw.ImageDraw,
    pane: _Pane,
    labels: list[_Label],
    scene: RenderScene,
    margin: int,
    font: Any,
    taken: list[tuple[float, float, float, float]],
) -> None:
    width, height = pane.frame.image.size
    radius = max(8, min(11, width // 64))
    lower_bound = max(margin + 28, 34)
    upper_bound = height - max(margin + 28, 34)
    drawn = []
    for label, anchor_x, anchor_y, _ in _label_targets(pane, labels, scene):
        chosen = None
        for distance in (radius * 1.8, radius * 2.8, radius * 4.0, radius * 6.0):
            for dx, dy in ((1, -1), (1, 1), (-1, -1), (-1, 1), (1, 0), (-1, 0), (0, -1), (0, 1)):
                x = int(round(min(width - radius - 2, max(radius + 2, anchor_x + dx * distance))))
                y = int(round(min(upper_bound, max(lower_bound, anchor_y + dy * distance))))
                box = (x - radius - 1, y - radius - 1, x + radius + 1, y + radius + 1)
                if not _overlaps(box, taken):
                    chosen = (x, y)
                    taken.append(box)
                    break
            if chosen:
                break
        if chosen is None:
            continue  # Too crowded here. labels.json and inspection.json still list the entity.
        drawn.append(label.number)
        draw.line((anchor_x, anchor_y, chosen[0], chosen[1]), fill=(49, 57, 65), width=1)
        draw.ellipse((anchor_x - 2, anchor_y - 2, anchor_x + 2, anchor_y + 2), fill=(49, 57, 65))
        draw.ellipse(
            (chosen[0] - radius, chosen[1] - radius, chosen[0] + radius, chosen[1] + radius),
            fill=(255, 255, 255),
            outline=(37, 44, 51),
            width=1,
        )
        number = str(label.number)
        box = draw.textbbox((0, 0), number, font=font)
        draw.text(
            (chosen[0] - (box[2] - box[0]) / 2 - box[0], chosen[1] - (box[3] - box[1]) / 2 - box[1]),
            number,
            fill=(24, 29, 34),
            font=font,
        )
    pane.drawn = tuple(sorted(drawn))


def _project(point: Vec3, camera: CameraSpec, width: int, height: int) -> tuple[float, float]:
    relative = _subtract(point, camera.focal_point)
    horizontal = _dot(relative, camera.right)
    vertical = _dot(relative, camera.up)
    view_height = 2.0 * camera.parallel_scale
    view_width = view_height * width / max(height, 1)
    return (
        (horizontal / view_width + 0.5) * width,
        (0.5 - vertical / view_height) * height,
    )


def _heat_colors(values: np.ndarray) -> np.ndarray:
    """Color ramp for an array of 0-1 values, as N x 3 unsigned bytes."""
    stops = np.asarray((0.0, 0.34, 0.67, 1.0))
    colors = np.asarray(((45, 92, 173), (43, 166, 171), (241, 211, 73), (207, 57, 48)), dtype=np.float64)
    clipped = np.clip(values, 0.0, 1.0)
    return np.stack([np.interp(clipped, stops, colors[:, channel]) for channel in range(3)], axis=1).round().astype(np.uint8)


def _heat_color(value: float) -> tuple[int, int, int]:
    return tuple(_heat_colors(np.asarray([value]))[0].tolist())


def _tuples(colors: np.ndarray) -> tuple[tuple[int, int, int], ...]:
    return tuple(map(tuple, colors.tolist()))


def _apply_heatmap(
    request: dict[str, Any],
    scene: RenderScene,
    shape: Any,
    shape_faces: list[Any],
    topology: exact.Topology | None,
    overlay: _Overlay,
    *,
    bounds: tuple[Vec3, Vec3],
    units: str,
    section: _Section | None,
    tolerance: float,
    feature_maps: tuple[dict[int, str], dict[int, str]],
) -> tuple[_HeatLegend | None, dict[str, Any] | None]:
    kind = request["heatmap"]
    fixed = request["heatmap_range"]
    scene.data_colors = kind is not None
    if kind == "thickness":
        return _thickness_map(request, scene, shape, topology, overlay, bounds, units, section, tolerance, feature_maps)
    if kind == "overhang":
        low, high = fixed or (0.0, 180.0)
        legend = _HeatLegend(
            "OVERHANG ANGLE FROM BUILD",
            low,
            high,
            "deg",
            f"triangle angle from build direction {_fmt_vector(request['direction'])}; 0 up, 90 wall, 180 down",
        )
        report = _apply_overhang_colors(scene, low, high, request["direction"])
        return legend, {"kind": "overhang", "direction": list(request["direction"]), **report}
    if kind == "draft":
        low, high = fixed or (-10.0, 10.0)
        legend = _HeatLegend(
            "DRAFT ANGLE",
            low,
            high,
            "deg",
            f"pull {_fmt_vector(request['direction'])}; near 0 has no draft, below 0 faces away from pull",
        )
        report = _apply_draft_colors(scene, low, high, request["direction"])
        return legend, {"kind": "draft", "direction": list(request["direction"]), **report}
    if kind == "curvature":
        return _curvature_map(scene, shape_faces, _bounds_diagonal(bounds), fixed, units)
    for face in scene.faces:
        if face.highlighted:
            face.color = _HIGHLIGHT
        elif face.section_cap and request["style"] == "shaded":
            face.color = _SECTION_CAP
        else:
            face.color = _BASE_COLORS[request["style"]]
    return None, None


def _thickness_map(
    request: dict[str, Any],
    scene: RenderScene,
    shape: Any,
    topology: exact.Topology | None,
    overlay: _Overlay,
    bounds: tuple[Vec3, Vec3],
    units: str,
    section: _Section | None,
    tolerance: float,
    feature_maps: tuple[dict[int, str], dict[int, str]],
) -> tuple[_HeatLegend, dict[str, Any]]:
    started = time.perf_counter()
    target = None
    if section and scene.faces:
        # Readings come from the uncut solid, so the cut itself never reads as a thin wall.
        original_faces = list(shape.faces())
        original_edges = list(shape.edges())
        target = _make_scene(
            shape, original_faces, original_edges,
            list(range(len(original_faces))), list(range(len(original_edges))),
            feature_maps[0], feature_maps[1], set(), set(), set(),
            bounds=bounds, units=units, section=None, tolerance=tolerance, topology=None,
        )
    walls = wall_samples(scene, target)
    measured = walls.values[np.isfinite(walls.values)]
    fixed = request["heatmap_range"]
    limit = request["min_wall"]
    if fixed:
        low, high = fixed
    else:
        low, high = _expanded_range(
            float(measured.min()) if len(measured) else 0.0,
            float(np.percentile(measured, 99.0)) if len(measured) else 1.0,
        )
    legend = _HeatLegend(
        "WALL THICKNESS",
        low,
        high,
        scene.units,
        f"{len(measured)} rays, {_fmt_length(walls.spacing)} {_unit_for_label(scene.units)} apart; grey = no reading"
        + (f"; magenta < {_fmt_length(limit)}" if limit else ""),
        reverse=True,
        limit=limit,
    )
    thinnest = None
    if topology is not None and len(measured):
        thinnest = _refine_thinnest(walls, shape, topology, scene, tolerance, _bounds_diagonal(bounds))
    if thinnest and not fixed:
        # Mesh chords read a hair thin on curved walls; the scale should start at the true value.
        low = min(thinnest["thickness"], high - 1.0e-9)
    legend = replace(legend, minimum=low, maximum=high)
    span = max(high - low, 1.0e-12)
    per_face = []
    for face in scene.faces:
        entry = walls.faces.get(face.index)
        if entry is None:
            face.color = _HIGHLIGHT if face.highlighted else _NO_READING
            continue
        points, triangles, values = entry
        face.points, face.triangles, face.uv = points, triangles, None
        if face.highlighted:
            face.color = _HIGHLIGHT
            face.triangle_colors = ()
        else:
            colors = _heat_colors(1.0 - (np.nan_to_num(values, nan=low) - low) / span)
            colors[~np.isfinite(values)] = _NO_READING
            if limit:
                # Chords cut slightly into curved walls, so allow the mesh error before flagging one.
                colors[np.isfinite(values) & (values < limit - tolerance * 2.0)] = _BELOW_LIMIT
            face.triangle_colors = _tuples(colors)
        readings = values[np.isfinite(values)]
        if len(readings) and face.source_index is not None:
            per_face.append(
                {
                    "selector": f"face:{face.source_index}",
                    "min": exact._clean(readings.min()),
                    "median": exact._clean(np.median(readings)),
                    "max": exact._clean(readings.max()),
                    "samples": int(len(readings)),
                }
            )
    report: dict[str, Any] = {
        "kind": "thickness",
        "method": "inward rays against the mesh, thinnest re-measured on the B-rep",
        "rays": int(len(walls.values)),
        "readings": int(len(measured)),
        "ray_spacing": exact._clean(walls.spacing),
        "mesh_error_allowance": exact._clean(tolerance * 2.0),
        "note": "Sampled wall search, not a certified global minimum. B-rep refinement measures the selected candidate.",
        "per_face": sorted(per_face, key=lambda item: item["min"]),
    }
    if limit:
        report["min_wall"] = limit
        report["rays_below_min_wall"] = int((measured < limit - tolerance * 2.0).sum())
    if thinnest:
        report["thinnest"] = thinnest
        overlay.marks.append({"point": tuple(thinnest["point_a"]), "text": f"min {_fmt_length(thinnest['thickness'])}"})
    _LOG.info("thickness map measured in %.2f ms", (time.perf_counter() - started) * 1000.0)
    return legend, report


def _refine_thinnest(
    walls: WallSamples,
    shape: Any,
    topology: exact.Topology,
    scene: RenderScene,
    tolerance: float,
    diagonal: float,
) -> dict[str, Any] | None:
    """Re-measure the thinnest mesh readings on the true surfaces, and report the smallest."""
    rays = exact.Rays(shape.wrapped, topology, diagonal)
    readable = np.flatnonzero(np.isfinite(walls.values))
    if not len(readable):
        return None
    order = readable[np.argsort(walls.values[readable], kind="stable")][:EXACT_REFINEMENTS]
    reach = max(tolerance * 4.0, diagonal * 1.0e-4, 1.0e-6)
    best: dict[str, Any] | None = None
    for row in order.tolist():
        wall = rays.wall_at(tuple(walls.origins[row].tolist()), tuple(walls.directions[row].tolist()), reach)
        if wall is None:
            continue
        if best is None or wall["thickness"] < best["thickness"]:
            best = wall
    if best is None:
        return None
    entry, leaving = best["entry"], best["exit"]
    result = {
        "thickness": exact._clean(best["thickness"]),
        "method": "brep-ray",
        "point_a": exact._xyz(entry["point"]),
        "point_b": exact._xyz(leaving["point"]),
        "face_a": _selector(topology.face_index(entry["face"])),
        "face_b": _selector(topology.face_index(leaving["face"])),
    }
    # The ray runs along a facet normal. For the true minimum, ask for the closest points of the two
    # faces, but only trust them if the straight line between them runs through material.
    closest = exact.distance(entry["face"], leaving["face"])
    if closest and 1.0e-7 < closest["distance"] <= best["thickness"] + 1.0e-9:
        middle = tuple((a + b) * 0.5 for a, b in zip(closest["point_a"], closest["point_b"]))
        toward_exit = _normalize(_subtract(closest["point_b"], closest["point_a"]))
        material_path = rays.wall_at(tuple(closest["point_a"]), toward_exit, 1.0e-6)
        if (exact.is_inside(shape.wrapped, middle) and material_path is not None
                and abs(material_path["thickness"] - closest["distance"]) <= max(diagonal * 1.0e-9, 1.0e-6)):
            result.update(
                thickness=closest["distance"],
                method="brep-extrema",
                point_a=closest["point_a"],
                point_b=closest["point_b"],
            )
    return result


def _selector(index: int | None) -> str | None:
    return f"face:{index}" if index is not None else None


def _triangle_normals(face: RenderFace) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    corners = np.asarray(face.points, dtype=np.float64)[np.asarray(face.triangles, dtype=np.int64)]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    lengths = np.linalg.norm(cross, axis=1)
    usable = lengths > 1.0e-14
    normals = np.zeros_like(cross)
    normals[usable] = cross[usable] / lengths[usable, None]
    return normals, usable, lengths * 0.5


def _apply_thickness_colors(scene: RenderScene, values: dict[int, float], legend: _HeatLegend) -> None:
    minimum, maximum = _expanded_range(legend.minimum, legend.maximum)
    for face in scene.faces:
        if face.highlighted:
            face.color = _HIGHLIGHT
        elif face.index in values:
            fraction = (values[face.index] - minimum) / (maximum - minimum)
            face.color = _heat_color(1.0 - fraction if legend.reverse else fraction)
        else:
            face.color = _NO_READING


def _apply_overhang_colors(scene: RenderScene, minimum: float, maximum: float, direction: Vec3 = (0.0, 0.0, 1.0)) -> dict[str, Any]:
    minimum, maximum = _expanded_range(minimum, maximum)
    build = np.asarray(direction)
    areas = np.zeros(4)
    for face in scene.faces:
        if not len(face.triangles):
            continue
        normals, usable, triangle_areas = _triangle_normals(face)
        angles = np.degrees(np.arccos(np.clip(normals @ build, -1.0, 1.0)))
        colors = _heat_colors((angles - minimum) / (maximum - minimum))
        colors[~usable] = _NO_READING
        if face.highlighted:
            colors[:] = _HIGHLIGHT
        face.triangle_colors = _tuples(colors)
        if not face.section_cap:
            areas += (
                triangle_areas[usable].sum(),
                triangle_areas[usable & (angles >= 135.0)].sum(),
                triangle_areas[usable & (angles >= 180.0 - 1.0e-6)].sum(),
                triangle_areas[usable & (angles > 90.0 + 1.0e-6)].sum(),
            )
    return {
        "total_area": exact._clean(areas[0]),
        "area_past_45deg_overhang": exact._clean(areas[1]),
        "area_facing_straight_down": exact._clean(areas[2]),
        "area_facing_downward": exact._clean(areas[3]),
    }


def _apply_draft_colors(scene: RenderScene, minimum: float, maximum: float, direction: Vec3) -> dict[str, Any]:
    pull = np.asarray(direction)
    no_draft = 0.0
    total = 0.0
    per_face = []
    for face in scene.faces:
        if not len(face.triangles):
            continue
        normals, usable, triangle_areas = _triangle_normals(face)
        draft = np.degrees(np.arcsin(np.clip(normals @ pull, -1.0, 1.0)))
        colors = _heat_colors((draft - minimum) / (maximum - minimum))
        colors[~usable] = _NO_READING
        if face.highlighted:
            colors[:] = _HIGHLIGHT
        face.triangle_colors = _tuples(colors)
        if not face.section_cap and usable.any():
            total += float(triangle_areas[usable].sum())
            no_draft += float(triangle_areas[usable & (np.abs(draft) < 1.0)].sum())
            if face.source_index is not None:
                per_face.append(
                    {
                        "selector": f"face:{face.source_index}",
                        "min_deg": exact._clean(draft[usable].min()),
                        "max_deg": exact._clean(draft[usable].max()),
                    }
                )
    return {
        "total_area": exact._clean(total),
        "area_under_1deg": exact._clean(no_draft),
        "per_face": sorted(per_face, key=lambda item: abs(item["min_deg"])),
    }


def _curvature_map(
    scene: RenderScene,
    shape_faces: list[Any],
    diagonal: float,
    fixed: list[float] | None,
    units: str,
) -> tuple[_HeatLegend, dict[str, Any]]:
    """Smallest local bend radius per triangle, from the surface itself. Flat faces stay grey."""
    radii: dict[int, np.ndarray] = {}
    for face in scene.faces:
        wrapped = getattr(shape_faces[face.index], "wrapped", None)
        if wrapped is None or face.uv is None or not len(face.triangles):
            continue
        adaptor = exact.surface_adaptor(wrapped)
        middles = np.asarray(face.uv)[np.asarray(face.triangles)].mean(axis=1)
        values = np.asarray([exact.largest_curvature(adaptor, u, v) for u, v in middles.tolist()])
        radii[face.index] = np.where(values > 1.0 / (diagonal * 1000.0), 1.0 / np.maximum(values, 1.0e-300), np.inf)
    finite = np.concatenate([values[np.isfinite(values)] for values in radii.values()]) if radii else np.zeros(0)
    if fixed:
        low, high = fixed
    elif len(finite):
        low, high = _expanded_range(float(finite.min()), float(finite.max()))
    else:
        low, high = 0.0, 1.0
    low_log = math.log(max(low, 1.0e-9))
    span = max(math.log(max(high, 1.0e-9)) - low_log, 1.0e-9)
    per_face = []
    for face in scene.faces:
        values = radii.get(face.index)
        if values is None:
            face.color = _HIGHLIGHT if face.highlighted else _NO_READING
            continue
        colors = _heat_colors(1.0 - (np.log(np.maximum(np.where(np.isfinite(values), values, high), 1.0e-9)) - low_log) / span)
        colors[~np.isfinite(values)] = _NO_READING
        if face.highlighted:
            colors[:] = _HIGHLIGHT
        face.triangle_colors = _tuples(colors)
        curved = values[np.isfinite(values)]
        if len(curved) and face.source_index is not None:
            per_face.append({"selector": f"face:{face.source_index}", "min_radius": exact._clean(curved.min())})
    legend = _HeatLegend("MIN BEND RADIUS", low, high, units, "from surface curvature, log scale; grey = flat", reverse=True)
    return legend, {
        "kind": "curvature",
        "min_radius": exact._clean(finite.min()) if len(finite) else None,
        "per_face": sorted(per_face, key=lambda item: item["min_radius"]),
    }


def _fmt_vector(vector: Sequence[float]) -> str:
    return "(" + ",".join(_fmt(component) for component in vector) + ")"


def _shape_report(
    ctx: Any,
    shape: Any,
    topology: exact.Topology,
    feature_faces: dict[int, str],
    feature_edges: dict[int, str],
    *,
    include_edge_classes: bool = True,
) -> dict[str, Any]:
    faces = []
    for index, face in enumerate(topology.faces):
        facts = exact.face_facts(face)
        facts["selector"] = f"face:{index}"
        if index in feature_faces:
            facts["feature"] = feature_faces[index]
        faces.append(facts)
    edges = []
    for index, edge in enumerate(topology.edges):
        facts = exact.edge_facts(edge, topology if include_edge_classes else None)
        facts["selector"] = f"edge:{index}"
        if index in feature_edges:
            facts["feature"] = feature_edges[index]
        edges.append(facts)
    report = exact.shape_facts(shape.wrapped)
    report["faces"] = faces
    report["edges"] = edges
    report["counts"] = {"faces": len(faces), "edges": len(edges), "solids": len(list(shape.solids()))}
    return report


def _round_faces(faces: list[dict[str, Any]], scene: RenderScene) -> list[dict[str, Any]]:
    """Cylinders that can be called out on a view, largest first."""
    scene_index = {face.source_index: face.index for face in scene.faces if face.source_index is not None}
    items = []
    for number, facts in enumerate(faces):
        if facts["type"] != "cylinder" or number not in scene_index:
            continue
        whole = facts["sweep_deg"] >= 179.0
        size = _fmt_length(facts["diameter"] if whole else facts["radius"])
        items.append(
            {
                "scene_index": scene_index[number],
                "axis": tuple(facts["axis_direction"]),
                "center": tuple(facts["axis_point"]),
                "radius": facts["radius"],
                "length": facts["axial_length"],
                "text": f"dia {size}" if whole else f"R{size}",
                # A partial cylinder's callout must land on its actual arc.
                "arc_toward": None if whole else tuple(facts["centroid"]),
            }
        )
    items.sort(key=lambda item: (-item["radius"], item["scene_index"]))
    return items


def _section_report(
    section: _Section,
    scene: RenderScene,
    shape_faces: list[Any],
    source_face_indexes: list[int | None],
) -> dict[str, Any]:
    caps = [shape_faces[face.index].wrapped for face in scene.faces if face.section_cap]
    regions = exact.section_loops(caps)
    return {
        "plane": section.plane,
        "point": [exact._clean(value) for value in section.point],
        "normal": [exact._clean(value) for value in section.normal],
        "removed": "the side the normal points to",
        "total_area": exact._clean(sum(region["area"] for region in regions)),
        "regions": regions,
    }


def _run_probes(
    probes: list[Any],
    pictures: dict[str, tuple[int, list[_Pane]]],
    rays: exact.Rays | None,
    solid_rays: exact.Rays | None,
    source_face_indexes: list[int | None],
    units: str,
    clip_box: tuple[Vec3, Vec3] | None = None,
) -> list[dict[str, Any]]:
    """Answer 'what is at this pixel, or along this ray' from the B-rep."""
    results = []
    for number, probe in enumerate(probes, start=1):
        result: dict[str, Any] = {"id": number}
        entry: dict[str, Any] = {"result": result}
        origin, direction = _probe_ray(probe, pictures, entry)
        result["ray"] = {"origin": [exact._clean(c) for c in origin], "direction": [exact._clean(c) for c in direction]}
        hits = rays.hits(origin, direction) if rays is not None else []
        if clip_box is not None:
            hits = [hit for hit in hits if all(
                low - 1.0e-7 <= value <= high + 1.0e-7
                for value, low, high in zip(exact._xyz(hit["point"]), *clip_box)
            )]
        if not hits:
            result["hit"] = False
            results.append(entry)
            continue
        described = rays.describe(hits[0])
        index = described.pop("face_index")
        source = source_face_indexes[index] if index is not None and index < len(source_face_indexes) else None
        result.update(described)
        result.update(hit=True, selector=_selector(source), section_cap=source is None)
        result["distance_from_origin"] = exact._clean(hits[0]["t"])
        normal = result.get("normal")
        if normal and solid_rays is not None and source is not None:
            inward = tuple(-component for component in normal)
            wall = solid_rays.wall_at(tuple(result["point"]), inward, 1.0e-5)
            if wall is not None:
                result["wall_thickness"] = exact._clean(wall["thickness"])
                exit_index = solid_rays.describe(wall["exit"])["face_index"]
                result["wall_exit"] = {"selector": _selector(exit_index), "point": exact._xyz(wall["exit"]["point"])}
        results.append(entry)
    return results


def _probe_ray(probe: Any, pictures: dict[str, tuple[int, list[_Pane]]], entry: dict[str, Any]) -> tuple[Vec3, Vec3]:
    point = _field(probe, "point", None)
    if point is not None:
        direction = _field(probe, "direction", None)
        if direction is None:
            raise ValueError("a probe with a point also needs a direction")
        origin = tuple(float(value) for value in point)
        if len(origin) != 3 or not all(map(math.isfinite, origin)):
            raise ValueError("probe point must be three finite numbers")
        return origin, _unit_vector(direction, "probe direction")
    image = _field(probe, "image", None) or _field(probe, "view", None)
    x, y = _field(probe, "x", None), _field(probe, "y", None)
    if image is None or x is None or y is None:
        raise ValueError("a probe needs either point and direction, or image, x and y")
    picture = pictures.get(str(image))
    if picture is None:
        raise ValueError(f"probe image {image!r} is not among this request's views {sorted(pictures)}")
    x, y = float(x), float(y)
    for pane in picture[1]:
        if pane.x <= x < pane.x + pane.width and pane.y <= y < pane.y + pane.height:
            local_x, local_y = x - pane.x, y - pane.y
            camera = pane.camera
            view_height = 2.0 * camera.parallel_scale
            view_width = view_height * pane.width / pane.height
            toward = _normalize(_subtract(camera.position, camera.focal_point))
            across = (local_x / pane.width - 0.5) * view_width
            rise = (0.5 - local_y / pane.height) * view_height
            origin = tuple(
                camera.focal_point[i] + camera.right[i] * across + camera.up[i] * rise + toward[i] * camera.distance
                for i in range(3)
            )
            entry["pixel"] = (pane.view, local_x, local_y)
            entry["result"]["pixel"] = {"image": str(image), "x": x, "y": y, "view": pane.view}
            return origin, tuple(-component for component in toward)
    raise ValueError(f"probe pixel ({x}, {y}) falls outside every view of image {image!r}")


def _run_measures(ctx: Any, measures: list[Any], shape: Any, topology: exact.Topology | None) -> list[dict[str, Any]]:
    """Exact minimum distance between pairs of selectors, plus the angle where both have a direction."""
    results = []
    for measure in measures:
        a, b = _field(measure, "a", None), _field(measure, "b", None)
        if a is None or b is None:
            raise ValueError("each measure needs a and b selectors")
        from_shapes = _shapes_for(ctx, str(a), shape, topology)
        to_shapes = _shapes_for(ctx, str(b), shape, topology)
        best = None
        for first in from_shapes:
            for second in to_shapes:
                found = exact.distance(first, second)
                if found is not None and (best is None or found["distance"] < best["distance"]):
                    best = found
        if best is None:
            raise ValueError(f"could not measure between {a!r} and {b!r}")
        result = {"a": str(a), "b": str(b), **best}
        result["delta"] = [exact._clean(q - p) for p, q in zip(best["point_a"], best["point_b"])]
        if len(from_shapes) == 1 and len(to_shapes) == 1:
            first_direction = exact.direction_of(from_shapes[0])
            second_direction = exact.direction_of(to_shapes[0])
            if first_direction and second_direction:
                cosine = max(-1.0, min(1.0, _dot(first_direction[1], second_direction[1])))
                result["angle_deg"] = exact._clean(math.degrees(math.acos(cosine)))
        results.append(result)
    return results


def _image_report(size: int, panes: list[_Pane], scene: RenderScene) -> dict[str, Any]:
    """How to turn world points into this image's pixels, and which entities each view shows."""
    names = {face.index: (f"face:{face.source_index}" if face.source_index is not None else f"section_cap:{face.index}") for face in scene.faces}
    result_panes = []
    for pane in panes:
        camera = pane.camera
        view_height = 2.0 * camera.parallel_scale
        view_width = view_height * pane.width / max(pane.height, 1)
        focal = camera.focal_point
        across = [component * pane.width / view_width for component in camera.right]
        rise = [-component * pane.height / view_height for component in camera.up]
        toward = _normalize(_subtract(camera.position, camera.focal_point))
        groups = _pixel_groups(pane.frame.face_ids) if pane.frame is not None and pane.frame.face_ids is not None else {}
        visible = [
            {
                "selector": names.get(index, f"face_index:{index}"),
                "pixels": group["pixels"],
                # Pixel boxes are given in whole-image coordinates, like the pixels of a probe.
                "box": [group["box"][0] + pane.x, group["box"][1] + pane.y, group["box"][2] + pane.x, group["box"][3] + pane.y],
            }
            for index, group in sorted(groups.items())
        ]
        result_panes.append(
            {
                "view": pane.view,
                "rect": [pane.x, pane.y, pane.width, pane.height],
                "pixels_per_unit": exact._clean(pane.height / view_height),
                "units_per_pixel": exact._clean(view_height / pane.height),
                "toward_camera": [exact._clean(component) for component in toward],
                "world_to_pixel": [
                    [exact._clean(c) for c in across]
                    + [exact._clean(pane.x + pane.width * 0.5 - _dot(across, focal))],
                    [exact._clean(c) for c in rise]
                    + [exact._clean(pane.y + pane.height * 0.5 - _dot(rise, focal))],
                ],
                "visible": visible,
                "labels_drawn": list(pane.drawn),
            }
        )
    return {"size": [size, size], "panes": result_panes}


def _expanded_range(minimum: float, maximum: float) -> tuple[float, float]:
    if maximum > minimum:
        return minimum, maximum
    margin = max(abs(minimum) * 0.05, 0.5)
    return minimum - margin, maximum + margin


def _save_png(image: Image.Image, path: Path) -> None:
    buffer = BytesIO()
    image.convert("RGB").save(
        buffer,
        format="PNG",
        optimize=False,
        compress_level=9,
    )
    path.write_bytes(buffer.getvalue())


def _fmt(value: float) -> str:
    return f"{value:.3g}"


def _fmt_length(value: float) -> str:
    """Enough digits to show a real dimension: 80, 12.7, 0.125."""
    text = f"{float(value):.5g}"
    if "e" in text:
        text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _unit_for_label(units: Any) -> str:
    text = str(units or "model units")
    return text if text in {"mm", "in", "cm", "m", "um", "ft"} else "units"


def _average(points: Sequence[Vec3]) -> Vec3:
    if not points:
        return (0.0, 0.0, 0.0)
    return tuple(sum(point[index] for point in points) / len(points) for index in range(3))  # type: ignore[return-value]


def _bounds_center(bounds: tuple[Vec3, Vec3]) -> Vec3:
    return tuple((bounds[0][index] + bounds[1][index]) * 0.5 for index in range(3))  # type: ignore[return-value]


def _triangle_center(points: tuple[Vec3, ...], triangle: tuple[int, int, int]) -> Vec3:
    return tuple(sum(points[index][axis] for index in triangle) / 3.0 for axis in range(3))  # type: ignore[return-value]


def _triangle_normal(points: tuple[Vec3, ...], triangle: tuple[int, int, int]) -> Vec3:
    a, b, c = (points[index] for index in triangle)
    return _normalize(_cross(_subtract(b, a), _subtract(c, a)))


def _normalize(vector: Sequence[float]) -> Vec3:
    magnitude = math.sqrt(sum(float(component) ** 2 for component in vector))
    if magnitude < 1.0e-14:
        raise ValueError("cannot normalize a zero-length vector")
    return tuple(float(component) / magnitude for component in vector)  # type: ignore[return-value]


def _subtract(left: Sequence[float], right: Sequence[float]) -> Vec3:
    return tuple(float(left[index]) - float(right[index]) for index in range(3))  # type: ignore[return-value]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(left[index]) * float(right[index]) for index in range(3))


def _cross(left: Sequence[float], right: Sequence[float]) -> Vec3:
    return (
        float(left[1]) * float(right[2]) - float(left[2]) * float(right[1]),
        float(left[2]) * float(right[0]) - float(left[0]) * float(right[2]),
        float(left[0]) * float(right[1]) - float(left[1]) * float(right[0]),
    )
