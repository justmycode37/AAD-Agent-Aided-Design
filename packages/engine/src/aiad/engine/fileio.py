"""Native 3D file I/O, loaded inside a kernel worker on demand.

Export validation checks the written file, not design intent or revision
verification. The platform owns the policy for exporting unverified revisions.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import struct
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from .types import Entity, FeatureInfo, ModelContext

_MM_PER_UNIT = {"mm": 1.0, "in": 25.4}
_STEP_SCHEMAS = {
    "AP214": ("AP214IS", "AUTOMOTIVE_DESIGN"),
    "AP242": ("AP242DIS", "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF"),
}
_STEP_LOCK = RLock()  # OCCT's Interface_Static settings are process-global.


class FileIOError(RuntimeError):
    """An import or export failure with a platform-compatible code and phase."""

    def __init__(self, message: str, *, code: str, phase: str) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase


@dataclass(frozen=True)
class ImportedModel:
    """An in-process context and explicit provenance for one imported shape."""

    context: ModelContext
    source_format: str
    source_units: str | None
    source_units_assumed: bool
    role: str
    history_notice: str


def export(ctx: ModelContext, fmt: str, out: Path, **options: Any) -> Path:
    """Write, inspect, and atomically promote a 3D export under ``ctx.job_dir``.

    See ``packages/engine/fileio.md`` for supported options and exactly what is
    checked. A relative ``out`` is interpreted relative to the job directory.
    """
    if not isinstance(fmt, str) or fmt.lower() not in {"step", "brep", "stl", "3mf", "glb"}:
        raise _error("EXPORT_FAILED", "input", f"Unsupported export format: {fmt!r}.")
    fmt = fmt.lower()
    units = _units(ctx.units, "EXPORT_FAILED")
    settings = _export_options(options)
    target = _export_target(ctx.job_dir, out)
    if ctx.shape is None:
        raise _error("EXPORT_FAILED", "input", "Export needs a 3D shape.")

    # Several native writers tessellate, change locations, or attach metadata.
    # Keep those effects, including effects of a failed writer, off the context.
    try:
        shape = copy.deepcopy(ctx.shape)
    except Exception as exc:
        raise _error("EXPORT_FAILED", "kernel", f"Could not copy the shape: {exc}") from exc

    temporary: Path | None = None
    try:
        # The suffix matters to build123d.Mesher.write, which selects its writer
        # from the extension. mkstemp creates a unique sibling on the same FS.
        fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=f".{fmt}", dir=target.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            _write_export(shape, fmt, temporary, units, settings)
        except FileIOError:
            raise
        except Exception as exc:
            raise _error("EXPORT_FAILED", "kernel", f"{fmt.upper()} writer failed: {type(exc).__name__}: {exc}") from exc
        try:
            _validate_export(shape, fmt, temporary, units, settings)
        except FileIOError:
            raise
        except Exception as exc:
            raise _error(
                "EXPORT_FAILED", "validate", f"{fmt.upper()} output validation failed: {type(exc).__name__}: {exc}"
            ) from exc

        # Recheck the destination before replacement. os.replace replaces a
        # directory entry rather than following a final symlink.
        if target.is_symlink():
            raise _error("EXPORT_FAILED", "input", "The export destination is a symbolic link.")
        os.replace(temporary, target)
        temporary = None
        return target
    except FileIOError:
        raise
    except OSError as exc:
        raise _error("EXPORT_FAILED", "runtime", f"The export could not be promoted: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_file(
    path: str | Path,
    job_dir: str | Path,
    *,
    name: str | None = None,
    role: str = "base",
    units: str = "mm",
    source_units: str | None = None,
    project_dir: str | Path | None = None,
) -> ImportedModel:
    """Import STEP, BREP, or STL into a context with one base feature.

    STEP's native reader returns millimetres. BREP and STL carry no unit, so
    their source defaults to assumed millimetres unless ``source_units`` is
    supplied. ``units`` selects the context's actual coordinate unit.
    """
    target_units = _units(units, "IMPORT_FAILED")
    if role not in {"base", "reference"}:
        raise _error("IMPORT_FAILED", "input", f"Unsupported import role: {role!r}.")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise _error("IMPORT_FAILED", "input", "The import feature name must be nonempty.")

    source = Path(path)
    fmt = {".step": "step", ".stp": "step", ".brep": "brep", ".stl": "stl"}.get(source.suffix.lower())
    if fmt is None:
        raise _error("IMPORT_FAILED", "input", f"Unsupported import file extension: {source.suffix!r}.")
    if not source.is_file():
        raise _error("IMPORT_FAILED", "input", f"The import file does not exist: {source}.")
    job = Path(job_dir)
    if not job.is_dir():
        raise _error("IMPORT_FAILED", "input", f"The job directory does not exist: {job}.")

    if fmt == "step":
        if source_units is not None:
            raise _error("IMPORT_FAILED", "input", "STEP units are read from the file; source_units is not accepted.")
        actual_source: str | None = None
        assumed = False
        factor = 1.0 / _MM_PER_UNIT[target_units]
    else:
        actual_source = _units(source_units if source_units is not None else "mm", "IMPORT_FAILED")
        assumed = source_units is None
        factor = _MM_PER_UNIT[actual_source] / _MM_PER_UNIT[target_units]

    flattened = False
    try:
        import build123d as bd

        if fmt == "step":
            shape = _read_step_mm(source)
            _require_shape(shape, require_solid=False)
            if factor != 1.0:
                shape, flattened = _scale_imported_brep(shape, factor)
        elif fmt == "brep":
            shape = bd.import_brep(source)
            _require_shape(shape, require_solid=False)
            if factor != 1.0:
                shape, flattened = _scale_imported_brep(shape, factor)
        else:
            # Shape.scale() does not scale a triangulation-only STL Face in
            # build123d 0.13, so scale the actual Poly_Triangulation nodes.
            shape = _read_stl(source, factor)
    except FileIOError:
        raise
    except Exception as exc:
        raise _error("IMPORT_FAILED", "validate", f"Could not read {fmt.upper()}: {type(exc).__name__}: {exc}") from exc

    feature_name = name or "base"
    feature = FeatureInfo(
        name=feature_name,
        faces=[Entity(kind="face", index=i, feature=feature_name) for i, _ in enumerate(shape.faces())],
        edges=[Entity(kind="edge", index=i, feature=feature_name) for i, _ in enumerate(shape.edges())],
    )
    context = ModelContext(
        project_dir=Path(project_dir) if project_dir is not None else job,
        job_dir=job,
        units=target_units,
        shape=shape,
        features=[feature],
    )
    notice = "Imported as one base feature; the original feature history is unavailable."
    if fmt == "stl":
        notice += " STL is a triangulated reference, not an editable B-rep solid."
    if flattened:
        notice += " Unit conversion flattened the assembly; component names and colors were not retained."
    if assumed:
        notice += " This unitless file was assumed to use mm."
    return ImportedModel(context, fmt, actual_source, assumed, role, notice)


def _error(code: str, phase: str, message: str) -> FileIOError:
    return FileIOError(message, code=code, phase=phase)


def _units(value: str, code: str) -> str:
    if not isinstance(value, str) or value not in _MM_PER_UNIT:
        raise _error(code, "input", f"Units must be 'mm' or 'in', got {value!r}.")
    return value


def _number_option(options: dict[str, Any], key: str, default: float, *, allow_zero: bool = False) -> float:
    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error("EXPORT_FAILED", "input", f"{key} must be a finite number.")
    try:
        value = float(value)
    except OverflowError as exc:
        raise _error("EXPORT_FAILED", "input", f"{key} must be a finite number.") from exc
    if not math.isfinite(value):
        raise _error("EXPORT_FAILED", "input", f"{key} must be a finite number.")
    if value < 0 or (value == 0 and not allow_zero):
        relation = "nonnegative" if allow_zero else "positive"
        raise _error("EXPORT_FAILED", "input", f"{key} must be {relation}.")
    return value


def _export_options(options: dict[str, Any]) -> dict[str, Any]:
    allowed = {"tolerance", "angular_tolerance", "step_schema", "schema", "step_volume_tolerance"}
    unknown = set(options) - allowed
    if unknown:
        raise _error("EXPORT_FAILED", "input", f"Unknown export options: {', '.join(sorted(unknown))}.")
    if "schema" in options and "step_schema" in options:
        raise _error("EXPORT_FAILED", "input", "Use only one of schema and step_schema.")
    schema = options.get("step_schema", options.get("schema", "AP214"))
    if not isinstance(schema, str) or schema.upper() not in _STEP_SCHEMAS:
        raise _error("EXPORT_FAILED", "input", f"STEP schema must be AP214 or AP242, got {schema!r}.")
    return {
        "schema": schema.upper(),
        "tolerance": _number_option(options, "tolerance", 1e-3),
        "angular_tolerance": _number_option(options, "angular_tolerance", 0.1),
        "step_volume_tolerance": _number_option(options, "step_volume_tolerance", 1e-6, allow_zero=True),
    }


def _export_target(job_dir: Path, out: Path) -> Path:
    try:
        lexical_root = Path(os.path.abspath(job_dir))
        root = lexical_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("job_dir is not a directory")
        requested = Path(out)
        if not requested.is_absolute():
            requested = lexical_root / requested
        requested = Path(os.path.abspath(requested))
        if not requested.is_relative_to(lexical_root) or requested == lexical_root:
            raise ValueError("destination is outside job_dir")
        parent = requested.parent.resolve(strict=True)
        if not parent.is_dir() or not parent.is_relative_to(root):
            raise ValueError("destination resolves outside job_dir")
        if requested.is_symlink():
            raise ValueError("destination is a symbolic link")
        target = parent / requested.name
        if target.is_symlink() or target.is_dir():
            raise ValueError("destination is a symbolic link or directory")
        return target
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error("EXPORT_FAILED", "input", f"Invalid export destination: {exc}.") from exc


@contextmanager
def _step_settings(schema: str | None = None) -> Iterator[None]:
    """Temporarily configure OCCT's process-global STEP settings."""
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Controller
    from OCP.STEPControl import STEPControl_Controller

    with _STEP_LOCK:
        STEPCAFControl_Controller.Init_s()
        STEPControl_Controller.Init_s()
        changes = {"xstep.cascade.unit": "MM"}
        if schema is not None:
            changes.update({"write.step.schema": _STEP_SCHEMAS[schema][0], "write.step.unit": "MM"})
        previous = {key: Interface_Static.CVal_s(key) for key in changes}
        try:
            for key, value in changes.items():
                if not Interface_Static.SetCVal_s(key, value):
                    raise RuntimeError(f"OCCT rejected {key}={value}")
            yield
        finally:
            for key, value in previous.items():
                Interface_Static.SetCVal_s(key, value)


def _write_step(shape: Any, path: Path, units: str, schema: str) -> None:
    import build123d as bd
    from build123d.exporters3d import _create_xde
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Message import Message, Message_Gravity
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs

    # build123d 0.13's export_step replaces the header with AP214 even when
    # write.step.schema is AP242. The native CAF writer honors the setting.
    with _step_settings(schema):
        printers = list(Message.DefaultMessenger_s().Printers())
        levels = [printer.GetTraceLevel() for printer in printers]
        try:
            for printer in printers:
                printer.SetTraceLevel(Message_Gravity.Message_Fail)
            document = _create_xde(shape, bd.Unit.MM if units == "mm" else bd.Unit.IN, auto_naming=True)
            writer = STEPCAFControl_Writer()
            writer.SetColorMode(True)
            writer.SetLayerMode(True)
            writer.SetNameMode(True)
            if not writer.Transfer(document, STEPControl_AsIs):
                raise RuntimeError("OCCT STEP transfer returned false")
            if writer.Write(str(path)) != IFSelect_RetDone:
                raise RuntimeError("OCCT STEP writer did not report success")
        finally:
            for printer, level in zip(printers, levels):
                printer.SetTraceLevel(level)


def _write_export(shape: Any, fmt: str, path: Path, units: str, options: dict[str, Any]) -> None:
    import build123d as bd

    unit = bd.Unit.MM if units == "mm" else bd.Unit.IN
    linear, angular = options["tolerance"], options["angular_tolerance"]
    if fmt == "step":
        _write_step(shape, path, units, options["schema"])
        return
    if fmt == "brep":
        ok = bd.export_brep(shape, path)
    elif fmt == "stl":
        ok = bd.export_stl(shape, path, tolerance=linear, angular_tolerance=angular, ascii_format=False)
    elif fmt == "3mf":
        mesher = bd.Mesher(unit=unit)
        mesher.add_shape(shape, linear_deflection=linear, angular_deflection=angular)
        mesher.write(path)
        ok = True
    else:  # glb
        # build123d 0.13 post-multiplies its Z-up -> Y-up rotation onto the
        # root location, which leaves an existing translation unrotated.
        # Conjugate the copied root location so the writer's result is the
        # global axis rotation * the original location, including translation.
        rotation = bd.Location((0, 0, 0), (1, 0, 0), -90)
        shape.location = rotation * shape.location * rotation.inverse()
        ok = bd.export_gltf(shape, path, unit=unit, binary=True, linear_deflection=linear, angular_deflection=angular)
    if not ok:
        raise RuntimeError(f"{fmt.upper()} writer did not report success")


def _read_step_mm(path: Path) -> Any:
    import build123d as bd
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    with _step_settings():
        reader = STEPControl_Reader()
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            raise ValueError("OCCT could not parse the STEP file")
        return bd.import_step(path)


def _scale_imported_brep(shape: Any, factor: float) -> tuple[Any, bool]:
    from build123d import Compound

    scaled = shape.scale(factor, about=(0, 0, 0))
    if isinstance(scaled, Compound) and scaled.children:
        # Shape.scale transforms a compound's topology but, in build123d
        # 0.13, leaves its assembly children at their old scale. Mesh and XDE
        # writers traverse those children. Use the transformed topology as a
        # flat compound so every later writer sees the actual coordinates.
        return Compound(scaled.wrapped), True
    return scaled, False


def _read_stl(path: Path, factor: float) -> Any:
    from build123d import Face
    from OCP.BRep import BRep_Builder
    from OCP.RWStl import RWStl
    from OCP.TopoDS import TopoDS_Face

    mesh = RWStl.ReadFile_s(str(path))
    if mesh is None or mesh.NbNodes() < 3 or mesh.NbTriangles() < 1:
        raise ValueError("STL contains no readable triangles")
    for index in range(1, mesh.NbNodes() + 1):
        point = mesh.Node(index)
        coords = (point.X() * factor, point.Y() * factor, point.Z() * factor)
        if not all(math.isfinite(value) for value in coords):
            raise ValueError("STL contains a non-finite vertex")
        point.SetCoord(*coords)
        mesh.SetNode(index, point)
    for index in range(1, mesh.NbTriangles() + 1):
        if any(vertex < 1 or vertex > mesh.NbNodes() for vertex in mesh.Triangle(index).Get()):
            raise ValueError("STL contains a triangle with an invalid vertex index")
    face = TopoDS_Face()
    BRep_Builder().MakeFace(face, mesh)
    return Face.cast(face)


def _require_shape(shape: Any, *, require_solid: bool) -> None:
    if shape is None or shape.wrapped is None or shape.wrapped.IsNull():
        raise ValueError("The parsed file contains no shape")
    if not shape.is_valid:
        raise ValueError("The parsed B-rep is not valid according to OCCT")
    if require_solid and not shape.solids():
        raise ValueError("The parsed STEP contains no solid")


def _validate_export(shape: Any, fmt: str, path: Path, units: str, options: dict[str, Any]) -> None:
    import build123d as bd

    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("The writer produced no file or an empty file")
    if fmt == "step":
        # The native writer's header is part of the check; don't silently accept
        # an AP214 file when AP242 was requested (or vice versa).
        with path.open("rb") as stream:
            header = stream.read(65536).decode("latin-1")
        match = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", header, re.IGNORECASE)
        expected_schema = _STEP_SCHEMAS[options["schema"]][1]
        if match is None or not match.group(1).upper().startswith(expected_schema):
            raise ValueError(f"STEP header does not declare {options['schema']}")
        restored = _read_step_mm(path)
        _require_shape(restored, require_solid=True)
        expected_mm3 = float(shape.volume) * _MM_PER_UNIT[units] ** 3
        actual_mm3 = float(restored.volume)
        if not math.isfinite(expected_mm3) or expected_mm3 <= 0 or not math.isfinite(actual_mm3) or actual_mm3 <= 0:
            raise ValueError("STEP volume comparison requires finite positive solid volumes")
        drift = abs(actual_mm3 - expected_mm3) / expected_mm3
        if drift > options["step_volume_tolerance"]:
            raise ValueError(
                f"STEP physical volume drift {drift:.9g} exceeds configured relative tolerance "
                f"{options['step_volume_tolerance']:.9g} (source {expected_mm3:.9g} mm^3, "
                f"read back {actual_mm3:.9g} mm^3)"
            )
    elif fmt == "brep":
        _require_shape(bd.import_brep(path), require_solid=False)
    elif fmt == "stl":
        _validate_binary_stl(path)
    elif fmt == "3mf":
        _validate_3mf(path, units)
    else:
        _validate_glb(path)


def _validate_binary_stl(path: Path) -> None:
    with path.open("rb") as stream:
        header = stream.read(84)
        if len(header) != 84:
            raise ValueError("Binary STL header is incomplete")
        count = struct.unpack_from("<I", header, 80)[0]
        if count == 0 or path.stat().st_size != 84 + 50 * count:
            raise ValueError("Binary STL triangle count does not match its length")
        for _ in range(count):
            triangle = stream.read(50)
            values = struct.unpack("<12fH", triangle)
            if not all(math.isfinite(value) for value in values[:12]):
                raise ValueError("Binary STL contains non-finite triangle data")


def _validate_3mf(path: Path, units: str) -> None:
    import build123d as bd

    unit = bd.Unit.MM if units == "mm" else bd.Unit.IN
    mesher = bd.Mesher(unit=unit)
    expected_unit = mesher.model.GetUnit()
    reader = mesher.model.QueryReader("3mf")
    reader.ReadFromFile(str(path))
    if mesher.model.GetUnit() != expected_unit:
        raise ValueError("3MF unit does not match the model unit")
    meshes = mesher.model.GetMeshObjects()
    mesh_ids: set[int] = set()
    while meshes.MoveNext():
        mesh = meshes.GetCurrentMeshObject()
        if not mesh.IsValid() or mesh.GetVertexCount() < 3 or mesh.GetTriangleCount() < 1:
            raise ValueError("3MF contains an invalid or empty mesh")
        mesh_ids.add(mesh.GetUniqueResourceID())
    items = mesher.model.GetBuildItems()
    if not mesh_ids or items.Count() == 0:
        raise ValueError("3MF contains no mesh build items")
    while items.MoveNext():
        if items.GetCurrent().GetObjectResourceID() not in mesh_ids:
            raise ValueError("3MF build item does not reference a parsed mesh")


def _validate_glb(path: Path) -> None:
    data = path.read_bytes()
    if len(data) < 28:
        raise ValueError("GLB header or chunks are incomplete")
    magic, version, length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise ValueError("GLB magic, version, or total length is invalid")
    offset = 12
    chunks: list[tuple[bytes, int, int]] = []
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("GLB chunk header is incomplete")
        size, kind = struct.unpack_from("<I4s", data, offset)
        start = offset + 8
        if size % 4 or start + size > len(data):
            raise ValueError("GLB chunk length is invalid")
        chunks.append((kind, start, size))
        offset = start + size
    if len(chunks) != 2 or chunks[0][0] != b"JSON" or chunks[1][0] != b"BIN\x00":
        raise ValueError("GLB must contain one JSON chunk followed by one binary chunk")
    _, json_start, json_size = chunks[0]
    _, bin_start, bin_size = chunks[1]
    document = json.loads(data[json_start : json_start + json_size].decode("utf-8"))
    if not isinstance(document, dict) or document.get("asset", {}).get("version") != "2.0":
        raise ValueError("GLB JSON does not declare glTF 2.0")
    buffers = document.get("buffers")
    if not isinstance(buffers, list) or len(buffers) != 1 or "uri" in buffers[0]:
        raise ValueError("GLB does not contain one embedded buffer")
    buffer_size = _nonnegative_int(buffers[0].get("byteLength"))
    if buffer_size == 0 or not 0 <= bin_size - buffer_size <= 3:
        raise ValueError("GLB binary buffer length is invalid")
    views = document.get("bufferViews")
    accessors = document.get("accessors")
    meshes = document.get("meshes")
    if not isinstance(views, list) or not isinstance(accessors, list) or not isinstance(meshes, list) or not meshes:
        raise ValueError("GLB has no mesh buffers or accessors")
    for view in views:
        start = _nonnegative_int(view.get("byteOffset", 0))
        size = _nonnegative_int(view.get("byteLength"))
        if view.get("buffer") != 0 or start + size > buffer_size:
            raise ValueError("GLB buffer view is outside the embedded buffer")

    def accessor(index: Any, kind: str) -> tuple[int, int, int, int]:
        index = _nonnegative_int(index)
        if index >= len(accessors):
            raise ValueError("GLB accessor index is invalid")
        item = accessors[index]
        view_index = _nonnegative_int(item.get("bufferView"))
        if view_index >= len(views) or item.get("type") != kind:
            raise ValueError("GLB accessor type or buffer view is invalid")
        view = views[view_index]
        component = item.get("componentType")
        component_size = {5121: 1, 5123: 2, 5125: 4, 5126: 4}.get(component)
        if component_size is None or (kind == "VEC3" and component != 5126) or (kind == "SCALAR" and component == 5126):
            raise ValueError("GLB accessor component type is invalid")
        width = component_size * (3 if kind == "VEC3" else 1)
        count = _nonnegative_int(item.get("count"))
        within = _nonnegative_int(item.get("byteOffset", 0))
        stride = _nonnegative_int(view.get("byteStride", width))
        if count == 0 or stride < width or within + (count - 1) * stride + width > view["byteLength"]:
            raise ValueError("GLB accessor exceeds its buffer view")
        return bin_start + view.get("byteOffset", 0) + within, stride, count, component

    for mesh in meshes:
        primitives = mesh.get("primitives")
        if not isinstance(primitives, list) or not primitives:
            raise ValueError("GLB mesh has no primitives")
        for primitive in primitives:
            if primitive.get("mode", 4) != 4:
                raise ValueError("GLB mesh is not a triangle primitive")
            start, stride, count, _ = accessor(primitive.get("attributes", {}).get("POSITION"), "VEC3")
            for index in range(count):
                if not all(math.isfinite(v) for v in struct.unpack_from("<3f", data, start + index * stride)):
                    raise ValueError("GLB has non-finite vertex coordinates")
            start, stride, indices, component = accessor(primitive.get("indices"), "SCALAR")
            if indices < 3 or indices % 3:
                raise ValueError("GLB triangle index count is invalid")
            index_format = {5121: "<B", 5123: "<H", 5125: "<I"}[component]
            for index in range(indices):
                if struct.unpack_from(index_format, data, start + index * stride)[0] >= count:
                    raise ValueError("GLB triangle index is outside its position accessor")

    nodes, scenes = document.get("nodes"), document.get("scenes")
    if not isinstance(nodes, list) or not isinstance(scenes, list):
        raise ValueError("GLB has no scene graph")
    scene_index = _nonnegative_int(document.get("scene", 0))
    if scene_index >= len(scenes) or not scenes[scene_index].get("nodes"):
        raise ValueError("GLB default scene has no nodes")
    pending = list(scenes[scene_index]["nodes"])
    seen: set[int] = set()
    mesh_found = False
    while pending:
        index = _nonnegative_int(pending.pop())
        if index >= len(nodes):
            raise ValueError("GLB scene references a nonexistent node")
        if index in seen:
            continue
        seen.add(index)
        node = nodes[index]
        if "mesh" in node:
            if _nonnegative_int(node["mesh"]) >= len(meshes):
                raise ValueError("GLB node references a nonexistent mesh")
            mesh_found = True
        pending.extend(node.get("children", []))
    if not mesh_found:
        raise ValueError("GLB scene does not reference a mesh")


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("A format index or length is not a nonnegative integer")
    return value
