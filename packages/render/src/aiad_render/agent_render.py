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
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .thickness import face_thickness, scene_triangles, visible_from
from .backends import CameraSpec, RenderFace, RenderLine, RenderScene, get_backend

_LOG = logging.getLogger(__name__)
_VIEWS = {"iso", "front", "back", "left", "right", "top", "bottom"}
_SHEET_VIEWS = ("front", "right", "top", "iso")
_STYLES = {"shaded", "lines", "xray"}
_LABELS = {"none", "features", "faces", "edges"}
_HEATMAPS = {None, "thickness", "overhang"}
_BASE_COLORS = {
    "shaded": (194, 201, 208),
    "lines": (248, 249, 250),
    "xray": (157, 191, 211),
}
_HIGHLIGHT = (244, 48, 91)
_SECTION_CAP = (224, 182, 128)
_NO_READING = (154, 160, 166)
_FRAME_MARGIN = 1.28  # Room around the part for the scale bar, triad and pane titles.

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


@dataclass(frozen=True, slots=True)
class _HeatLegend:
    kind: str
    minimum: float
    maximum: float
    units: str
    heuristic: str
    reverse: bool = False  # Low values are the warning color, as with thin walls.


def render(ctx: Any, req: Any, out: Path) -> list[Path]:
    """Render ``ctx.shape`` to deterministic PNGs under ``out``.

    Supported section planes are ``XY``, ``XZ`` and ``YZ`` (offset is along
    their positive normal), plus general strings such as
    ``point=(0, 0, 0);normal=(1, 1, 0)``. General-plane offsets move the point
    along the normalized normal. The section is an OCCT boolean cut, so the
    new section face is capped; diagonal hatch lines are clipped to its mesh.
    """
    request = _read_request(req)
    shape = _field(ctx, "shape", None)
    if shape is None:
        raise ValueError("aiad_render needs a 3D context with ctx.shape")

    output = Path(out)
    output.mkdir(parents=True, exist_ok=True)
    original_faces = list(shape.faces())
    original_edges = list(shape.edges())
    if not original_faces:
        raise ValueError("aiad_render needs a non-empty 3D shape with renderable faces")
    bounds = _shape_bounds(shape)
    diagonal = _bounds_diagonal(bounds)
    units = str(_field(ctx, "units", "model units") or "model units")

    section = _parse_section(request["section"], units) if request["section"] else None
    section_history: dict[str, Any] = {}
    render_shape = _section_shape(shape, section, diagonal, history=section_history) if section else shape
    shape_faces = list(render_shape.faces())
    shape_edges = list(render_shape.edges())
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
    )
    if section and not shape_faces:
        scene.section_label = section.label + " | EMPTY: no geometry on retained side"

    heat_legend: _HeatLegend | None = None
    if request["heatmap"] == "thickness":
        started = time.perf_counter()
        if section and scene.faces:
            original_scene = _make_scene(
                shape, original_faces, original_edges,
                list(range(len(original_faces))), list(range(len(original_edges))),
                feature_face_map, feature_edge_map, set(), set(), set(),
                bounds=bounds, units=units, section=None,
            )
            original_values = face_thickness(original_scene)
            thickness_values = {
                face.index: original_values[face.source_index]
                for face in scene.faces if face.source_index in original_values
            }
        else:
            thickness_values = face_thickness(scene)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _LOG.info("thickness ray-cast heatmap measured in %.2f ms", elapsed_ms)
        measured = list(thickness_values.values())
        minimum, maximum = _expanded_range(
            min(measured) if measured else 0.0,
            max(measured) if measured else 1.0,
        )
        heat_legend = _HeatLegend(
            "WALL THICKNESS",
            minimum,
            maximum,
            scene.units,
            "heuristic: inward rays, thin side per face; grey = no reading",
            reverse=True,
        )
        _apply_thickness_colors(scene, thickness_values, heat_legend)
    elif request["heatmap"] == "overhang":
        minimum, maximum = 0.0, 180.0
        heat_legend = _HeatLegend(
            "OVERHANG ANGLE FROM +Z",
            minimum,
            maximum,
            "deg",
            "heuristic: triangle angle from +Z, build direction assumed +Z",
        )
        _apply_overhang_colors(scene, minimum, maximum)
    else:
        for face in scene.faces:
            if face.highlighted:
                face.color = _HIGHLIGHT
            elif face.section_cap and request["style"] == "shaded":
                face.color = _SECTION_CAP
            else:
                face.color = _BASE_COLORS[request["style"]]

    labels, legend = _make_labels(
        request["labels"],
        _field(ctx, "features", ()),
        scene,
        bounds=scene.bounds,
    )
    backend = get_backend(request["backend"])
    result_paths: list[Path] = []

    for view in request["views"]:
        if view == "sheet":
            image = _render_sheet(
                backend,
                scene,
                request["size"],
                request["style"],
                labels,
                heat_legend,
            )
            path = output / "sheet.png"
        else:
            camera = _camera(view, scene.bounds, request["size"], request["size"])
            image = backend.render(
                scene,
                camera,
                width=request["size"],
                height=request["size"],
                style=request["style"],
            )
            _annotate(
                image,
                camera,
                scene,
                view,
                labels,
                heat_legend,
                border=False,
            )
            path = output / f"{view}.png"
        _save_png(image, path)
        result_paths.append(path)

    if request["labels"] != "none":
        legend_path = output / "labels.json"
        legend_bytes = (json.dumps(legend, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        legend_path.write_bytes(legend_bytes)
        result_paths.append(legend_path)
    return result_paths


def _read_request(req: Any) -> dict[str, Any]:
    raw_views = _field(req, "views", None)
    if raw_views is None:
        views = ["sheet"]
    elif isinstance(raw_views, str):
        views = [raw_views]
    else:
        views = list(raw_views)
    if not views:
        views = ["sheet"]
    normalized_views = []
    for view in views:
        key = str(view).strip().lower()
        if key != "sheet" and key not in _VIEWS:
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
        raise ValueError("heatmap must be thickness, overhang or None")

    size = _field(req, "size", 1024)
    if size is None:
        size = 1024
    if isinstance(size, bool) or not isinstance(size, int) or size < 1 or size > 2048:
        raise ValueError("render size must be an integer between 1 and 2048 pixels")

    highlight = _field(req, "highlight", ()) or ()
    if isinstance(highlight, str):
        highlight = [highlight]
    return {
        "views": normalized_views,
        "style": style,
        "section": _field(req, "section", None),
        "labels": labels,
        "heatmap": heatmap,
        "highlight": [str(selector) for selector in highlight],
        "size": size,
        "backend": str(_field(req, "backend", "vtk") or "vtk"),
    }


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

    try:
        from aiad.engine.select import resolve as engine_resolve
    except ImportError:
        engine_resolve = None

    for selector in selectors:
        if engine_resolve is not None:
            resolved = engine_resolve(ctx, selector)
            if resolved is None:
                continue
            entities = resolved if isinstance(resolved, (list, tuple, set)) else [resolved]
            for entity in entities:
                kind = str(_field(entity, "kind", "")).lower()
                index = _field(entity, "index", None)
                feature = _field(entity, "feature", None)
                if feature:
                    feature_names.add(str(feature))
                if index is None:
                    continue
                _add_highlight_index(
                    kind,
                    int(index),
                    face_count,
                    edge_count,
                    face_indexes,
                    edge_indexes,
                    selector,
                )
            continue

        match = re.fullmatch(r"\s*(face|edge)\s*:\s*(\d+)\s*", selector, re.IGNORECASE)
        if match:
            kind = match.group(1).lower()
            index = int(match.group(2))
            _add_highlight_index(
                kind,
                index,
                face_count,
                edge_count,
                face_indexes,
                edge_indexes,
                selector,
            )
            continue
        feature_match = re.fullmatch(r"\s*feature\s*:\s*(.+?)\s*", selector, re.IGNORECASE)
        if feature_match:
            feature_names.add(feature_match.group(1))
            continue
        raise ValueError(
            f"cannot resolve highlight {selector!r}; without aiad.engine.select.resolve "
            "use face:<index>, edge:<index> or feature:<name>"
        )
    return face_indexes, edge_indexes, feature_names


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
) -> RenderScene:
    diagonal = _bounds_diagonal(bounds)
    tolerance = max(diagonal * 0.0015, 1.0e-6)
    angular_tolerance = 0.14
    scene = RenderScene(bounds=bounds, units=units, section_label=section.label if section else None)
    cap_face_indexes: list[int] = []
    tessellations = _tessellate_faces(shape, faces, tolerance, angular_tolerance)
    for index, face in enumerate(faces):
        if tessellations is None:
            try:
                vertices, triangles = face.tessellate(tolerance, angular_tolerance)
            except TypeError:
                vertices, triangles = face.tessellate(tolerance)
            points = tuple(_vector(point) for point in vertices)
        else:
            points, triangles = tessellations[index]
        triangle_indexes = tuple(
            tuple(int(value) for value in triangle)
            for triangle in triangles
            if len(triangle) == 3 and all(0 <= int(value) < len(points) for value in triangle)
        )
        if not points or not triangle_indexes:
            continue
        try:
            normal = _normalize(_vector(face.normal_at()))
        except Exception:
            normal = _triangle_normal(points, triangle_indexes[0])
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
            )
        )

    for index, edge in enumerate(edges):
        points = _edge_points(edge, diagonal, tolerance)
        if len(points) < 2:
            continue
        source_index = source_edge_indexes[index] if index < len(source_edge_indexes) else None
        feature = feature_edge_map.get(source_index) if source_index is not None else None
        try:
            anchor = _vector(edge.position_at(0.5))
        except Exception:
            anchor = points[len(points) // 2]
        scene.lines.append(
            RenderLine(
                points=points,
                role="edge",
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
            scene.lines.extend(_hatch_lines(scene.faces[face_index], section, diagonal))
    return scene


def _surface_anchor(
    face: Any,
    points: tuple[Vec3, ...],
    triangles: tuple[tuple[int, int, int], ...],
) -> Vec3:
    """A point on the face itself. A curved face's center of mass is often out in empty space."""
    try:
        center = _vector(face.center())
    except Exception:
        return _triangle_center(points, triangles[0])
    centers = (_triangle_center(points, triangle) for triangle in triangles)
    return min(centers, key=lambda candidate: math.dist(candidate, center))


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
) -> list[tuple[tuple[Vec3, ...], tuple[tuple[int, int, int], ...]]] | None:
    """Read OCCT face triangulations without build123d's slow triangle iterator."""
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

        if not BRepTools.Triangulation_s(wrapped_shape, tolerance):
            BRepMesh_IncrementalMesh(
                wrapped_shape,
                tolerance,
                True,
                angular_tolerance,
                True,
            )
    except (ImportError, AttributeError, TypeError, RuntimeError):
        return None

    tessellations = []
    for face in faces:
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face.wrapped, location)
        if triangulation is None:
            return None
        transform = location.Transformation()
        points = tuple(
            (
                transformed.X(),
                transformed.Y(),
                transformed.Z(),
            )
            for transformed in (
                triangulation.Node(node_index).Transformed(transform)
                for node_index in range(1, triangulation.NbNodes() + 1)
            )
        )
        reverse = face.wrapped.Orientation() == TopAbs_Orientation.TopAbs_REVERSED
        triangles = []
        for triangle_index in range(1, triangulation.NbTriangles() + 1):
            first, second, third = triangulation.Triangle(triangle_index).Get()
            if reverse:
                second, third = third, second
            triangles.append((first - 1, second - 1, third - 1))
        tessellations.append((points, tuple(triangles)))
    return tessellations


def _hatch_lines(face: RenderFace, section: _Section, diagonal: float) -> list[RenderLine]:
    normal = section.normal
    reference = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (0.0, 1.0, 0.0)
    axis_u = _normalize(_cross(normal, reference))
    axis_v = _normalize(_cross(normal, axis_u))
    direction_2d = _normalize((1.0, 1.0, 0.0))
    perpendicular = (-direction_2d[1], direction_2d[0])
    projected = [
        (_dot(_subtract(point, section.point), axis_u), _dot(_subtract(point, section.point), axis_v))
        for point in face.points
    ]
    if not projected:
        return []
    values = [u * perpendicular[0] + v * perpendicular[1] for u, v in projected]
    spacing = max(diagonal * 0.035, 1.0e-4)
    first = math.ceil(min(values) / spacing)
    last = math.floor(max(values) / spacing)
    hatch: list[RenderLine] = []
    for step in range(first, last + 1):
        offset = step * spacing
        for triangle in face.triangles:
            polygon = [projected[index] for index in triangle]
            intersections: list[tuple[float, float]] = []
            for edge_index in range(3):
                a = polygon[edge_index]
                b = polygon[(edge_index + 1) % 3]
                value_a = a[0] * perpendicular[0] + a[1] * perpendicular[1] - offset
                value_b = b[0] * perpendicular[0] + b[1] * perpendicular[1] - offset
                if abs(value_a) <= 1.0e-10:
                    intersections.append(a)
                if value_a * value_b < -1.0e-18:
                    fraction = value_a / (value_a - value_b)
                    intersections.append(
                        (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)
                    )
            distinct: list[tuple[float, float]] = []
            for point in intersections:
                if not any(math.dist(point, existing) < 1.0e-8 for existing in distinct):
                    distinct.append(point)
            if len(distinct) < 2:
                continue
            distinct.sort(key=lambda point: point[0] * direction_2d[0] + point[1] * direction_2d[1])
            for start_2d, end_2d in ((distinct[0], distinct[-1]),):
                start = tuple(
                    section.point[i] + axis_u[i] * start_2d[0] + axis_v[i] * start_2d[1]
                    for i in range(3)
                )
                end = tuple(
                    section.point[i] + axis_u[i] * end_2d[0] + axis_v[i] * end_2d[1]
                    for i in range(3)
                )
                hatch.append(RenderLine(points=(start, end), role="hatch"))
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
                if line.role == "edge" and line.feature == name and line.anchor is not None
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
            if line.role != "edge" or line.anchor is None:
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


def _camera(view: str, bounds: tuple[Vec3, Vec3], width: int, height: int) -> CameraSpec:
    directions: dict[str, tuple[Vec3, Vec3]] = {
        "iso": (_normalize((1.0, -1.0, 1.0)), (0.0, 0.0, 1.0)),
        "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
        "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
        "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
    }
    toward_camera, tentative_up = directions[view]
    forward = tuple(-value for value in toward_camera)
    right = _normalize(_cross(forward, tentative_up))
    up = _normalize(_cross(right, forward))
    center = _bounds_center(bounds)
    corners = [
        (x, y, z)
        for x in (bounds[0][0], bounds[1][0])
        for y in (bounds[0][1], bounds[1][1])
        for z in (bounds[0][2], bounds[1][2])
    ]
    half_width = max(abs(_dot(_subtract(corner, center), right)) for corner in corners)
    half_height = max(abs(_dot(_subtract(corner, center), up)) for corner in corners)
    aspect = max(width / max(height, 1), 1.0e-6)
    parallel_scale = max(half_height, half_width / aspect, 1.0e-6) * _FRAME_MARGIN
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


def _render_sheet(
    backend: Any,
    scene: RenderScene,
    size: int,
    style: str,
    labels: list[_Label],
    heat_legend: _HeatLegend | None,
) -> Image.Image:
    if size < 64:
        return backend.render(scene, _camera("iso", scene.bounds, size, size), width=size, height=size, style=style)
    padding = max(8, size // 64)
    gutter = max(8, size // 72)
    pane_width = max(1, (size - 2 * padding - gutter) // 2)
    pane_height = max(1, (size - 2 * padding - gutter) // 2)
    canvas = Image.new("RGB", (size, size), (235, 239, 243))
    draw = ImageDraw.Draw(canvas)
    positions = (
        (padding, padding),
        (padding + pane_width + gutter, padding),
        (padding, padding + pane_height + gutter),
        (padding + pane_width + gutter, padding + pane_height + gutter),
    )
    cameras = [
        _camera(view, scene.bounds, pane_width, pane_height)
        for view in _SHEET_VIEWS
    ]
    render_sheet = getattr(backend, "render_sheet", None)
    if callable(render_sheet):
        panes = render_sheet(
            scene,
            cameras,
            canvas_size=size,
            pane_width=pane_width,
            pane_height=pane_height,
            positions=positions,
            style=style,
        )
    else:
        render_many = getattr(backend, "render_many", None)
        if callable(render_many):
            panes = render_many(
                scene,
                cameras,
                width=pane_width,
                height=pane_height,
                style=style,
            )
        else:
            panes = [
                backend.render(
                    scene,
                    camera,
                    width=pane_width,
                    height=pane_height,
                    style=style,
                )
                for camera in cameras
            ]

    for view, (x, y), camera, pane in zip(_SHEET_VIEWS, positions, cameras, panes, strict=True):
        _annotate(pane, camera, scene, view, labels, heat_legend, border=False)
        canvas.paste(pane, (x, y))
        draw.rectangle(
            (x, y, x + pane_width - 1, y + pane_height - 1),
            outline=(185, 192, 199),
            width=1,
        )
    return canvas


def _annotate(
    image: Image.Image,
    camera: CameraSpec,
    scene: RenderScene,
    view: str,
    labels: list[_Label],
    heat_legend: _HeatLegend | None,
    *,
    border: bool,
) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    if min(width, height) < 64:
        return
    font = ImageFont.load_default()
    margin = max(8, min(16, width // 48))
    draw.rectangle((0, 0, width - 1, height - 1), outline=(207, 213, 219), width=1)
    title = view.upper()
    draw.text((margin, margin), title, fill=(37, 43, 49), font=font)
    if scene.section_label:
        section_text = scene.section_label
        while len(section_text) > 3 and draw.textlength(section_text, font=font) > width - 2 * margin:
            section_text = section_text[:-4] + "..."
        draw.text((margin, margin + 14), section_text, fill=(59, 66, 73), font=font)
        if not scene.faces:
            text = "No geometry retained"
            text_width = draw.textlength(text, font=font)
            draw.text(((width - text_width) / 2, height / 2), text, fill=(59, 66, 73), font=font)
    _draw_scale_bar(draw, camera, scene, width, height, margin, font)
    _draw_axis_triad(draw, camera, width, height, margin, font)
    if heat_legend:
        _draw_heat_legend(draw, heat_legend, width, height, margin, font)
        note_y = margin + (28 if scene.section_label else 14)
        note = heat_legend.heuristic
        if draw.textlength(note, font=font) > width - 2 * margin:
            note = "Heuristic; build +Z" if heat_legend.kind == "OVERHANG ANGLE FROM +Z" else "Heuristic; grey = no reading"
        if width >= 200:
            draw.text((margin, note_y), note, fill=(73, 79, 85), font=font)
    if labels:
        visible = _visible_labels(labels, camera, scene)
        _draw_labels(draw, visible, camera, width, height, margin, font)


def _draw_scale_bar(
    draw: ImageDraw.ImageDraw,
    camera: CameraSpec,
    scene: RenderScene,
    width: int,
    height: int,
    margin: int,
    font: ImageFont.ImageFont,
) -> None:
    pixels_per_unit = height / max(2.0 * camera.parallel_scale, 1.0e-12)
    target = min(96.0, max(35.0, width * 0.20))
    raw_length = target / max(pixels_per_unit, 1.0e-12)
    magnitude = 10.0 ** math.floor(math.log10(max(raw_length, 1.0e-12)))
    length = min((1.0, 2.0, 5.0, 10.0), key=lambda base: abs(base * magnitude - raw_length)) * magnitude
    pixels = max(1, int(round(length * pixels_per_unit)))
    x0 = margin
    y = height - margin - 12
    draw.line((x0, y, x0 + pixels, y), fill=(35, 42, 48), width=2)
    draw.line((x0, y - 4, x0, y + 4), fill=(35, 42, 48), width=2)
    draw.line((x0 + pixels, y - 4, x0 + pixels, y + 4), fill=(35, 42, 48), width=2)
    draw.text((x0, y - 14), f"{_fmt(length)} {_unit_for_label(scene.units)}", fill=(37, 43, 49), font=font)


def _draw_axis_triad(
    draw: ImageDraw.ImageDraw,
    camera: CameraSpec,
    width: int,
    height: int,
    margin: int,
    font: ImageFont.ImageFont,
) -> None:
    origin = (width - margin - 34, height - margin - 22)
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
    font: ImageFont.ImageFont,
) -> None:
    bar_width = max(72, min(122, width // 4))
    x0 = (width - bar_width) // 2
    x0 = min(x0, max(margin, width - bar_width - margin))
    y0 = height - margin - 16
    steps = max(bar_width, 2)
    for offset in range(steps):
        fraction = offset / (steps - 1)
        color = _heat_color(1.0 - fraction if legend.reverse else fraction)
        draw.line((x0 + offset, y0, x0 + offset, y0 + 6), fill=color)
    units = legend.units
    if legend.kind == "OVERHANG ANGLE FROM +Z":
        units = "deg"
    low = f"{_fmt(legend.minimum)} {units}"
    high = f"{_fmt(legend.maximum)} {units}"
    title = legend.kind
    if width < 400:
        title = "OVERHANG" if title == "OVERHANG ANGLE FROM +Z" else "THICKNESS"
    title_x = x0 + (bar_width - draw.textlength(title, font=font)) / 2
    draw.text((title_x, y0 - 11), title, fill=(36, 42, 49), font=font)
    draw.text((x0, y0 + 7), low, fill=(37, 43, 49), font=font)
    right_width = draw.textbbox((0, 0), high, font=font)[2]
    draw.text((x0 + bar_width - right_width, y0 + 7), high, fill=(37, 43, 49), font=font)


def _visible_labels(labels: list[_Label], camera: CameraSpec, scene: RenderScene) -> list[_Label]:
    """Face and edge labels are drawn only where their anchor can be seen; all stay in the legend."""
    import numpy as np

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


def _draw_labels(
    draw: ImageDraw.ImageDraw,
    labels: list[_Label],
    camera: CameraSpec,
    width: int,
    height: int,
    margin: int,
    font: ImageFont.ImageFont,
) -> None:
    radius = max(7, min(11, width // 64))
    placed: list[tuple[int, int]] = []
    lower_bound = max(margin + 24, 32)
    upper_bound = height - max(margin + 46, 52)
    for label in labels:
        anchor_x, anchor_y = _project(label.anchor, camera, width, height)
        if anchor_x < -radius or anchor_x > width + radius or anchor_y < -radius or anchor_y > height + radius:
            continue
        candidates = []
        for distance in (radius * 1.8, radius * 2.8, radius * 4.0):
            candidates.extend(
                (
                    (anchor_x + distance, anchor_y - distance),
                    (anchor_x + distance, anchor_y + distance),
                    (anchor_x - distance, anchor_y - distance),
                    (anchor_x - distance, anchor_y + distance),
                    (anchor_x + distance, anchor_y),
                    (anchor_x - distance, anchor_y),
                    (anchor_x, anchor_y - distance),
                    (anchor_x, anchor_y + distance),
                )
            )
        chosen = None
        for candidate_x, candidate_y in candidates:
            x = int(round(min(width - radius - 2, max(radius + 2, candidate_x))))
            y = int(round(min(upper_bound, max(lower_bound, candidate_y))))
            if all(math.hypot(x - px, y - py) >= radius * 2.3 for px, py in placed):
                chosen = (x, y)
                break
        if chosen is None:
            x = int(round(min(width - radius - 2, max(radius + 2, anchor_x + radius * 1.5))))
            y = int(round(min(upper_bound, max(lower_bound, anchor_y))))
            chosen = (x, y)
        placed.append(chosen)
        draw.line((anchor_x, anchor_y, chosen[0], chosen[1]), fill=(49, 57, 65), width=1)
        draw.ellipse(
            (anchor_x - 2, anchor_y - 2, anchor_x + 2, anchor_y + 2),
            fill=(49, 57, 65),
        )
        draw.ellipse(
            (
                chosen[0] - radius,
                chosen[1] - radius,
                chosen[0] + radius,
                chosen[1] + radius,
            ),
            fill=(255, 255, 255),
            outline=(37, 44, 51),
            width=1,
        )
        number = str(label.number)
        box = draw.textbbox((0, 0), number, font=font)
        text_width = box[2] - box[0]
        text_height = box[3] - box[1]
        draw.text(
            (chosen[0] - text_width / 2, chosen[1] - text_height / 2 - box[1]),
            number,
            fill=(24, 29, 34),
            font=font,
        )


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


def _apply_overhang_colors(scene: RenderScene, minimum: float, maximum: float) -> None:
    minimum, maximum = _expanded_range(minimum, maximum)
    for face in scene.faces:
        colors = []
        for triangle in face.triangles:
            if face.highlighted:
                colors.append(_HIGHLIGHT)
                continue
            try:
                normal = _triangle_normal(face.points, triangle)
            except ValueError:
                colors.append(_NO_READING)
                continue
            angle = math.degrees(math.acos(max(-1.0, min(1.0, normal[2]))))
            colors.append(_heat_color((angle - minimum) / (maximum - minimum)))
        face.triangle_colors = tuple(colors)


def _heat_color(value: float) -> tuple[int, int, int]:
    stops = (
        (0.0, (45, 92, 173)),
        (0.34, (43, 166, 171)),
        (0.67, (241, 211, 73)),
        (1.0, (207, 57, 48)),
    )
    value = max(0.0, min(1.0, value))
    for (left, left_color), (right, right_color) in zip(stops, stops[1:]):
        if value <= right:
            fraction = (value - left) / (right - left)
            return tuple(
                int(round(left_color[index] + fraction * (right_color[index] - left_color[index])))
                for index in range(3)
            )
    return stops[-1][1]


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
