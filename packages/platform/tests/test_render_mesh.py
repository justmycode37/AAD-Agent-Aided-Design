from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

import aad_platform.render_mesh as mesh_worker
import aad_platform.ops as operations
from aad_platform.ops import _render_result


def test_mesh_worker_caps_payload_bytes_before_decoding(monkeypatch):
    from aad_render import render_limits
    from aad_render.render_wire import pack_render_payload

    wire = pack_render_payload({"version": 1}).to_bytes()
    monkeypatch.setattr(render_limits, "MAX_RENDER_PAYLOAD_BYTES", len(wire) - 1)

    with pytest.raises(ValueError, match="render payload exceeds"):
        mesh_worker._read_request(BytesIO(wire))


def test_mesh_worker_returns_the_size_of_the_bounded_binary_record():
    from aad_render.render_wire import pack_render_payload

    wire = pack_render_payload({"version": 1}).to_bytes()
    request, size = mesh_worker._read_request(BytesIO(wire))
    assert request == {"render": {"version": 1}}
    assert size == len(wire)


def test_mesh_worker_decodes_packed_mesh_arrays_without_json_number_lists():
    import numpy as np

    from aad_render.render_wire import pack_render_payload

    points = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
    triangles = np.asarray([[0, 1, 2]], dtype=np.int64)
    wire = pack_render_payload({"version": 1, "scene": {
        "faces": [{
            "points": points,
            "triangles": triangles,
            "triangle_colors": np.empty((0, 3), dtype=np.uint8),
        }], "lines": [],
    }}).to_bytes()

    request, size = mesh_worker._read_request(BytesIO(wire))

    face = request["render"]["scene"]["faces"][0]
    assert size == len(wire)
    assert np.array_equal(face["points"], points)
    assert np.array_equal(face["triangles"], triangles.astype(np.uint32))


def test_mesh_worker_rejects_binary_array_counts_before_creating_views(monkeypatch):
    import numpy as np

    from aad_render import render_limits
    from aad_render.render_wire import pack_render_payload

    wire = pack_render_payload({"version": 1, "scene": {
        "faces": [{
            "points": np.zeros((2, 3), dtype=np.float64),
            "triangles": np.empty((0, 3), dtype=np.int64),
            "triangle_colors": np.empty((0, 3), dtype=np.uint8),
        }], "lines": [],
    }}).to_bytes()
    monkeypatch.setattr(render_limits, "MAX_RENDER_VERTICES", 1)

    with pytest.raises(ValueError, match="vertices"):
        mesh_worker._read_request(BytesIO(wire))


def test_mesh_worker_rejects_trailing_binary_mesh_data():
    import numpy as np

    from aad_render.render_wire import pack_render_payload

    wire = pack_render_payload({"version": 1, "scene": {
        "faces": [{
            "points": np.zeros((2, 3), dtype=np.float64),
            "triangles": np.empty((0, 3), dtype=np.int64),
            "triangle_colors": np.empty((0, 3), dtype=np.uint8),
        }], "lines": [],
    }}).to_bytes()

    with pytest.raises(ValueError, match="trailing or unreferenced"):
        mesh_worker._read_request(BytesIO(wire + b"x"))


def test_mesh_worker_caps_binary_header_before_parsing(monkeypatch):
    import struct

    from aad_render import render_limits
    from aad_render.render_wire import RENDER_WIRE_MAGIC

    monkeypatch.setattr(render_limits, "MAX_RENDER_HEADER_BYTES", 16)
    wire = RENDER_WIRE_MAGIC + struct.pack(">I", 17)
    with pytest.raises(ValueError, match="metadata header is out of bounds"):
        mesh_worker._read_request(BytesIO(wire))


def test_mesh_worker_rejects_nonfinite_json_constants():
    import struct

    from aad_render.render_wire import RENDER_WIRE_MAGIC

    header = b'{"render":{"coordinate":NaN}}'
    wire = RENDER_WIRE_MAGIC + struct.pack(">I", len(header)) + header
    with pytest.raises(ValueError, match="non-finite"):
        mesh_worker._read_request(BytesIO(wire))


def test_parent_caps_json_artifact_reads(tmp_path, monkeypatch):
    from aad_render import render_limits

    artifact = tmp_path / "inspection.json"
    artifact.write_bytes(b'{"inspection":"too big"}')
    monkeypatch.setattr(render_limits, "MAX_INSPECTION_JSON_BYTES", 8)

    with pytest.raises(ValueError, match="size limit"):
        _render_result([artifact])


def test_parent_refuses_symlinked_json_artifacts(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("{}")
    artifact = tmp_path / "inspection.json"
    artifact.symlink_to(target)

    with pytest.raises(OSError):
        _render_result([artifact])


def test_sandbox_prepare_caps_mesh_payload_before_returning_it(monkeypatch):
    from aad_render import render_limits

    prepared = {"version": 1, "render": {"data": "too much"}, "inspection": {}}
    monkeypatch.setattr(operations, "_required", lambda *_args: lambda *_a: prepared)
    monkeypatch.setattr(operations, "_render_request", lambda args: args)
    monkeypatch.setattr(render_limits, "MAX_RENDER_PAYLOAD_BYTES", 8)

    with pytest.raises(ValueError, match="render payload exceeds"):
        operations.op_render_prepare(SimpleNamespace(kind="assembly"), {}, Path("."))
