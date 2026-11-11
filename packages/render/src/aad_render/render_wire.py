"""Bounded binary transport for prepared render meshes."""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np

from . import render_limits

RENDER_WIRE_MAGIC = b"AADRMESH"
_HEADER_LENGTH = struct.Struct(">I")
_BATCH_COUNT = struct.Struct(">H")
_BATCH_ID_LENGTH = struct.Struct(">B")
_BATCH_PAYLOAD_LENGTH = struct.Struct(">Q")
_ALIGNMENT = 8
_ARRAY_TAG = "__aad_render_array__"
RENDER_BATCH_MAGIC = b"AADRBAT1"
_BATCH_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ARRAY_FIELDS = {
    "points": ("f64le", np.dtype("<f8")),
    "triangles": ("u32le", np.dtype("<u4")),
    "triangle_colors": ("u8", np.dtype("u1")),
}


@dataclass(frozen=True, slots=True)
class RenderWirePayload:
    """A small JSON header plus zero-copy views of the mesh arrays."""

    header: bytes
    chunks: tuple[bytes | memoryview, ...]
    size: int

    def iter_bytes(self) -> Iterator[bytes | memoryview]:
        yield RENDER_WIRE_MAGIC
        yield _HEADER_LENGTH.pack(len(self.header))
        yield self.header
        yield from self.chunks

    def to_bytes(self) -> bytes:
        return b"".join(self.iter_bytes())


@dataclass(frozen=True, slots=True)
class RenderWireBatch:
    """Bounded sequence of individually versioned mesh payloads."""

    cases: tuple[tuple[str, RenderWirePayload], ...]
    size: int

    def iter_bytes(self) -> Iterator[bytes | memoryview]:
        yield RENDER_BATCH_MAGIC
        yield _BATCH_COUNT.pack(len(self.cases))
        for case_id, payload in self.cases:
            encoded_id = case_id.encode("ascii")
            yield _BATCH_ID_LENGTH.pack(len(encoded_id))
            yield encoded_id
            yield _BATCH_PAYLOAD_LENGTH.pack(payload.size)
            yield from payload.iter_bytes()

    def to_bytes(self) -> bytes:
        return b"".join(self.iter_bytes())


def pack_render_batch(cases: list[tuple[str, dict[str, Any]]]) -> RenderWireBatch:
    """Pack a bounded set of render cases for one clean VTK process."""
    if not cases or len(cases) > render_limits.MAX_REVIEW_PACKET_CASES:
        raise ValueError(f"review packet must contain 1 to {render_limits.MAX_REVIEW_PACKET_CASES} render cases")
    packed: list[tuple[str, RenderWirePayload]] = []
    total = len(RENDER_BATCH_MAGIC) + _BATCH_COUNT.size
    for case_id, render in cases:
        if not isinstance(case_id, str) or not _BATCH_ID.fullmatch(case_id):
            raise ValueError("review packet contains an invalid render case ID")
        payload = pack_render_payload(render)
        total += _BATCH_ID_LENGTH.size + len(case_id) + _BATCH_PAYLOAD_LENGTH.size + payload.size
        if total > render_limits.MAX_RENDER_PAYLOAD_BYTES:
            raise ValueError(
                f"review packet render payload exceeds the {render_limits.MAX_RENDER_PAYLOAD_BYTES}-byte limit"
            )
        packed.append((case_id, payload))
    return RenderWireBatch(tuple(packed), total)


def iter_render_batch(data: bytes) -> Iterator[tuple[str, bytes]]:
    """Decode one render case at a time from a bounded batch record."""
    if not isinstance(data, bytes) or not data.startswith(RENDER_BATCH_MAGIC):
        raise ValueError("render batch has an invalid binary header")
    if len(data) > render_limits.MAX_RENDER_PAYLOAD_BYTES:
        raise ValueError(f"render batch exceeds the {render_limits.MAX_RENDER_PAYLOAD_BYTES}-byte limit")
    view = memoryview(data)
    cursor = len(RENDER_BATCH_MAGIC)
    if cursor + _BATCH_COUNT.size > len(view):
        raise ValueError("render batch header is truncated")
    count = _BATCH_COUNT.unpack_from(view, cursor)[0]
    cursor += _BATCH_COUNT.size
    if count < 1 or count > render_limits.MAX_REVIEW_PACKET_CASES:
        raise ValueError("render batch has an invalid case count")
    seen: set[str] = set()
    for _ in range(count):
        if cursor + _BATCH_ID_LENGTH.size > len(view):
            raise ValueError("render batch case ID is truncated")
        id_length = _BATCH_ID_LENGTH.unpack_from(view, cursor)[0]
        cursor += _BATCH_ID_LENGTH.size
        if id_length < 1 or cursor + id_length + _BATCH_PAYLOAD_LENGTH.size > len(view):
            raise ValueError("render batch case header is out of bounds")
        try:
            case_id = bytes(view[cursor:cursor + id_length]).decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("render batch case ID is not ASCII") from exc
        cursor += id_length
        if not _BATCH_ID.fullmatch(case_id) or case_id in seen:
            raise ValueError("render batch contains an invalid or duplicate case ID")
        seen.add(case_id)
        payload_size = _BATCH_PAYLOAD_LENGTH.unpack_from(view, cursor)[0]
        cursor += _BATCH_PAYLOAD_LENGTH.size
        if payload_size > render_limits.MAX_RENDER_PAYLOAD_BYTES or cursor + payload_size > len(view):
            raise ValueError("render batch case payload is out of bounds")
        payload = bytes(view[cursor:cursor + payload_size])
        cursor += payload_size
        yield case_id, payload
    if cursor != len(view):
        raise ValueError("render batch contains trailing bytes")


def pack_render_payload(render: dict[str, Any]) -> RenderWirePayload:
    """Prepare a render record for bounded transfer without JSON numeric arrays."""
    if not isinstance(render, dict):
        raise ValueError("render payload must be an object")
    chunks: list[bytes | memoryview] = []
    offset = 0
    vertex_count = triangle_count = 0
    scene = render.get("scene")
    if isinstance(scene, dict):
        scene_copy = dict(scene)
        faces = scene.get("faces")
        lines = scene.get("lines")
        if isinstance(faces, list):
            if len(faces) > render_limits.MAX_RENDER_FACES:
                raise ValueError(f"render has more than {render_limits.MAX_RENDER_FACES} faces")
            encoded_faces = []
            vertex_count = triangle_count = 0
            for face in faces:
                if not isinstance(face, dict):
                    raise ValueError("render face must be an object")
                encoded_face = dict(face)
                point_count = None
                face_triangle_count = None
                if "points" in face:
                    previous_offset = offset
                    points, descriptor, offset = _pack_array(face["points"], "points", offset)
                    vertex_count += len(points)
                    point_count = len(points)
                    encoded_face["points"] = descriptor
                    chunks.extend(_array_chunks(points, descriptor, previous_offset))
                if "triangles" in face:
                    previous_offset = offset
                    triangles, descriptor, offset = _pack_array(face["triangles"], "triangles", offset)
                    triangle_count += len(triangles)
                    face_triangle_count = len(triangles)
                    if point_count is not None and len(triangles):
                        if int(triangles.max()) >= point_count:
                            raise ValueError("triangle index is outside its face vertex array")
                    encoded_face["triangles"] = descriptor
                    chunks.extend(_array_chunks(triangles, descriptor, previous_offset))
                if "triangle_colors" in face:
                    previous_offset = offset
                    colors, descriptor, offset = _pack_array(face["triangle_colors"], "triangle_colors", offset)
                    if len(colors) and face_triangle_count is not None and len(colors) != face_triangle_count:
                        raise ValueError("render triangle colors must match the triangle count")
                    encoded_face["triangle_colors"] = descriptor
                    chunks.extend(_array_chunks(colors, descriptor, previous_offset))
                if vertex_count > render_limits.MAX_RENDER_VERTICES:
                    raise ValueError(f"render has more than {render_limits.MAX_RENDER_VERTICES} vertices")
                if triangle_count > render_limits.MAX_RENDER_TRIANGLES:
                    raise ValueError(f"render has more than {render_limits.MAX_RENDER_TRIANGLES} triangles")
                encoded_faces.append(encoded_face)
            scene_copy["faces"] = encoded_faces
        if isinstance(lines, list):
            if len(lines) > render_limits.MAX_RENDER_LINES:
                raise ValueError(f"render has more than {render_limits.MAX_RENDER_LINES} lines")
            encoded_lines = []
            for line in lines:
                if not isinstance(line, dict):
                    raise ValueError("render line must be an object")
                encoded_line = dict(line)
                if "points" in line:
                    previous_offset = offset
                    points, descriptor, offset = _pack_array(line["points"], "points", offset)
                    vertex_count += len(points)
                    if vertex_count > render_limits.MAX_RENDER_VERTICES:
                        raise ValueError(f"render has more than {render_limits.MAX_RENDER_VERTICES} vertices")
                    encoded_line["points"] = descriptor
                    chunks.extend(_array_chunks(points, descriptor, previous_offset))
                encoded_lines.append(encoded_line)
            scene_copy["lines"] = encoded_lines
        copied_render = dict(render)
        copied_render["scene"] = scene_copy
    else:
        copied_render = dict(render)

    record = {"render": copied_render}
    render_limits.json_size_limited(record, render_limits.MAX_RENDER_HEADER_BYTES, "render metadata")
    header = json.dumps(record, allow_nan=False, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(header) > render_limits.MAX_RENDER_HEADER_BYTES:
        raise ValueError(f"render metadata exceeds the {render_limits.MAX_RENDER_HEADER_BYTES}-byte limit")
    prefix_size = len(RENDER_WIRE_MAGIC) + _HEADER_LENGTH.size + len(header)
    padding_size = _aligned(prefix_size) - prefix_size
    prefix = [RENDER_WIRE_MAGIC, _HEADER_LENGTH.pack(len(header)), header]
    if padding_size:
        prefix.append(bytes(padding_size))
    all_chunks = tuple(prefix + chunks)
    size = sum(len(chunk) for chunk in all_chunks)
    return RenderWirePayload(header, tuple(all_chunks[3:]), size)


def decode_render_payload(data: bytes) -> tuple[dict[str, Any], int]:
    """Decode a render wire record, validating array ranges before creating views."""
    if not isinstance(data, bytes) or not data.startswith(RENDER_WIRE_MAGIC):
        raise ValueError("render payload has an invalid binary header")
    if len(data) < len(RENDER_WIRE_MAGIC) + _HEADER_LENGTH.size:
        raise ValueError("render payload header is truncated")
    header_len = _HEADER_LENGTH.unpack_from(data, len(RENDER_WIRE_MAGIC))[0]
    header_start = len(RENDER_WIRE_MAGIC) + _HEADER_LENGTH.size
    if header_len > render_limits.MAX_RENDER_HEADER_BYTES or header_start + header_len > len(data):
        raise ValueError("render payload metadata header is out of bounds")
    header_end = header_start + header_len
    try:
        request = json.loads(data[header_start:header_end], parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("render payload metadata is invalid JSON") from exc
    if not isinstance(request, dict) or set(request) != {"render"} or not isinstance(request["render"], dict):
        raise ValueError("mesh renderer request must contain one render object")

    data_start = _aligned(header_end)
    if data_start > len(data) or any(data[header_end:data_start]):
        raise ValueError("render payload header padding is invalid")
    body = memoryview(data)
    cursor = 0
    vertex_count = triangle_count = 0
    scene = request["render"].get("scene")
    if scene is not None:
        if not isinstance(scene, dict):
            raise ValueError("render scene metadata must be an object")
        faces = scene.get("faces")
        lines = scene.get("lines")
        if not isinstance(faces, list) or not isinstance(lines, list):
            raise ValueError("render scene must contain face and line arrays")
        if len(faces) > render_limits.MAX_RENDER_FACES or len(lines) > render_limits.MAX_RENDER_LINES:
            raise ValueError("render scene exceeds its face or line limit")
        for face in faces:
            if not isinstance(face, dict):
                raise ValueError("render face must be an object")
            for field in ("points", "triangles", "triangle_colors"):
                if field not in face or not _is_descriptor(face[field]):
                    raise ValueError(f"render face {field} must use a bounded binary array")
                face[field], cursor = _unpack_array(face[field], field, body, data_start, cursor)
            vertex_count += len(face["points"])
            triangle_count += len(face["triangles"])
            if triangle_count > render_limits.MAX_RENDER_TRIANGLES:
                raise ValueError(f"render has more than {render_limits.MAX_RENDER_TRIANGLES} triangles")
            if vertex_count > render_limits.MAX_RENDER_VERTICES:
                raise ValueError(f"render has more than {render_limits.MAX_RENDER_VERTICES} vertices")
        for line in lines:
            if not isinstance(line, dict) or "points" not in line or not _is_descriptor(line["points"]):
                raise ValueError("render line points must use a bounded binary array")
            line["points"], cursor = _unpack_array(line["points"], "points", body, data_start, cursor)
            vertex_count += len(line["points"])
            if vertex_count > render_limits.MAX_RENDER_VERTICES:
                raise ValueError(f"render has more than {render_limits.MAX_RENDER_VERTICES} vertices")
    if data_start + cursor != len(data):
        raise ValueError("render payload has trailing or unreferenced binary data")
    return request, len(data)


def render_payload_size(render: dict[str, Any]) -> int:
    """Return the packed size without copying the numeric arrays."""
    return pack_render_payload(render).size


def _pack_array(value: Any, field: str, offset: int) -> tuple[np.ndarray, dict[str, Any], int]:
    tag, dtype = _ARRAY_FIELDS[field]
    if field == "points":
        array = _as_points(value)
    elif field == "triangles":
        array = _as_triangles(value)
    else:
        array = _as_triangle_colors(value)
    padding = _aligned(offset) - offset
    offset += padding
    descriptor = {
        _ARRAY_TAG: tag,
        "shape": [int(array.shape[0]), 3],
        "offset": offset,
        "nbytes": int(array.nbytes),
    }
    return array, descriptor, offset + array.nbytes


def _array_chunks(array: np.ndarray, descriptor: dict[str, Any], previous_offset: int) -> tuple[bytes | memoryview, ...]:
    padding_size = descriptor["offset"] - previous_offset
    padding = (bytes(padding_size),) if padding_size else ()
    data = (memoryview(array).cast("B"),) if array.nbytes else ()
    return (*padding, *data)


def _as_points(value: Any) -> np.ndarray:
    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("render points must be an N by 3 numeric array") from exc
    if array.ndim != 2 or array.shape[1] != 3 or array.dtype.kind not in "fiu" or array.dtype.kind == "b":
        raise ValueError("render points must be an N by 3 numeric array")
    array = np.ascontiguousarray(array, dtype="<f8")
    if array.size and not np.isfinite(array).all():
        raise ValueError("render points contain non-finite coordinates")
    return array


def _as_triangles(value: Any) -> np.ndarray:
    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("render triangles must be an N by 3 integer array") from exc
    if array.ndim != 2 or array.shape[1] != 3 or array.dtype.kind not in "iu":
        raise ValueError("render triangles must be an N by 3 integer array")
    if array.size and (int(array.min()) < 0 or int(array.max()) > np.iinfo(np.uint32).max):
        raise ValueError("render triangle index is outside the supported range")
    return np.ascontiguousarray(array, dtype="<u4")


def _as_triangle_colors(value: Any) -> np.ndarray:
    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("triangle colors must be an N by 3 byte array") from exc
    if array.ndim != 2 or array.shape[1] != 3 or array.dtype.kind not in "iu":
        raise ValueError("triangle colors must be an N by 3 byte array")
    if array.size and (int(array.min()) < 0 or int(array.max()) > 255):
        raise ValueError("triangle colors must contain byte values")
    if len(array) > render_limits.MAX_RENDER_TRIANGLES:
        raise ValueError(f"render has more than {render_limits.MAX_RENDER_TRIANGLES} triangle colors")
    return np.ascontiguousarray(array, dtype="u1")


def _is_descriptor(value: Any) -> bool:
    return isinstance(value, dict) and _ARRAY_TAG in value


def _unpack_array(
    descriptor: Any,
    field: str,
    buffer: memoryview,
    data_start: int,
    cursor: int,
) -> tuple[np.ndarray, int]:
    if not isinstance(descriptor, dict) or set(descriptor) != {_ARRAY_TAG, "shape", "offset", "nbytes"}:
        raise ValueError("render mesh array descriptor has unexpected fields")
    tag, dtype = _ARRAY_FIELDS[field]
    if descriptor[_ARRAY_TAG] != tag:
        raise ValueError(f"render {field} array has an unexpected encoding")
    shape = descriptor["shape"]
    count_limit = (
        render_limits.MAX_RENDER_VERTICES if field == "points" else render_limits.MAX_RENDER_TRIANGLES
    )
    if (
        not isinstance(shape, list)
        or len(shape) != 2
        or any(isinstance(count, bool) or not isinstance(count, int) for count in shape)
        or shape[1] != 3
        or shape[0] < 0
    ):
        raise ValueError(f"render {field} array has an invalid shape")
    if shape[0] > count_limit:
        noun = "vertices" if field == "points" else "triangles"
        raise ValueError(f"render has more than {count_limit} {noun}")
    row_count = shape[0]
    item_count = row_count * 3
    nbytes = item_count * dtype.itemsize
    offset = descriptor["offset"]
    declared_nbytes = descriptor["nbytes"]
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset != _aligned(cursor)
        or isinstance(declared_nbytes, bool)
        or not isinstance(declared_nbytes, int)
        or declared_nbytes != nbytes
        or data_start + offset + nbytes > len(buffer)
    ):
        raise ValueError(f"render {field} array is out of bounds")
    if any(buffer[data_start + cursor : data_start + offset]):
        raise ValueError(f"render {field} array padding is invalid")
    start = data_start + offset
    array = np.frombuffer(buffer, dtype=dtype, count=item_count, offset=start).reshape(row_count, 3)
    return array, offset + nbytes


def _aligned(value: int) -> int:
    return (value + _ALIGNMENT - 1) & -_ALIGNMENT


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token} is not allowed")
