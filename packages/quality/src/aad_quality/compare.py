"""Rigid reference-mesh comparison used by spec checks and the CLI.

The gate definition is frozen in the project contract: F@0.1% of the reference
bbox diagonal, exact point-to-triangle distances, area-weighted samples, and a
proper rigid transform only. Heavy numerical and VTK imports are lazy so a
missing optional comparison dependency can never turn into a passing check.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import io
import math
import os
from pathlib import Path
import struct
import time
from typing import Any


MAX_REFERENCE_BYTES = 256 * 1024 * 1024
MAX_VERTICES = 6_000_000
MAX_TRIANGLES = 12_000_000
MAX_HEADER_BYTES = 64 * 1024
MAX_ASCII_FACES = 1_000_000
SAMPLE_COUNT = 100_000
REGISTRATION_SAMPLE_COUNT = 200_000
REGISTRATION_SUBSAMPLE_COUNT = 15_000
DEFAULT_TIME_BUDGET_S = 45.0
IOU_RESOLUTION = 256
MAX_IOU_VOXELS = 64_000_000
REGISTRATION_WORKERS = 4
TAUS = (0.0005, 0.001, 0.0025, 0.005, 0.01, 0.02)
GATE_TAU = 0.001
GATE_MIN = 0.99
_ALLOWED_EXTENSIONS = {".step", ".stp", ".stl", ".ply", ".obj"}


class ComparisonError(ValueError):
    """An invalid, unreadable, or unsupported comparison input."""


class ComparisonUnavailable(ComparisonError):
    """A comparison dependency or required geometry operation is unavailable."""


class ComparisonTimeout(ComparisonUnavailable):
    """The comparison exceeded its explicit time budget."""


class RegistrationError(ComparisonUnavailable):
    """A stable proper rigid registration could not be produced."""


class _OpenSurfaceError(ComparisonError):
    """A surface did not establish the closed topology needed for volume IoU."""


@dataclass(frozen=True)
class TriangleMesh:
    """Triangle surface in millimetres, optionally grouped by B-rep solid."""

    vertices: Any
    faces: Any
    solid_ids: Any | None = None
    merge_for_closed: bool = False


class _Deadline:
    def __init__(self, budget_s: float):
        if isinstance(budget_s, bool) or not isinstance(budget_s, (int, float)):
            raise ComparisonError("time budget must be a finite positive number of seconds")
        try:
            budget = float(budget_s)
        except OverflowError as exc:
            raise ComparisonError("time budget must be a finite positive number of seconds") from exc
        if not math.isfinite(budget) or budget <= 0 or budget > 120.0:
            raise ComparisonError("time budget must be greater than 0 and at most 120 seconds")
        self.started = time.perf_counter()
        self.ends = self.started + budget
        self.budget = budget

    def check(self, stage: str) -> None:
        if time.perf_counter() > self.ends:
            elapsed = time.perf_counter() - self.started
            raise ComparisonTimeout(
                f"comparison exceeded its {self.budget:g} second budget during {stage} "
                f"({elapsed:.1f} seconds elapsed)"
            )

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started


def _numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise ComparisonUnavailable(f"NumPy is not available: {exc}") from exc
    return np


def _scipy_tree() -> Any:
    try:
        from scipy.spatial import cKDTree
    except ImportError as exc:
        raise ComparisonUnavailable(f"SciPy is not available for rigid registration: {exc}") from exc
    return cKDTree


def _vtk_modules() -> dict[str, Any]:
    """Import only VTK's data-model modules; importing top-level ``vtk`` is broad."""
    try:
        from vtkmodules.vtkCommonCore import VTK_INT, reference, vtkIdList, vtkPoints
        from vtkmodules.vtkCommonDataModel import (
            vtkCellArray,
            vtkGenericCell,
            vtkPolyData,
            vtkStaticCellLocator,
        )
        from vtkmodules.util import numpy_support
    except ImportError as exc:
        raise ComparisonUnavailable(f"VTK's exact cell locator is not available: {exc}") from exc
    return {
        "VTK_INT": VTK_INT,
        "reference": reference,
        "vtkIdList": vtkIdList,
        "vtkPoints": vtkPoints,
        "vtkCellArray": vtkCellArray,
        "vtkGenericCell": vtkGenericCell,
        "vtkPolyData": vtkPolyData,
        "vtkStaticCellLocator": vtkStaticCellLocator,
        "numpy_support": numpy_support,
    }


def resolve_project_reference(project_dir: str | os.PathLike[str] | None, relative_path: str) -> Path:
    """Resolve a project asset and refuse absolute, traversal, and symlink paths."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ComparisonError("reference path must be a non-empty project-relative path")
    raw = Path(relative_path)
    if raw.is_absolute():
        raise ComparisonError("reference path must be project-relative")
    if ".." in raw.parts:
        raise ComparisonError("reference path may not contain a parent traversal component")
    if project_dir is None:
        raise ComparisonError("project directory is unavailable")
    try:
        root = Path(project_dir).expanduser().resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ComparisonError(f"project directory is unavailable: {exc}") from exc
    if not root.is_dir():
        raise ComparisonError("project directory is not a directory")

    candidate = root
    for component in raw.parts:
        if component in {"", "."}:
            continue
        candidate = candidate / component
        try:
            if candidate.is_symlink():
                raise ComparisonError("reference path may not contain a symbolic link")
        except OSError as exc:
            raise ComparisonError(f"reference path could not be inspected: {exc}") from exc

    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ComparisonError("reference path resolves outside the project or does not exist") from exc
    _validate_reference_file(resolved)
    return resolved


def _validate_reference_file(path: str | os.PathLike[str]) -> Path:
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
        stat = resolved.stat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ComparisonError(f"reference file is unavailable: {exc}") from exc
    if not resolved.is_file():
        raise ComparisonError("reference is not a regular file")
    if resolved.suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise ComparisonError("reference must be a STEP, STL, PLY, or OBJ file")
    if stat.st_size <= 0:
        raise ComparisonError("reference file is empty")
    if stat.st_size > MAX_REFERENCE_BYTES:
        raise ComparisonError(
            f"reference exceeds the {MAX_REFERENCE_BYTES // (1024 * 1024)} MiB input limit"
        )
    return resolved


def _validate_mesh(mesh: TriangleMesh, *, label: str) -> TriangleMesh:
    np = _numpy()
    try:
        vertices = np.asarray(mesh.vertices, dtype=np.float64)
        raw_faces = np.asarray(mesh.faces)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ComparisonError(f"{label} mesh arrays are invalid: {exc}") from exc
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 3:
        raise ComparisonError(f"{label} mesh needs at least three 3D vertices")
    if raw_faces.ndim != 2 or raw_faces.shape[1] != 3 or len(raw_faces) == 0:
        raise ComparisonError(f"{label} mesh needs at least one triangle")
    if len(vertices) > MAX_VERTICES or len(raw_faces) > MAX_TRIANGLES:
        raise ComparisonError("mesh exceeds the vertex or triangle count limit")
    if not np.isfinite(vertices).all():
        raise ComparisonError(f"{label} mesh contains non-finite vertex coordinates")
    if float(np.max(np.abs(vertices))) > 1e9:
        raise ComparisonError(f"{label} mesh coordinates exceed the supported numeric range")
    if not np.issubdtype(raw_faces.dtype, np.integer):
        raise ComparisonError(f"{label} mesh face indices must be integers")
    if int(raw_faces.min()) < 0 or int(raw_faces.max()) >= len(vertices):
        raise ComparisonError(f"{label} mesh contains an out-of-range triangle index")
    faces = np.asarray(raw_faces, dtype=np.int32)
    solid_ids = mesh.solid_ids
    if solid_ids is not None:
        solid_ids = np.asarray(solid_ids, dtype=np.int32)
        if solid_ids.ndim != 1 or len(solid_ids) != len(faces) or int(solid_ids.min()) < 0:
            raise ComparisonError(f"{label} mesh has invalid solid grouping")
    return TriangleMesh(
        vertices=np.ascontiguousarray(vertices),
        faces=np.ascontiguousarray(faces),
        solid_ids=None if solid_ids is None else np.ascontiguousarray(solid_ids),
        merge_for_closed=mesh.merge_for_closed,
    )


def _scale_mesh(mesh: TriangleMesh, factor: float) -> TriangleMesh:
    np = _numpy()
    if not math.isfinite(factor) or factor <= 0:
        raise ComparisonError("mesh unit conversion is invalid")
    return TriangleMesh(
        vertices=np.ascontiguousarray(mesh.vertices * factor),
        faces=mesh.faces,
        solid_ids=mesh.solid_ids,
        merge_for_closed=mesh.merge_for_closed,
    )


def _mm_per_unit(units: str) -> float:
    if units == "mm":
        return 1.0
    if units == "in":
        return 25.4
    raise ComparisonError("mesh units must be 'mm' or 'in'")


def mesh_from_shape(shape: Any, *, units: str = "mm", deadline: _Deadline | None = None) -> TriangleMesh:
    """Tessellate a native build123d shape with the prototype's fixed settings."""
    np = _numpy()
    if shape is None:
        raise ComparisonError("model shape is missing")
    try:
        from build123d import Shape
        from OCP.BRep import BRep_Tool
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.TopAbs import TopAbs_REVERSED
        from OCP.TopLoc import TopLoc_Location

        raw = getattr(shape, "wrapped", shape)
        clean_shape = Shape.cast(raw)
        solids = list(clean_shape.solids())
        items = solids if solids else [clean_shape]
        vertices: list[Any] = []
        faces: list[Any] = []
        solid_ids: list[Any] = []
        base = 0
        triangle_total = 0
        for solid_index, item in enumerate(items):
            if deadline is not None:
                deadline.check("model tessellation")
            BRepMesh_IncrementalMesh(item.wrapped, 0.05, False, 0.15, True)
            for face_index, face_wrapper in enumerate(item.faces()):
                if face_index % 256 == 0 and deadline is not None:
                    deadline.check("model tessellation")
                face = face_wrapper.wrapped
                location = TopLoc_Location()
                triangulation = BRep_Tool.Triangulation_s(face, location)
                if triangulation is None:
                    continue
                transform = location.Transformation()
                node_count = triangulation.NbNodes()
                triangle_count = triangulation.NbTriangles()
                if node_count <= 0 or triangle_count <= 0:
                    continue
                if base + node_count > MAX_VERTICES or triangle_total + triangle_count > MAX_TRIANGLES:
                    raise ComparisonError("tessellated model exceeds the vertex or triangle count limit")
                points = np.empty((node_count, 3), dtype=np.float64)
                for node_index in range(1, node_count + 1):
                    point = triangulation.Node(node_index).Transformed(transform)
                    points[node_index - 1] = (point.X(), point.Y(), point.Z())
                triangles = np.empty((triangle_count, 3), dtype=np.int32)
                for triangle_index in range(1, triangle_count + 1):
                    first, second, third = triangulation.Triangle(triangle_index).Get()
                    triangles[triangle_index - 1] = (first - 1 + base, second - 1 + base, third - 1 + base)
                if face.Orientation() == TopAbs_REVERSED:
                    triangles = triangles[:, ::-1].copy()
                vertices.append(points)
                faces.append(triangles)
                solid_ids.append(np.full(triangle_count, solid_index, dtype=np.int32))
                base += node_count
                triangle_total += triangle_count
        if not faces:
            raise ComparisonError("model shape contains no tessellated triangles")
        mesh = TriangleMesh(
            vertices=np.vstack(vertices),
            faces=np.vstack(faces),
            solid_ids=np.concatenate(solid_ids),
            merge_for_closed=True,
        )
    except ComparisonError:
        raise
    except ImportError as exc:
        raise ComparisonUnavailable(f"build123d/OCCT tessellation is unavailable: {exc}") from exc
    except Exception as exc:
        raise ComparisonError(f"could not tessellate model shape: {type(exc).__name__}: {exc}") from exc
    if deadline is not None:
        deadline.check("model tessellation")
    return _scale_mesh(_validate_mesh(mesh, label="model"), _mm_per_unit(units))


def _read_reference(path: Path, *, units: str, deadline: _Deadline) -> TriangleMesh:
    path = _validate_reference_file(path)
    extension = path.suffix.lower()
    if extension in {".step", ".stp"}:
        try:
            from build123d import import_step
        except ImportError as exc:
            raise ComparisonUnavailable(f"build123d/OCCT STEP import is unavailable: {exc}") from exc
        deadline.check("STEP reference import")
        try:
            shape = import_step(str(path))
        except Exception as exc:
            raise ComparisonError(f"STEP reference could not be read: {type(exc).__name__}: {exc}") from exc
        deadline.check("STEP reference import")
        # build123d's STEP importer normalizes STEP length units to millimetres.
        mesh = mesh_from_shape(shape, units="mm", deadline=deadline)
    elif extension == ".ply":
        mesh = _read_ply(path, deadline)
        mesh = _scale_mesh(mesh, _mm_per_unit(units))
    elif extension == ".stl":
        mesh = _read_stl(path, deadline)
        mesh = _scale_mesh(mesh, _mm_per_unit(units))
    elif extension == ".obj":
        mesh = _read_obj(path, deadline)
        mesh = _scale_mesh(mesh, _mm_per_unit(units))
    else:  # Extension validation and dispatch must stay in sync.
        raise ComparisonError("reference must be a STEP, STL, PLY, or OBJ file")
    return _validate_mesh(mesh, label="reference")


_PLY_TYPES = {
    "char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}


def _parse_ply_header(data: bytes) -> tuple[dict[str, Any], int]:
    end = data.find(b"end_header")
    if end < 0 or end > MAX_HEADER_BYTES:
        raise ComparisonError("PLY header is missing or exceeds the 64 KiB header limit")
    line_end = data.find(b"\n", end)
    if line_end < 0:
        raise ComparisonError("PLY header is not newline terminated")
    header = data[:line_end + 1]
    try:
        lines = header.decode("ascii").replace("\r", "").splitlines()
    except UnicodeDecodeError as exc:
        raise ComparisonError("PLY header must contain ASCII text") from exc
    if not lines or lines[0].strip() != "ply":
        raise ComparisonError("file does not have a PLY header")
    format_name: str | None = None
    elements: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines[1:]:
        tokens = line.split()
        if not tokens or tokens[0] in {"comment", "obj_info"}:
            continue
        if tokens[0] == "format":
            if len(tokens) != 3 or tokens[2] != "1.0":
                raise ComparisonError("only PLY format version 1.0 is supported")
            format_name = tokens[1]
        elif tokens[0] == "element":
            if len(tokens) != 3:
                raise ComparisonError("PLY element declaration is malformed")
            try:
                count = int(tokens[2], 10)
            except ValueError as exc:
                raise ComparisonError("PLY element count is malformed") from exc
            if count < 0 or count > max(MAX_VERTICES, MAX_TRIANGLES):
                raise ComparisonError("PLY element count exceeds the input limit")
            current = {"name": tokens[1], "count": count, "properties": []}
            elements.append(current)
        elif tokens[0] == "property":
            if current is None:
                raise ComparisonError("PLY property appears before an element")
            if len(tokens) == 3:
                if tokens[1] not in _PLY_TYPES:
                    raise ComparisonError(f"unsupported PLY scalar type {tokens[1]!r}")
                current["properties"].append({"name": tokens[2], "scalar": tokens[1]})
            elif len(tokens) == 5 and tokens[1] == "list":
                if tokens[2] not in _PLY_TYPES or tokens[3] not in _PLY_TYPES:
                    raise ComparisonError("unsupported PLY list type")
                current["properties"].append(
                    {"name": tokens[4], "count_type": tokens[2], "item_type": tokens[3]}
                )
            else:
                raise ComparisonError("PLY property declaration is malformed")
        elif tokens[0] != "end_header":
            raise ComparisonError(f"unsupported PLY header directive {tokens[0]!r}")
    if format_name not in {"ascii", "binary_little_endian", "binary_big_endian"}:
        raise ComparisonError("PLY format must be ASCII or binary little/big endian")
    by_name = {element["name"]: element for element in elements}
    if "vertex" not in by_name or "face" not in by_name or len(by_name) != len(elements):
        raise ComparisonError("PLY must have one vertex element and one face element")
    if any(element["name"] not in {"vertex", "face"} for element in elements):
        raise ComparisonError("PLY elements other than vertex and face are unsupported")
    if [element["name"] for element in elements] != ["vertex", "face"]:
        raise ComparisonError("supported PLY files must declare vertices before faces")
    vertex = by_name["vertex"]
    face = by_name["face"]
    if not all(any(prop.get("name") == axis and "scalar" in prop for prop in vertex["properties"]) for axis in "xyz"):
        raise ComparisonError("PLY vertices must have scalar x, y, and z properties")
    if any("scalar" not in prop for prop in vertex["properties"]):
        raise ComparisonError("PLY vertex list properties are unsupported")
    if not any(prop.get("name") in {"vertex_indices", "vertex_index"} and "item_type" in prop for prop in face["properties"]):
        raise ComparisonError("PLY faces need a vertex_indices list property")
    return {"format": format_name, "elements": elements}, line_end + 1


def _read_ply(path: Path, deadline: _Deadline) -> TriangleMesh:
    np = _numpy()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ComparisonError(f"PLY reference could not be read: {exc}") from exc
    header, offset = _parse_ply_header(data)
    elements = header["elements"]
    if header["format"] == "ascii":
        mesh = _read_ply_ascii(data[offset:], elements, deadline)
    else:
        endian = "<" if header["format"] == "binary_little_endian" else ">"
        mesh = _read_ply_binary(data, offset, elements, endian, deadline)
    del data
    return _validate_mesh(mesh, label="PLY reference")


def _read_ply_ascii(payload: bytes, elements: list[dict[str, Any]], deadline: _Deadline) -> TriangleMesh:
    np = _numpy()
    by_name = {element["name"]: element for element in elements}
    vertex_element = by_name["vertex"]
    face_element = by_name["face"]
    vertex_properties = vertex_element["properties"]
    face_properties = face_element["properties"]
    vertex_count = vertex_element["count"]
    face_count = face_element["count"]
    if vertex_count < 3 or vertex_count > MAX_VERTICES or face_count <= 0 or face_count > MAX_TRIANGLES:
        raise ComparisonError("PLY vertex or face count is outside the supported range")
    if face_count > MAX_ASCII_FACES:
        raise ComparisonError("ASCII PLY face count exceeds the 1 million face limit")
    vertices = np.empty((vertex_count, 3), dtype=np.float64)
    axes = {axis: next(i for i, prop in enumerate(vertex_properties) if prop["name"] == axis) for axis in "xyz"}
    lines = iter(io.BytesIO(payload))
    for index in range(vertex_count):
        if index % 4096 == 0:
            deadline.check("ASCII PLY vertex parsing")
        line = next(lines, None)
        if line is None or len(line) > 1_000_000:
            raise ComparisonError("PLY vertex data is truncated or has an oversized record")
        tokens = line.split()
        if len(tokens) != len(vertex_properties):
            raise ComparisonError("PLY vertex record does not match its header")
        try:
            vertices[index] = [float(tokens[axes[axis]]) for axis in "xyz"]
        except (ValueError, OverflowError) as exc:
            raise ComparisonError("PLY contains an invalid vertex coordinate") from exc
    index_property = next(prop for prop in face_properties if prop.get("name") in {"vertex_indices", "vertex_index"} and "item_type" in prop)
    triangles = array("i")  # Flat vertex indices: compact, unlike one Python tuple per triangle.
    for face_index in range(face_count):
        if face_index % 4096 == 0:
            deadline.check("ASCII PLY face parsing")
        line = next(lines, None)
        if line is None or len(line) > 1_000_000:
            raise ComparisonError("PLY face data is truncated or has an oversized record")
        tokens = line.split()
        cursor = 0
        polygon: list[int] | None = None
        try:
            for prop in face_properties:
                if "scalar" in prop:
                    cursor += 1
                else:
                    count = int(tokens[cursor], 10)
                    cursor += 1
                    if count < 0 or count > MAX_VERTICES or cursor + count > len(tokens):
                        raise ComparisonError("PLY face list count is invalid")
                    values = [int(value, 10) for value in tokens[cursor:cursor + count]]
                    cursor += count
                    if prop is index_property:
                        polygon = values
        except (ValueError, IndexError) as exc:
            raise ComparisonError("PLY face record is malformed") from exc
        if cursor != len(tokens) or polygon is None or len(polygon) < 3:
            raise ComparisonError("PLY face record does not contain a valid polygon")
        if min(polygon) < 0 or max(polygon) >= vertex_count:
            raise ComparisonError("PLY face references a vertex outside the file")
        for i in range(1, len(polygon) - 1):
            triangles.extend((polygon[0], polygon[i], polygon[i + 1]))
        if len(triangles) > MAX_TRIANGLES * 3:
            raise ComparisonError("triangulated PLY exceeds the triangle limit")
    return TriangleMesh(vertices, np.array(triangles, dtype=np.int32).reshape(-1, 3), merge_for_closed=False)


def _read_ply_binary(
    data: bytes, offset: int, elements: list[dict[str, Any]], endian: str, deadline: _Deadline
) -> TriangleMesh:
    np = _numpy()
    by_name = {element["name"]: element for element in elements}
    vertex_element = by_name["vertex"]
    face_element = by_name["face"]
    vertex_count = vertex_element["count"]
    face_count = face_element["count"]
    if vertex_count < 3 or vertex_count > MAX_VERTICES or face_count <= 0 or face_count > MAX_TRIANGLES:
        raise ComparisonError("PLY vertex or face count is outside the supported range")
    vertex_properties = vertex_element["properties"]
    vertex_dtype_fields = []
    for prop in vertex_properties:
        dtype = np.dtype(endian + _PLY_TYPES[prop["scalar"]])
        vertex_dtype_fields.append((prop["name"], dtype))
    vertex_dtype = np.dtype(vertex_dtype_fields, align=False)
    vertex_bytes = vertex_count * vertex_dtype.itemsize
    if offset + vertex_bytes > len(data):
        raise ComparisonError("binary PLY vertex data is truncated")
    deadline.check("binary PLY vertex parsing")
    records = np.frombuffer(data, dtype=vertex_dtype, count=vertex_count, offset=offset)
    try:
        vertices = np.column_stack([records[axis] for axis in "xyz"]).astype(np.float64, copy=False)
    except (ValueError, TypeError) as exc:
        raise ComparisonError("binary PLY vertex records are invalid") from exc
    offset += vertex_bytes

    face_properties = face_element["properties"]
    index_property = next(prop for prop in face_properties if prop.get("name") in {"vertex_indices", "vertex_index"} and "item_type" in prop)
    list_properties = [prop for prop in face_properties if "item_type" in prop]
    fast_dtype: Any | None = None
    fast_names: dict[str, str] = {}
    if len(list_properties) == 1:
        fields: list[tuple[str, Any, int]] = []
        cursor = 0
        for prop in face_properties:
            if "scalar" in prop:
                dtype = np.dtype(endian + _PLY_TYPES[prop["scalar"]])
                fields.append((prop["name"], dtype, cursor))
                cursor += dtype.itemsize
            else:
                count_dtype = np.dtype(endian + _PLY_TYPES[prop["count_type"]])
                item_dtype = np.dtype(endian + _PLY_TYPES[prop["item_type"]])
                count_name = f"_count_{prop['name']}"
                fields.append((count_name, count_dtype, cursor))
                cursor += count_dtype.itemsize
                if prop is index_property:
                    item_name = f"_indices_{prop['name']}"
                    fields.append((item_name, (item_dtype, (3,)), cursor))
                    fast_names["count"] = count_name
                    fast_names["indices"] = item_name
                else:
                    # This fixed triangle reader only accepts one face list. Fall back below.
                    fields = []
                    break
                cursor += 3 * item_dtype.itemsize
        if fields:
            fast_dtype = np.dtype(
                {
                    "names": [field[0] for field in fields],
                    "formats": [field[1] for field in fields],
                    "offsets": [field[2] for field in fields],
                    "itemsize": cursor,
                },
                align=False,
            )
    if fast_dtype is not None:
        byte_count = face_count * fast_dtype.itemsize
        if offset + byte_count > len(data):
            raise ComparisonError("binary PLY face data is truncated")
        deadline.check("binary PLY face parsing")
        face_records = np.frombuffer(data, dtype=fast_dtype, count=face_count, offset=offset)
        if not np.all(face_records[fast_names["count"]] == 3):
            raise ComparisonError("binary PLY fast face records must each contain one triangle")
        faces = np.asarray(face_records[fast_names["indices"]], dtype=np.int32).reshape(-1, 3).copy()
    else:
        if face_count > MAX_ASCII_FACES:
            raise ComparisonError("variable-list binary PLY face count exceeds the 1 million face limit")
        faces, offset = _read_ply_binary_faces(data, offset, face_properties, face_count, endian, deadline)
    if int(faces.min()) < 0 or int(faces.max()) >= vertex_count:
        raise ComparisonError("PLY face references a vertex outside the file")
    return TriangleMesh(vertices, faces, merge_for_closed=False)


def _read_ply_binary_faces(
    data: bytes, offset: int, properties: list[dict[str, Any]], count: int, endian: str, deadline: _Deadline
) -> tuple[Any, int]:
    np = _numpy()
    triangles = array("i")  # Flat vertex indices: compact, unlike one Python tuple per triangle.
    for face_index in range(count):
        if face_index % 4096 == 0:
            deadline.check("binary PLY face parsing")
        polygon: list[int] | None = None
        try:
            for prop in properties:
                if "scalar" in prop:
                    dtype = np.dtype(endian + _PLY_TYPES[prop["scalar"]])
                    if offset + dtype.itemsize > len(data):
                        raise ComparisonError("binary PLY face data is truncated")
                    offset += dtype.itemsize
                    continue
                count_dtype = np.dtype(endian + _PLY_TYPES[prop["count_type"]])
                item_dtype = np.dtype(endian + _PLY_TYPES[prop["item_type"]])
                if offset + count_dtype.itemsize > len(data):
                    raise ComparisonError("binary PLY face data is truncated")
                item_count = int(np.frombuffer(data, dtype=count_dtype, count=1, offset=offset)[0])
                offset += count_dtype.itemsize
                if item_count < 0 or item_count > MAX_VERTICES or offset + item_count * item_dtype.itemsize > len(data):
                    raise ComparisonError("binary PLY face list count is invalid")
                values = np.frombuffer(data, dtype=item_dtype, count=item_count, offset=offset).astype(np.int64, copy=False)
                offset += item_count * item_dtype.itemsize
                if prop.get("name") in {"vertex_indices", "vertex_index"}:
                    polygon = [int(value) for value in values]
        except (ValueError, OverflowError) as exc:
            raise ComparisonError("binary PLY face record is malformed") from exc
        if polygon is None or len(polygon) < 3:
            raise ComparisonError("binary PLY face has no usable vertex-index list")
        if min(polygon) < 0 or max(polygon) > np.iinfo(np.int32).max:
            raise ComparisonError("binary PLY face index is outside the supported range")
        for i in range(1, len(polygon) - 1):
            triangles.extend((polygon[0], polygon[i], polygon[i + 1]))
        if len(triangles) > MAX_TRIANGLES * 3:
            raise ComparisonError("triangulated PLY exceeds the triangle limit")
    return np.array(triangles, dtype=np.int32).reshape(-1, 3), offset


def _read_stl(path: Path, deadline: _Deadline) -> TriangleMesh:
    np = _numpy()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ComparisonError(f"STL reference could not be read: {exc}") from exc
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if count <= MAX_TRIANGLES and 84 + count * 50 == len(data):
            # Each binary triangle stores three vertices, so check before expanding them.
            if count * 3 > MAX_VERTICES:
                raise ComparisonError("binary STL exceeds the vertex limit")
            deadline.check("binary STL parsing")
            dtype = np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (9,)), ("attribute", "<u2")])
            records = np.frombuffer(data, dtype=dtype, count=count, offset=84)
            vertices = records["vertices"].reshape(-1, 3).astype(np.float64)
            faces = np.arange(count * 3, dtype=np.int32).reshape(-1, 3)
            del records, data
            return TriangleMesh(vertices, faces, merge_for_closed=True)
    return _read_stl_ascii(data, deadline)


def _read_stl_ascii(data: bytes, deadline: _Deadline) -> TriangleMesh:
    np = _numpy()
    vertices_chunks: list[Any] = []
    values: list[tuple[float, float, float]] = []
    count = 0
    try:
        lines = io.BytesIO(data)
        for line_number, raw in enumerate(lines):
            if line_number % 8192 == 0:
                deadline.check("ASCII STL parsing")
            if len(raw) > 1_000_000:
                raise ComparisonError("ASCII STL contains an oversized line")
            tokens = raw.split()
            if tokens and tokens[0].lower() == b"vertex":
                if len(tokens) != 4:
                    raise ComparisonError("ASCII STL vertex record is malformed")
                try:
                    values.append(tuple(float(value) for value in tokens[1:4]))
                except (ValueError, OverflowError) as exc:
                    raise ComparisonError("ASCII STL contains an invalid vertex") from exc
                count += 1
                if count > MAX_VERTICES:
                    raise ComparisonError("ASCII STL exceeds the vertex limit")
                if len(values) >= 300_000:
                    vertices_chunks.append(np.asarray(values, dtype=np.float64))
                    values.clear()
    except UnicodeDecodeError as exc:
        raise ComparisonError("STL is neither valid binary STL nor ASCII STL") from exc
    if values:
        vertices_chunks.append(np.asarray(values, dtype=np.float64))
    if count < 3 or count % 3:
        raise ComparisonError("ASCII STL must contain complete triangles")
    vertices = np.vstack(vertices_chunks)
    faces = np.arange(count, dtype=np.int32).reshape(-1, 3)
    return TriangleMesh(vertices, faces, merge_for_closed=True)


def _read_obj(path: Path, deadline: _Deadline) -> TriangleMesh:
    np = _numpy()
    vertex_chunks: list[Any] = []
    triangle_chunks: list[Any] = []
    vertices_chunk: list[tuple[float, float, float]] = []
    triangles_chunk: list[tuple[int, int, int]] = []
    vertex_count = 0
    triangle_count = 0
    try:
        stream = path.open("r", encoding="utf-8", errors="strict", newline=None)
    except (OSError, UnicodeError) as exc:
        raise ComparisonError(f"OBJ reference could not be opened as UTF-8 text: {exc}") from exc
    with stream:
        for line_number, line in enumerate(stream):
            if line_number % 4096 == 0:
                deadline.check("OBJ parsing")
            if len(line) > 1_000_000:
                raise ComparisonError("OBJ contains an oversized line")
            tokens = line.split("#", 1)[0].split()
            if not tokens:
                continue
            if tokens[0] == "v":
                if len(tokens) not in {4, 5}:
                    raise ComparisonError("OBJ vertex record must contain x, y, z and optional w")
                try:
                    vertices_chunk.append(tuple(float(value) for value in tokens[1:4]))
                except (ValueError, OverflowError) as exc:
                    raise ComparisonError("OBJ contains an invalid vertex") from exc
                vertex_count += 1
                if vertex_count > MAX_VERTICES:
                    raise ComparisonError("OBJ exceeds the vertex limit")
                if len(vertices_chunk) >= 200_000:
                    vertex_chunks.append(np.asarray(vertices_chunk, dtype=np.float64))
                    vertices_chunk.clear()
            elif tokens[0] == "f":
                if len(tokens) < 4:
                    raise ComparisonError("OBJ face must contain at least three vertices")
                polygon: list[int] = []
                try:
                    for token in tokens[1:]:
                        vertex_token = token.split("/", 1)[0]
                        value = int(vertex_token, 10)
                        if value == 0:
                            raise ValueError("OBJ indices are one-based")
                        index = value - 1 if value > 0 else vertex_count + value
                        if index < 0 or index >= vertex_count:
                            raise ValueError("OBJ face index is outside the preceding vertex records")
                        polygon.append(index)
                except ValueError as exc:
                    raise ComparisonError(f"OBJ face is malformed: {exc}") from exc
                for position in range(1, len(polygon) - 1):
                    triangles_chunk.append((polygon[0], polygon[position], polygon[position + 1]))
                    triangle_count += 1
                    if triangle_count > MAX_TRIANGLES:
                        raise ComparisonError("triangulated OBJ exceeds the triangle limit")
                if len(triangles_chunk) >= 300_000:
                    triangle_chunks.append(np.asarray(triangles_chunk, dtype=np.int32))
                    triangles_chunk.clear()
    if vertices_chunk:
        vertex_chunks.append(np.asarray(vertices_chunk, dtype=np.float64))
    if triangles_chunk:
        triangle_chunks.append(np.asarray(triangles_chunk, dtype=np.int32))
    if not vertex_chunks or not triangle_chunks:
        raise ComparisonError("OBJ does not contain a usable vertex and face mesh")
    return TriangleMesh(np.vstack(vertex_chunks), np.vstack(triangle_chunks), merge_for_closed=False)


def _triangle_areas(mesh: TriangleMesh, deadline: _Deadline) -> Any:
    np = _numpy()
    faces = mesh.faces
    vertices = mesh.vertices
    areas = np.empty(len(faces), dtype=np.float64)
    chunk = 200_000
    for start in range(0, len(faces), chunk):
        deadline.check("triangle area calculation")
        stop = min(start + chunk, len(faces))
        triangles = faces[start:stop]
        first = vertices[triangles[:, 0]]
        second = vertices[triangles[:, 1]]
        third = vertices[triangles[:, 2]]
        areas[start:stop] = 0.5 * np.linalg.norm(
            np.cross(second - first, third - first), axis=1
        )
    if not np.isfinite(areas).all() or not float(areas.sum()) > 0:
        raise ComparisonError("mesh has no finite, positive-area triangle surface")
    return areas


def _sample(mesh: TriangleMesh, areas: Any, count: int, rng: Any, deadline: _Deadline) -> Any:
    np = _numpy()
    deadline.check("surface sampling")
    face_indices = rng.choice(len(areas), count, p=areas / areas.sum())
    first, second, third = (mesh.vertices[mesh.faces[face_indices, axis]] for axis in range(3))
    u, w = rng.random(count), rng.random(count)
    reflected = u + w > 1
    u[reflected], w[reflected] = 1 - u[reflected], 1 - w[reflected]
    points = first + u[:, None] * (second - first) + w[:, None] * (third - first)
    if not np.isfinite(points).all():
        raise ComparisonError("area-weighted surface sampling returned non-finite points")
    return points


def _kabsch(source: Any, target: Any) -> tuple[Any, Any]:
    np = _numpy()
    source_center, target_center = source.mean(axis=0), target.mean(axis=0)
    try:
        u, _, vt = np.linalg.svd((source - source_center).T @ (target - target_center))
    except np.linalg.LinAlgError as exc:
        raise RegistrationError(f"rigid registration SVD failed: {exc}") from exc
    sign = 1.0 if np.linalg.det(vt.T @ u.T) >= 0 else -1.0
    rotation = vt.T @ np.diag([1.0, 1.0, sign]) @ u.T
    translation = target_center - rotation @ source_center
    return rotation, translation


def _icp(source: Any, tree: Any, target: Any, rotation: Any, translation: Any, iterations: int, trim: float) -> tuple[Any, Any, float]:
    np = _numpy()
    for _ in range(iterations):
        transformed = source @ rotation.T + translation
        distances, indices = tree.query(transformed, workers=REGISTRATION_WORKERS)
        keep = distances <= np.quantile(distances, trim)
        rotation, translation = _kabsch(source[keep], target[indices[keep]])
    transformed = source @ rotation.T + translation
    distances, _ = tree.query(transformed, workers=REGISTRATION_WORKERS)
    return rotation, translation, float(np.mean(np.sort(distances)[: int(trim * len(distances))]))


def _register(model_points: Any, reference_points: Any, deadline: _Deadline) -> tuple[Any, Any]:
    np = _numpy()
    cKDTree = _scipy_tree()
    model_center, reference_center = model_points.mean(axis=0), reference_points.mean(axis=0)

    def principal_frame(points: Any, center: Any) -> Any:
        try:
            _, vectors = np.linalg.eigh(np.cov((points - center).T))
        except np.linalg.LinAlgError as exc:
            raise RegistrationError(f"rigid registration PCA failed: {exc}") from exc
        vectors = vectors[:, ::-1]
        if np.linalg.det(vectors) < 0:
            vectors[:, 2] *= -1
        return vectors

    model_frame = principal_frame(model_points, model_center)
    reference_frame = principal_frame(reference_points, reference_center)
    flips = [np.diag(item) for item in ([1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1])]
    permutations = [
        np.eye(3),
        np.eye(3)[[1, 2, 0]],
        np.eye(3)[[2, 0, 1]],
        np.eye(3)[[1, 0, 2]] * np.array([[1], [1], [-1]]),
        np.eye(3)[[0, 2, 1]] * np.array([[1], [1], [-1]]),
        np.eye(3)[[2, 1, 0]] * np.array([[1], [1], [-1]]),
    ]
    tree = cKDTree(reference_points)
    rng = np.random.default_rng(1)
    subset = model_points[rng.choice(len(model_points), min(REGISTRATION_SUBSAMPLE_COUNT, len(model_points)), replace=False)]
    congruent_residual = 0.001 * float(np.linalg.norm(reference_points.max(axis=0) - reference_points.min(axis=0)))
    candidates = [(np.eye(3), reference_center - model_center)]
    for permutation in permutations:
        for flip in flips:
            initial = reference_frame @ flip @ permutation @ model_frame.T
            if np.linalg.det(initial) > 0:
                candidates.append((initial, reference_center - initial @ model_center))
    best: tuple[Any, Any, float] | None = None
    for index, (initial_rotation, initial_translation) in enumerate(candidates):
        if index % 2 == 0:
            deadline.check("rigid registration")
        rotation, translation, error = _icp(
            subset, tree, reference_points, initial_rotation, initial_translation, 12, 0.7
        )
        if best is None or error < best[2]:
            best = (rotation, translation, error)
        # Exact rigid copies converge to a very small point-cloud residual. Once
        # one is found, evaluating deliberately bad PCA sign/permutation starts
        # cannot improve a surface-level F score and only burns build budget.
        if error <= congruent_residual:
            break
    if best is None or not math.isfinite(best[2]):
        raise RegistrationError("rigid registration did not produce a finite candidate")
    rotation, translation = best[:2]
    if best[2] > congruent_residual:
        for trim in (0.8, 0.9, 0.95):
            deadline.check("rigid registration")
            rotation, translation, _ = _icp(
                model_points, tree, reference_points, rotation, translation, 15, trim
            )
    determinant = float(np.linalg.det(rotation))
    orthogonal_error = float(np.max(np.abs(rotation.T @ rotation - np.eye(3))))
    if not np.isfinite(rotation).all() or not np.isfinite(translation).all():
        raise RegistrationError("rigid registration returned non-finite transform values")
    if determinant <= 0 or abs(determinant - 1.0) > 1e-8 or orthogonal_error > 1e-8:
        raise RegistrationError("registration did not produce a proper rigid transform with determinant +1")
    return rotation, translation


@dataclass
class _VtkLocator:
    locator: Any
    polydata: Any
    points: Any
    cells: Any
    vtk_vertices: Any
    vtk_offsets: Any
    vtk_connectivity: Any
    mesh: TriangleMesh
    clean: Any | None = None


def _make_polydata(mesh: TriangleMesh, deadline: _Deadline) -> _VtkLocator:
    np = _numpy()
    vtk = _vtk_modules()
    deadline.check("cell locator build")
    vertices = np.ascontiguousarray(mesh.vertices, dtype=np.float64)
    faces = np.ascontiguousarray(mesh.faces, dtype=np.int32)
    if len(vertices) > np.iinfo(np.int32).max or len(faces) > np.iinfo(np.int32).max:
        raise ComparisonError("mesh exceeds the VTK 32-bit cell-array limit")
    offsets = np.arange(0, len(faces) * 3 + 1, 3, dtype=np.int32)
    connectivity = faces.reshape(-1)
    ns = vtk["numpy_support"]
    vtk_vertices = ns.numpy_to_vtk(vertices, deep=False)
    points = vtk["vtkPoints"]()
    points.SetData(vtk_vertices)
    vtk_offsets = ns.numpy_to_vtk(offsets, deep=False, array_type=vtk["VTK_INT"])
    vtk_connectivity = ns.numpy_to_vtk(connectivity, deep=False, array_type=vtk["VTK_INT"])
    cells = vtk["vtkCellArray"]()
    if not cells.SetData(vtk_offsets, vtk_connectivity):
        raise ComparisonUnavailable("VTK could not accept the triangle cell array")
    polydata = vtk["vtkPolyData"]()
    polydata.SetPoints(points)
    polydata.SetPolys(cells)
    return _VtkLocator(None, polydata, points, cells, vtk_vertices, vtk_offsets, vtk_connectivity, mesh)


def _build_locator(mesh: TriangleMesh, deadline: _Deadline) -> _VtkLocator:
    vtk = _vtk_modules()
    built = _make_polydata(mesh, deadline)
    locator = vtk["vtkStaticCellLocator"]()
    locator.SetDataSet(built.polydata)
    locator.BuildLocator()
    deadline.check("cell locator build")
    built.locator = locator
    return built


def _exact_distances(points: Any, mesh: TriangleMesh, deadline: _Deadline) -> Any:
    np = _numpy()
    vtk = _vtk_modules()
    built = _build_locator(mesh, deadline)
    result = np.empty(len(points), dtype=np.float64)
    closest = [0.0, 0.0, 0.0]
    cell = vtk["vtkGenericCell"]()
    cell_id = vtk["reference"](0)
    sub_id = vtk["reference"](0)
    squared_distance = vtk["reference"](0.0)
    for index, point in enumerate(points):
        if index % 128 == 0:
            deadline.check("exact point-to-triangle distance queries")
        built.locator.FindClosestPoint(point, closest, cell, cell_id, sub_id, squared_distance)
        value = float(squared_distance.get())
        if not math.isfinite(value) or value < 0:
            raise ComparisonError("VTK returned an invalid point-to-triangle distance")
        result[index] = math.sqrt(value)
    deadline.check("exact point-to-triangle distance queries")
    return result


def _f_score(model_distances: Any, reference_distances: Any, tau_mm: float) -> dict[str, float]:
    precision = float((model_distances <= tau_mm).mean())
    recall = float((reference_distances <= tau_mm).mean())
    score = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "F": score}


def _mesh_extent(mesh: TriangleMesh) -> Any:
    np = _numpy()
    return mesh.vertices.max(axis=0) - mesh.vertices.min(axis=0)


def _solid_indices(mesh: TriangleMesh) -> list[int]:
    np = _numpy()
    if mesh.solid_ids is None:
        return [0]
    return [int(value) for value in np.unique(mesh.solid_ids)]


def _mesh_for_solid(mesh: TriangleMesh, solid_id: int) -> TriangleMesh:
    np = _numpy()
    if mesh.solid_ids is None:
        return TriangleMesh(mesh.vertices, mesh.faces, merge_for_closed=mesh.merge_for_closed)
    selected = mesh.faces[mesh.solid_ids == solid_id]
    if len(selected) == 0:
        raise ComparisonError("solid grouping contains an empty surface")
    return TriangleMesh(mesh.vertices, selected, merge_for_closed=mesh.merge_for_closed)


def _closed_locator(mesh: TriangleMesh, deadline: _Deadline) -> tuple[bool, _VtkLocator | None]:
    vtk = _vtk_modules()
    try:
        from vtkmodules.vtkFiltersCore import vtkCleanPolyData, vtkFeatureEdges
    except ImportError as exc:
        raise ComparisonUnavailable(f"VTK closed-surface checks are not available: {exc}") from exc
    built = _make_polydata(mesh, deadline)
    dataset = built.polydata
    clean = None
    if mesh.merge_for_closed:
        clean = vtkCleanPolyData()
        clean.SetInputData(dataset)
        clean.PointMergingOn()
        clean.ToleranceIsAbsoluteOn()
        clean.SetAbsoluteTolerance(1e-5)
        clean.Update()
        dataset = clean.GetOutput()
    edge_filter = vtkFeatureEdges()
    edge_filter.SetInputData(dataset)
    edge_filter.BoundaryEdgesOn()
    edge_filter.NonManifoldEdgesOn()
    edge_filter.FeatureEdgesOff()
    edge_filter.ManifoldEdgesOff()
    edge_filter.Update()
    if edge_filter.GetOutput().GetNumberOfCells() != 0:
        return False, None
    locator = vtk["vtkStaticCellLocator"]()
    locator.SetDataSet(dataset)
    locator.BuildLocator()
    deadline.check("closed-solid locator build")
    # Keep the clean filter, its input, and its output alive for the locator.
    built.clean = clean
    built.polydata = dataset
    built.locator = locator
    return True, built


def _voxel_occupancy(
    mesh: TriangleMesh,
    label: str,
    grid_min: Any,
    pitch: float,
    shape: tuple[int, int, int],
    x_centers: Any,
    y_centers: Any,
    deadline: _Deadline,
) -> Any:
    np = _numpy()
    vtk = _vtk_modules()
    occupancy = np.zeros(shape, dtype=np.bool_)
    intersections = vtk["vtkPoints"]()
    intersections.SetDataTypeToDouble()
    intersection_cells = vtk["vtkIdList"]()
    cell = vtk["vtkGenericCell"]()
    dedupe_tolerance = max(1e-9, pitch * 1e-7)
    for solid_id in _solid_indices(mesh):
        deadline.check("volumetric IoU")
        solid_mesh = _mesh_for_solid(mesh, solid_id)
        closed, built = _closed_locator(solid_mesh, deadline)
        if not closed or built is None:
            raise _OpenSurfaceError(
                f"{label} contains an open or non-manifold surface; volumetric IoU requires closed solids"
            )
        low = solid_mesh.vertices.min(axis=0)
        high = solid_mesh.vertices.max(axis=0)
        ix = np.flatnonzero((x_centers >= low[0]) & (x_centers <= high[0]))
        iy = np.flatnonzero((y_centers >= low[1]) & (y_centers <= high[1]))
        for x_index in ix:
            x = float(x_centers[x_index] + 1.37e-4 * pitch)
            for y_index in iy:
                if (int(x_index) * len(iy) + int(y_index)) % 4096 == 0:
                    deadline.check("volumetric IoU ray casting")
                y = float(y_centers[y_index] + 2.11e-4 * pitch)
                start = (x, y, float(low[2] - pitch))
                end = (x, y, float(high[2] + pitch))
                intersections.Initialize()
                intersection_cells.Reset()
                built.locator.IntersectWithLine(start, end, 0.0, intersections, intersection_cells, cell)
                hits = sorted(
                    float(intersections.GetPoint(hit_index)[2])
                    for hit_index in range(intersections.GetNumberOfPoints())
                )
                if len(hits) > 100_000:
                    raise ComparisonError("closed-solid ray crossed too many surfaces")
                unique_hits: list[float] = []
                for hit_z in hits:
                    if not math.isfinite(hit_z):
                        raise ComparisonError("closed-solid ray returned a non-finite surface crossing")
                    if not unique_hits or hit_z - unique_hits[-1] > dedupe_tolerance:
                        unique_hits.append(hit_z)
                for first_hit, second_hit in zip(unique_hits[0::2], unique_hits[1::2]):
                    k0 = int(math.ceil((first_hit - grid_min[2]) / pitch - 0.5))
                    k1 = int(math.floor((second_hit - grid_min[2]) / pitch - 0.5))
                    if k1 >= k0:
                        occupancy[x_index, y_index, max(k0, 0):min(k1 + 1, shape[2])] = True
    return occupancy


def _volume_iou(model: TriangleMesh, reference: TriangleMesh, deadline: _Deadline, resolution: int) -> dict[str, Any]:
    np = _numpy()
    model_count, reference_count = len(_solid_indices(model)), len(_solid_indices(reference))
    all_vertices_min = np.minimum(model.vertices.min(axis=0), reference.vertices.min(axis=0))
    all_vertices_max = np.maximum(model.vertices.max(axis=0), reference.vertices.max(axis=0))
    reference_extent = reference.vertices.max(axis=0) - reference.vertices.min(axis=0)
    pitch = float(reference_extent.max()) / resolution
    if not math.isfinite(pitch) or pitch <= 0:
        return {"status": "not available", "reason": "reference has no positive bounding-box extent"}
    grid_shape = tuple((np.ceil((all_vertices_max - all_vertices_min) / pitch).astype(int) + 1).tolist())
    voxel_count = math.prod(grid_shape)
    if voxel_count > MAX_IOU_VOXELS:
        return {
            "status": "not available",
            "reason": f"voxel grid exceeds the {MAX_IOU_VOXELS:,} cell IoU limit",
            "pitch_mm": pitch,
        }
    deadline.check("volumetric IoU grid allocation")
    x_centers = all_vertices_min[0] + (np.arange(grid_shape[0]) + 0.5) * pitch
    y_centers = all_vertices_min[1] + (np.arange(grid_shape[1]) + 0.5) * pitch
    try:
        model_occupancy = _voxel_occupancy(
            model, "model", all_vertices_min, pitch, grid_shape, x_centers, y_centers, deadline
        )
        reference_occupancy = _voxel_occupancy(
            reference, "reference", all_vertices_min, pitch, grid_shape, x_centers, y_centers, deadline
        )
    except ComparisonError as exc:
        if isinstance(exc, ComparisonTimeout):
            raise
        return {
            "status": "not available",
            "reason": str(exc),
            "pitch_mm": pitch,
            "closed_solids": {"model": f"unknown/{model_count}", "reference": f"unknown/{reference_count}"},
        }
    intersection = int(np.count_nonzero(model_occupancy & reference_occupancy))
    union = int(np.count_nonzero(model_occupancy | reference_occupancy))
    if union == 0:
        return {"status": "not available", "reason": "both voxelized volumes were empty", "pitch_mm": pitch}
    return {
        "status": "available",
        "value": round(intersection / union, 5),
        "pitch_mm": round(pitch, 3),
        "vol_model_mm3_vox": float(model_occupancy.sum() * pitch ** 3),
        "vol_ref_mm3_vox": float(reference_occupancy.sum() * pitch ** 3),
        "closed_solids": {"model": f"{model_count}/{model_count}", "reference": f"{reference_count}/{reference_count}"},
    }


def _compare_meshes(
    model: TriangleMesh,
    reference: TriangleMesh,
    *,
    tau_pct: float,
    seed: int,
    time_budget_s: float,
    compute_iou: bool,
    iou_resolution: int,
    deadline: _Deadline,
) -> dict[str, Any]:
    np = _numpy()
    _scipy_tree()
    _vtk_modules()
    model = _validate_mesh(model, label="model")
    reference = _validate_mesh(reference, label="reference")
    if isinstance(tau_pct, bool) or not isinstance(tau_pct, (int, float)) or not math.isfinite(float(tau_pct)) or not 0 < float(tau_pct) <= 100:
        raise ComparisonError("tau_pct must be greater than 0 and at most 100")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ComparisonError("seed must be an integer")
    if isinstance(iou_resolution, bool) or not isinstance(iou_resolution, int) or not 16 <= iou_resolution <= 256:
        raise ComparisonError("IoU resolution must be an integer from 16 through 256")

    reference_bounds = reference.vertices.max(axis=0) - reference.vertices.min(axis=0)
    diagonal = float(np.linalg.norm(reference_bounds))
    if not math.isfinite(diagonal) or diagonal <= 0:
        raise ComparisonError("reference bounding box has no positive diagonal")
    model_areas = _triangle_areas(model, deadline)
    reference_areas = _triangle_areas(reference, deadline)
    rng = np.random.default_rng(seed)
    model_registration_points = _sample(model, model_areas, REGISTRATION_SAMPLE_COUNT, rng, deadline)
    reference_registration_points = _sample(reference, reference_areas, REGISTRATION_SAMPLE_COUNT, rng, deadline)
    rotation, translation = _register(model_registration_points, reference_registration_points, deadline)
    transformed_model = TriangleMesh(
        vertices=np.ascontiguousarray(model.vertices @ rotation.T + translation),
        faces=model.faces,
        solid_ids=model.solid_ids,
        merge_for_closed=model.merge_for_closed,
    )
    transformed_areas = _triangle_areas(transformed_model, deadline)
    model_samples = _sample(transformed_model, transformed_areas, SAMPLE_COUNT, rng, deadline)
    reference_samples = _sample(reference, reference_areas, SAMPLE_COUNT, rng, deadline)
    model_distances = _exact_distances(model_samples, reference, deadline)
    reference_distances = _exact_distances(reference_samples, transformed_model, deadline)

    scores: dict[str, dict[str, float]] = {}
    for fraction in TAUS:
        percentage = fraction * 100.0
        key = f"F@{percentage:g}%D"
        tau_mm = fraction * diagonal
        values = _f_score(model_distances, reference_distances, tau_mm)
        scores[key] = {
            "tau_mm": round(tau_mm, 3),
            "precision": round(values["precision"], 5),
            "recall": round(values["recall"], 5),
            "F": round(values["F"], 5),
        }
    selected_fraction = float(tau_pct) / 100.0
    selected_key = f"F@{float(tau_pct):g}%D"
    if selected_key not in scores:
        selected = _f_score(model_distances, reference_distances, selected_fraction * diagonal)
        scores[selected_key] = {
            "tau_mm": round(selected_fraction * diagonal, 3),
            "precision": round(selected["precision"], 5),
            "recall": round(selected["recall"], 5),
            "F": round(selected["F"], 5),
        }
    selected_score = scores[selected_key]
    rotation_det = float(np.linalg.det(rotation))
    result: dict[str, Any] = {
        "D_mm": round(diagonal, 3),
        "tau_gate_mm": round(selected_fraction * diagonal, 3),
        "tau_pct_of_diagonal": float(tau_pct),
        "sample_count_per_side": SAMPLE_COUNT,
        **scores,
        "selected_f_score": selected_score["F"],
        "chamfer_mm": round(float((model_distances.mean() + reference_distances.mean()) / 2), 4),
        "hausdorff_mm": round(float(max(model_distances.max(), reference_distances.max())), 3),
        "p95_model_to_ref_mm": round(float(np.quantile(model_distances, 0.95)), 3),
        "p95_ref_to_model_mm": round(float(np.quantile(reference_distances, 0.95)), 3),
        "extent_model_mm": np.round(_mesh_extent(transformed_model), 2).tolist(),
        "extent_ref_mm": np.round(_mesh_extent(reference), 2).tolist(),
        "area_ratio_model_over_ref": round(float(transformed_areas.sum() / reference_areas.sum()), 4),
        "solids_model": len(_solid_indices(model)),
        "solids_ref": len(_solid_indices(reference)),
        "rotation": np.round(rotation, 10).tolist(),
        "translation_mm": np.round(translation, 3).tolist(),
        "det_R": round(rotation_det, 10),
        "GATE_F": selected_score["F"],
        "GATE_PASS": bool(selected_score["F"] >= GATE_MIN),
    }
    if compute_iou:
        iou_result = _volume_iou(transformed_model, reference, deadline, iou_resolution)
        if iou_result.get("status") == "available":
            result["IoU"] = iou_result["value"]
        else:
            result["IoU"] = "not available"
            result["iou_reason"] = iou_result.get("reason", "closed-solid occupancy was not established")
        result["iou_status"] = iou_result
    else:
        result["iou_status"] = {"status": "not computed", "reason": "IoU is omitted from this spec check."}
    deadline.check("comparison completion")
    result["seconds"] = round(deadline.elapsed, 3)
    return result


def compare_meshes(
    model: TriangleMesh,
    reference: TriangleMesh,
    *,
    tau_pct: float = 0.1,
    seed: int = 7,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    compute_iou: bool = True,
    iou_resolution: int = IOU_RESOLUTION,
) -> dict[str, Any]:
    """Compare two in-memory triangle meshes with fixed 100k samples per side."""
    deadline = _Deadline(time_budget_s)
    return _compare_meshes(
        model, reference, tau_pct=tau_pct, seed=seed,
        time_budget_s=time_budget_s, compute_iou=compute_iou,
        iou_resolution=iou_resolution, deadline=deadline,
    )


def compare_shape_to_reference(
    shape: Any,
    reference_path: str | os.PathLike[str],
    *,
    model_units: str = "mm",
    reference_units: str = "mm",
    tau_pct: float = 0.1,
    seed: int = 7,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    compute_iou: bool = True,
    iou_resolution: int = IOU_RESOLUTION,
) -> dict[str, Any]:
    """Tessellate a model shape, parse an external reference, and compare them."""
    deadline = _Deadline(time_budget_s)
    _numpy()
    _scipy_tree()
    _vtk_modules()
    deadline.check("comparison dependency loading")
    path = _validate_reference_file(reference_path)
    model_mesh = mesh_from_shape(shape, units=model_units, deadline=deadline)
    reference_mesh = _read_reference(path, units=reference_units, deadline=deadline)
    result = _compare_meshes(
        model_mesh, reference_mesh, tau_pct=tau_pct, seed=seed,
        time_budget_s=time_budget_s, compute_iou=compute_iou,
        iou_resolution=iou_resolution, deadline=deadline,
    )
    result["reference"] = path.name
    return result
