"""Build a deterministic three-cad-viewer scene from an engine context."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from aad.engine.types import ModelContext, SceneManifest


PART_COLOR = "#b8c0cc"  # A neutral machined look, rather than ocp-tessellate's default yellow.
_DROP_FROM_SCENE = object()


@dataclass
class _LocalSceneManifest:
    """Contract-identical fallback used when aad-engine is not installed."""

    scene_id: str = ""
    project: str = ""
    rev: int = 0
    kind: str = "3d"
    format: str = "tcv-5"
    encoding: str = "gzip"
    units: str = "mm"
    bbox: list[float] = field(default_factory=list)
    total_bytes: int = 0
    triangles: int | None = None
    parts: list[dict[str, Any]] = field(default_factory=list)
    params: list[dict[str, Any]] = field(default_factory=list)
    checks_summary: dict[str, int] = field(default_factory=dict)
    failed_features: list[str] = field(default_factory=list)
    status: str = "draft"
    compare: _LocalSceneManifest | None = None


def _manifest_class() -> type:
    """Import the shared manifest only when a scene is actually requested."""
    try:
        from aad.engine.types import SceneManifest
    except ModuleNotFoundError as error:
        if error.name not in {"aad", "aad.engine", "aad.engine.types"}:
            raise
        return _LocalSceneManifest
    return SceneManifest


def _name(value: Any) -> str | None:
    for attr in ("name", "label", "part_name"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _safe_name(value: str) -> str:
    # A slash is a path separator in the viewer's pick IDs.
    return value.replace("/", "%2F").replace("\\", "%5C")


def _children(shape: Any) -> list[Any]:
    value = getattr(shape, "children", None)
    if callable(value):
        value = value()
    if value is None:
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _solids(shape: Any) -> list[Any]:
    method = getattr(shape, "solids", None)
    if not callable(method):
        return []
    try:
        return list(method())
    except (TypeError, ValueError):
        return []


def _covers_all_topology(shape: Any, solids: list[Any]) -> bool:
    """Whether a compound is only its solids, so that splitting it cannot lose loose faces or edges."""
    try:
        return len(shape.faces()) == sum(len(solid.faces()) for solid in solids) and len(shape.edges()) <= sum(
            len(solid.edges()) for solid in solids
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _in_world(shape: Any, parent: Any) -> Any:
    """Apply the accumulated location of a shape's ancestors, which its own location does not include."""
    if parent is None or parent.IsIdentity():
        return shape
    from build123d import Location

    return shape.moved(Location(parent))


def _scene_parts(shape: Any, ctx: Any) -> list[tuple[Any, str]]:
    """Flatten Compound children to stable, separately pickable viewer parts in world position."""
    result: list[tuple[Any, str]] = []

    def visit(obj: Any, prefix: tuple[str, ...], ordinal: int, parent: Any) -> None:
        children = _children(obj)
        if children:
            own = _wrapped(obj).Location()
            location = own if parent is None else parent.Multiplied(own)
            for index, child in enumerate(children, start=1):
                segment = _name(child) or f"part-{index}"
                visit(child, prefix + (segment,), index, location)
            return

        solids = _solids(obj)
        complete = _covers_all_topology(obj, solids) if solids else False
        if len(solids) > 1 and complete:
            for index, solid in enumerate(solids, start=1):
                segment = _name(solid) or f"part-{index}"
                visit(solid, prefix + (segment,), index, parent)
            return

        # A one-solid Compound still represents one named part. Passing the
        # Solid avoids making the viewer treat its Compound wrapper as another
        # assembly level. A compound with loose faces or edges stays whole.
        part_shape = solids[0] if len(solids) == 1 and complete and type(obj).__name__ == "Compound" else obj
        if prefix:
            part_name = "__".join(prefix)
        else:
            part_name = (
                _name(ctx)
                or _name(part_shape)
                or _name(obj)
                or (f"part-{ordinal}" if ordinal > 0 else "main")
            )
        result.append((_in_world(part_shape, parent), _safe_name(part_name)))

    visit(shape, (), 0, None)

    # OCP group IDs are name-based. Keep duplicate labels distinct and stable.
    seen: dict[str, int] = {}
    unique: list[tuple[Any, str]] = []
    for part_shape, part_name in result:
        count = seen.get(part_name, 0) + 1
        seen[part_name] = count
        unique.append((part_shape, part_name if count == 1 else f"{part_name}-{count}"))
    return unique


def _wrapped(shape: Any) -> Any:
    return getattr(shape, "wrapped", shape)


def _feature_names(features: Any, attribute: str, entity_count: int) -> dict[int, str | None]:
    names: dict[int, str | None] = {}
    if features is None:
        return names

    for feature in features:
        feature_name = getattr(feature, "name", None)
        if not isinstance(feature_name, str) or not feature_name:
            feature_name = "main"
        entities = getattr(feature, attribute, None) or ()
        try:
            iterator = iter(entities)
        except TypeError:
            continue
        for entity in iterator:
            index = getattr(entity, "index", None)
            if index is None or isinstance(index, bool):
                continue
            try:
                index = operator.index(index)
            except TypeError:
                continue
            if index < 0 or index >= entity_count:
                continue
            if index not in names:
                names[index] = feature_name
            elif names[index] != feature_name:
                # More than one feature claims this topology. Preserve the
                # explicit unknown marker rather than choosing one arbitrarily.
                names[index] = None
    return names


def _index_by_hash(engine_entities: list[Any]) -> dict[int, list[int]]:
    """Entities by kernel hash. A linear IsSame scan cost tens of ms on a 200-face part."""
    buckets: dict[int, list[int]] = {}
    for index, entity in enumerate(engine_entities):
        buckets.setdefault(hash(_wrapped(entity)), []).append(index)
    return buckets


def _same_index(topology: Any, engine_entities: list[Any], buckets: dict[int, list[int]]) -> int | None:
    matches = [index for index in buckets.get(hash(topology), ()) if topology.IsSame(_wrapped(engine_entities[index]))]
    return matches[0] if len(matches) == 1 else None


def _mesh_face_order(shape: Any, kernel: Any, get_faces: Any) -> list[Any]:
    """Match tessellator.tessellate_faces: get_faces order, minus unmeshed faces."""
    result = []
    for face in get_faces(shape):
        location = kernel.TopLoc.TopLoc_Location()
        if kernel.BRep.BRep_Tool.Triangulation_s(face, location) is not None:
            result.append(face)
    return result


def _mesh_edge_order(shape: Any, kernel: Any, get_edges: Any) -> list[Any]:
    """Match OCPAdapter.compute_edges, including its no-polygon skips."""
    result = []
    for edge, face in get_edges(shape, True):
        location = kernel.TopLoc.TopLoc_Location()
        triangulation = kernel.BRep.BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            continue
        polygon = kernel.BRep.BRep_Tool.PolygonOnTriangulation_s(edge, triangulation, location)
        if polygon is not None:
            result.append(edge)
    return result


def _placed(shape: Any, location: Any) -> Any:
    if location is None:
        return shape
    try:
        if location.IsIdentity():
            return shape
    except AttributeError:
        pass
    return shape.Moved(location)  # Composes. Located() would discard the shape's own location.


def _feature_map_for_part(
    source_shape: Any,
    location: Any,
    engine_faces: list[Any],
    engine_edges: list[Any],
    face_features: dict[int, str | None],
    edge_features: dict[int, str | None],
    kernel: Any,
    get_faces: Any,
    get_edges: Any,
    face_count: int,
    edge_count: int,
) -> dict[str, list[str]]:
    face_buckets = _index_by_hash(engine_faces)
    edge_buckets = _index_by_hash(engine_edges)
    faces = [
        face_features.get(index) or "main"
        for face in _mesh_face_order(source_shape, kernel, get_faces)
        if (index := _same_index(_placed(face, location), engine_faces, face_buckets)) is not None
    ]
    edges = [
        edge_features.get(index) or "main"
        for edge in _mesh_edge_order(source_shape, kernel, get_edges)
        if (index := _same_index(_placed(edge, location), engine_edges, edge_buckets)) is not None
    ]

    # A topology mismatch must never shift subsequent names onto the wrong
    # indices. In that case leave this part explicitly unknown.
    if len(faces) != face_count:
        faces = ["main"] * face_count
    if len(edges) != edge_count:
        edges = ["main"] * edge_count
    return {"faces": faces, "edges": edges}


def _tree_leaves(node: dict[str, Any]):
    children = node.get("parts")
    if children:
        for child in children:
            yield from _tree_leaves(child)
    else:
        yield node


def _bbox(shape: Any) -> list[float]:
    method = getattr(shape, "bounding_box", None)
    if not callable(method):
        raise TypeError("ctx.shape must provide build123d Shape.bounding_box()")
    bounds = method()
    return [
        float(bounds.min.X),
        float(bounds.min.Y),
        float(bounds.min.Z),
        float(bounds.max.X),
        float(bounds.max.Y),
        float(bounds.max.Z),
    ]


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_value(item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "value"):
        return _json_value(value.value)
    return str(value)


def _params(ctx: Any) -> tuple[list[dict[str, Any]], bool]:
    raw = getattr(ctx, "params", None)
    if raw is None:
        return [], False
    items = raw.items() if isinstance(raw, Mapping) else ()
    result = []
    for name, value in items:
        if isinstance(value, Mapping) and "value" in value:
            entry: dict[str, Any] = {"name": str(name), "value": _json_value(value["value"])}
            for optional in ("min", "max", "unit"):
                if optional in value and value[optional] is not None:
                    entry[optional] = _json_value(value[optional])
        elif hasattr(value, "value"):
            entry = {"name": str(name), "value": _json_value(value.value)}
            for optional in ("min", "max", "unit"):
                candidate = getattr(value, optional, None)
                if candidate is not None:
                    entry[optional] = _json_value(candidate)
        else:
            entry = {"name": str(name), "value": _json_value(value)}
        result.append(entry)
    return result, True


class _NumpyEncoder(json.JSONEncoder):
    def default(self, value: Any) -> Any:
        try:
            import numpy as np
        except ImportError:
            return super().default(value)
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.ndarray):
            return value.tolist()
        return super().default(value)


def _scene_json_value(value: Any) -> Any:
    """Keep scene data JSON-safe while dropping opaque shape metadata.

    ``ocp-tessellate`` copies shape materials and other attributes into its
    scene tree. Those values can be library-specific objects (for example,
    ``bd_warehouse``'s ``FinishedMaterial``), which are useful to the CAD
    model but have no representation in the viewer scene. Drop an unsupported
    mapping entry as a whole, while leaving the geometry tree intact.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _DROP_FROM_SCENE

    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        if isinstance(value, np.generic):
            return _scene_json_value(value.item())
        if isinstance(value, np.ndarray):
            return _scene_json_value(value.tolist())

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str):
                json_key = key
            elif isinstance(key, (bool, int, float)):
                json_key = str(key)
            else:
                continue
            safe_item = _scene_json_value(item)
            if safe_item is not _DROP_FROM_SCENE:
                result[json_key] = safe_item
        return result

    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            safe_item = _scene_json_value(item)
            if safe_item is _DROP_FROM_SCENE:
                # A list can be geometry or metadata. Let the containing
                # mapping drop it rather than changing a geometry sequence.
                return _DROP_FROM_SCENE
            result.append(safe_item)
        return result

    return _DROP_FROM_SCENE


def _manifest_context(ctx: Any, bbox: list[float], parts: list[dict[str, Any]], total_bytes: int, triangles: int):
    params, has_params = _params(ctx)
    checks_summary = _json_value(getattr(ctx, "checks_summary")) if hasattr(ctx, "checks_summary") else None
    failed_features = _json_value(getattr(ctx, "failed_features")) if hasattr(ctx, "failed_features") else None
    project = getattr(ctx, "project", None)
    if project is None:
        project_dir = getattr(ctx, "project_dir", None)
        project = Path(project_dir).name if project_dir is not None else "dev"
    if not project:
        project = "dev"

    raw_kind = str(getattr(ctx, "kind", "3d")).lower()
    raw_units = str(getattr(ctx, "units", "mm")).lower()
    status = getattr(ctx, "status", "draft")
    if status not in {"draft", "verified"}:
        status = "draft"
    manifest_data: dict[str, Any] = {
        "scene_id": "",
        "project": str(project),
        "rev": int(getattr(ctx, "rev", 0)),
        "kind": "2d" if raw_kind == "2d" else "3d",
        "format": "tcv-5",
        "encoding": "gzip",
        "units": "in" if raw_units in {"in", "inch", "inches"} else "mm",
        "bbox": bbox,
        "total_bytes": total_bytes,
        "triangles": triangles,
        "parts": parts,
        "status": status,
    }
    if has_params:
        manifest_data["params"] = params
    if checks_summary is not None:
        manifest_data["checks_summary"] = checks_summary
    if failed_features is not None:
        manifest_data["failed_features"] = failed_features
    return manifest_data


def _manifest_json_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, cls=_NumpyEncoder, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_single_scene(ctx: Any, out: Path) -> tuple[Any, dict[str, Any]]:
    shape = getattr(ctx, "shape", None)
    if shape is None:
        raise ValueError("make_scene requires a 3D ctx.shape")

    # These imports bring in OCCT only when a render is requested; importing
    # aad_render itself stays independent of the engine and CAD kernel.
    from ocp_tessellate._kernel import kernel_of
    from ocp_tessellate.convert import tessellate_group, to_ocpgroup
    from ocp_tessellate.ocp_utils.topology import get_edges, get_faces
    from ocp_tessellate.tessellator import cache as mesh_cache
    from ocp_tessellate.utils import numpy_to_buffer_json

    # A cached mesh leaves the source topology without triangulations, which loses the feature map and
    # changes the bytes for one revision. Always tessellate what this context holds.
    mesh_cache.clear()

    parts = _scene_parts(shape, ctx)
    if not parts:
        raise ValueError("ctx.shape contains no tessellatable parts")

    names = [part_name for _, part_name in parts]
    group, instances = to_ocpgroup(*[part for part, _ in parts], names=names, colors=[PART_COLOR] * len(parts))

    # ocp-tessellate.compute_quality scales linear deflection by
    # (bbox.x + bbox.y + bbox.z) / 300. The default 0.1 therefore remains
    # relative to part size; for a 60x40x40 mm bracket it is about 0.047 mm.
    # Its 0.2 radian angular cap is the default that keeps curved edges smooth
    # without oversampling the <=200-face parts in the timing check.
    options = getattr(ctx, "scene_options", {})
    if not isinstance(options, Mapping):
        options = {}
    deviation = float(options.get("deviation", 0.1))
    angular_tolerance = float(options.get("angular_tolerance", 0.2))
    meshed, shapes, mapping = tessellate_group(
        group,
        instances,
        {"deviation": deviation, "angular_tolerance": angular_tolerance},
    )

    engine_faces = list(shape.faces()) if callable(getattr(shape, "faces", None)) else []
    engine_edges = list(shape.edges()) if callable(getattr(shape, "edges", None)) else []
    features = getattr(ctx, "features", None)
    face_features = _feature_names(features, "faces", len(engine_faces))
    edge_features = _feature_names(features, "edges", len(engine_edges))
    kernel_of(_wrapped(shape))

    source_by_path = {
        leaf["id"]: leaf
        for leaf in _tree_leaves(mapping)
        if isinstance(leaf.get("shape"), dict) and "obj" in leaf["shape"]
    }
    feature_map: dict[str, dict[str, list[str]]] = {}
    manifest_parts: list[dict[str, Any]] = []
    triangles = 0

    for leaf in _tree_leaves(shapes):
        reference = leaf.get("shape")
        if leaf.get("type") != "shapes" or not isinstance(reference, dict) or "ref" not in reference:
            continue
        path = str(leaf.get("id", ""))
        mesh_index = int(reference["ref"])
        if mesh_index < 0 or mesh_index >= len(meshed):
            continue
        mesh = meshed[mesh_index]
        mesh_faces = len(mesh.get("triangles_per_face", ()))
        mesh_edges = len(mesh.get("segments_per_edge", ()))
        triangles += len(mesh.get("triangles", ())) // 3
        source_leaf = source_by_path.get(path)
        if source_leaf is None:
            feature_map[path] = {"faces": ["main"] * mesh_faces, "edges": ["main"] * mesh_edges}
        else:
            feature_map[path] = _feature_map_for_part(
                source_leaf["shape"]["obj"],
                source_leaf.get("loc"),
                engine_faces,
                engine_edges,
                face_features,
                edge_features,
                kernel_of(source_leaf["shape"]["obj"]),
                get_faces,
                get_edges,
                mesh_faces,
                mesh_edges,
            )

        part = {"path": path, "name": str(leaf.get("name", path.rsplit("/", 1)[-1]))}
        color = leaf.get("color")
        if isinstance(color, str) and color:
            part["color"] = color
        feature = leaf.get("feature")
        if isinstance(feature, str) and feature:
            part["feature"] = feature
        manifest_parts.append(part)

    if not manifest_parts:
        raise ValueError("make_scene needs a shape with at least one face; wires and edges alone have no mesh to show")

    shapes["feature_map"] = feature_map
    # TCV 5.1.3 directly decodes this instance/reference form in Viewer.render.
    # ocp_tessellate emits Float32 geometry; no extra decimal rounding is used.
    binary_tree = numpy_to_buffer_json({"instances": meshed, "shapes": shapes})
    safe_tree = _scene_json_value(binary_tree)
    if not isinstance(safe_tree, dict):
        raise TypeError("scene tree is not JSON-compatible")
    payload = json.dumps(
        safe_tree,
        cls=_NumpyEncoder,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    compressed = gzip.compress(payload, compresslevel=6, mtime=0)

    out.mkdir(parents=True, exist_ok=True)
    (out / "scene.bin.gz").write_bytes(compressed)
    scene_id = hashlib.sha256(compressed).hexdigest()
    manifest_data = _manifest_context(
        ctx,
        _bbox(shape),
        manifest_parts,
        len(compressed),
        triangles,
    )
    manifest_data["scene_id"] = scene_id

    manifest_cls = _manifest_class()
    constructor_values = {
        key: value
        for key, value in manifest_data.items()
        if key not in {"scene_id", "project", "rev", "kind", "format", "encoding", "units", "bbox", "total_bytes", "triangles", "parts", "status"}
    }
    manifest = manifest_cls(
        scene_id=scene_id,
        project=manifest_data["project"],
        rev=manifest_data["rev"],
        kind=manifest_data["kind"],
        format=manifest_data["format"],
        encoding=manifest_data["encoding"],
        units=manifest_data["units"],
        bbox=manifest_data["bbox"],
        total_bytes=manifest_data["total_bytes"],
        triangles=manifest_data["triangles"],
        parts=manifest_data["parts"],
        status=manifest_data["status"],
        **constructor_values,
    )
    (out / "scene.json").write_bytes(_manifest_json_bytes(manifest_data))
    return manifest, manifest_data


def make_scene(ctx: ModelContext, out: Path, compare: ModelContext | None = None) -> SceneManifest:
    """Write ``scene.bin.gz`` and ``scene.json`` and return the scene manifest."""
    output_dir = Path(out)
    manifest, manifest_data = _write_single_scene(ctx, output_dir)
    if compare is not None:
        compare_manifest, compare_data = _write_single_scene(compare, output_dir / "compare")
        manifest.compare = compare_manifest
        manifest_data["compare"] = compare_data
        (output_dir / "scene.json").write_bytes(_manifest_json_bytes(manifest_data))
    return manifest
