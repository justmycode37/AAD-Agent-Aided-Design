from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

import aad_platform.render_mesh as mesh_worker
import aad_platform.ops as operations
from aad_platform.ops import _render_result


def test_mesh_worker_caps_json_bytes_before_parsing(monkeypatch):
    from aad_render import render_limits

    raw = b'{"render":{}}'
    monkeypatch.setattr(render_limits, "MAX_RENDER_PAYLOAD_BYTES", len(raw) - 1)

    with pytest.raises(ValueError, match="render payload exceeds"):
        mesh_worker._read_request(BytesIO(raw))


def test_mesh_worker_rejects_nonfinite_json_constants():
    with pytest.raises(ValueError, match="non-finite"):
        mesh_worker._read_request(BytesIO(b'{"render":{"coordinate":NaN}}'))


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
