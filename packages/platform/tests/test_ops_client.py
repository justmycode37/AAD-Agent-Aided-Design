from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from aad.engine import sandbox
from aad_platform.daemon import ops_client
from aad_platform.ops import RESULT_MARKER


class _Worker:
    returncode = 0

    def __init__(self, result):
        self.result = result
        self.input_data = None

    def communicate(self, input_data=None, timeout=None):
        self.input_data = input_data
        return RESULT_MARKER + json.dumps(self.result).encode() + b"\n", b""


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_render_prepares_brep_in_sandbox_and_sends_render_payload_to_vtk(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(ops_client.sys, "platform", platform)
    monkeypatch.delenv("AAD_REMOTE", raising=False)
    prepared = {
        "version": 1,
        "inspection": {"faces": [{"surface": "plane"}]},
        "render": {
            "request": {"backend": "vtk", "views": ["iso"], "style": "shaded", "labels": "none"}
        },
        "render_payload_file": ops_client.PREPARED_RENDER_FILENAME,
    }
    payload_bytes = b'{"render":{"version":1,"scene":{"faces":[{"points":[[0, 0, 0]]}]}}}'
    (tmp_path / ops_client.PREPARED_RENDER_FILENAME).write_bytes(payload_bytes)
    rendered = {"images": ["iso.png"], "labels": None, "image_reports": {"iso.png": {"panes": []}}}
    sandbox_worker = _Worker({"ok": True, "prepared_render": prepared})
    mesh_worker = _Worker({"ok": True, "rendered": rendered})
    sandbox_calls = []
    mesh_calls = []

    def launch_sandbox(argv, out, *, read_paths=()):
        sandbox_calls.append((argv, out, tuple(read_paths)))
        return sandbox_worker

    def launch_mesh(argv, **kwargs):
        mesh_calls.append((argv, kwargs))
        return mesh_worker

    monkeypatch.setattr(sandbox, "popen", launch_sandbox)
    monkeypatch.setattr(sandbox, "environment", lambda _out: {})
    monkeypatch.setattr(ops_client.subprocess, "Popen", launch_mesh)
    monkeypatch.setattr(ops_client, "_render_result", lambda paths: {"artifacts": {}, "images": []})
    import aad_render

    monkeypatch.setattr(aad_render, "finish_render", lambda _prepared, _rendered, _out: [])

    result = ops_client._run_sandboxed(
        {
            "op": "render",
            "project_dir": str(tmp_path / "project"),
            "rev": 7,
            "kind": "part",
            "args": {"views": ["iso"]},
            "out_dir": str(tmp_path),
            "read_paths": [str(tmp_path / "project" / ".aad" / "revs")],
        },
        60.0,
    )

    assert result == {"ok": True, "artifacts": {}, "images": []}
    prepared_request = json.loads(sandbox_worker.input_data)
    assert prepared_request["op"] == "render_prepare"
    assert prepared_request["project_dir"] == str(tmp_path / "project")
    assert sandbox_calls[0][0][-2:] == ["-m", "aad_platform.ops"]
    assert mesh_calls[0][0][-2:] == ["aad_platform.render_mesh", str(tmp_path)]
    assert mesh_worker.input_data == payload_bytes
    mesh_request = json.loads(mesh_worker.input_data)
    assert mesh_request == {"render": {"version": 1, "scene": {"faces": [{"points": [[0, 0, 0]]}]}}}
    assert "inspection" not in mesh_request["render"]
    assert not {"project_dir", "rev", "read_paths", "brep"} & set(mesh_request)
    assert not (tmp_path / ops_client.PREPARED_RENDER_FILENAME).exists()


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"),
    reason="OS sandbox render path",
)
def test_3d_render_parses_a_real_brep_under_sandbox_and_writes_pngs(tmp_path):
    from build123d import Box, export_brep
    from PIL import Image

    project = tmp_path / "project"
    revision = project / ".aad" / "revs" / "000001"
    revision.mkdir(parents=True)
    assert export_brep(Box(8, 6, 4), str(revision / "part.brep"))
    (revision / "meta.json").write_text(json.dumps({"features": [], "meta": {}}))
    out = tmp_path / "render-output"

    result = ops_client._run_sandboxed(
        {
            "op": "render",
            "project": "part",
            "project_dir": str(project),
            "rev": 1,
            "kind": "part",
            "units": "mm",
            "args": {"views": ["iso"], "size": 128},
            "out_dir": str(out),
            "read_paths": [str(project / ".aad" / "revs")],
        },
        120.0,
    )

    assert result["ok"], result
    assert [Path(image["path"]).name for image in result["images"]] == ["iso.png"]
    assert result["inspection"]["faces"]
    with Image.open(out / "iso.png") as image:
        assert image.size == (128, 128)


@pytest.mark.parametrize(("op", "kind"), [("check", "part"), ("render", "2d")])
@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_non_vtk_operations_still_use_sandbox(tmp_path, monkeypatch, op, kind, platform):
    monkeypatch.setattr(ops_client.sys, "platform", platform)
    monkeypatch.delenv("AAD_REMOTE", raising=False)
    worker = _Worker({"ok": True, "checked": True})
    sandbox_calls = []

    def launch_sandbox(argv, out, *, read_paths=()):
        sandbox_calls.append((argv, out, tuple(read_paths)))
        return worker

    monkeypatch.setattr(sandbox, "popen", launch_sandbox)
    monkeypatch.setattr(ops_client.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must use Seatbelt"))
    request = {
        "op": op,
        "project_dir": str(tmp_path / "project"),
        "rev": 1,
        "kind": kind,
        "args": {},
        "out_dir": str(tmp_path),
        "read_paths": [],
    }

    result = ops_client._run_sandboxed(request, 30.0)

    assert result == {"ok": True, "checked": True}
    assert json.loads(worker.input_data)["op"] == op
    assert sandbox_calls


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_remote_render_remains_refused(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(ops_client.sys, "platform", platform)
    monkeypatch.setenv("AAD_REMOTE", "1")
    monkeypatch.setattr(sandbox, "popen", lambda *_a, **_k: pytest.fail("remote render must be refused"))
    result = ops_client._run_sandboxed(
        {"op": "render", "kind": "part", "out_dir": str(tmp_path), "args": {}},
        30.0,
    )
    assert result["ok"] is False
    assert result["failure"]["code"] == "SANDBOX_DENIED"


def test_render_failure_keeps_brep_and_reports_render_phase(tmp_path, monkeypatch):
    revision = tmp_path / "project" / ".aad" / "revs" / "000001"
    revision.mkdir(parents=True)
    brep = revision / "part.brep"
    brep.write_bytes(b"original BREP")
    prepared = {
        "version": 1,
        "inspection": {},
        "render": {"request": {"backend": "vtk", "views": ["iso"], "style": "shaded", "labels": "none"}},
        "render_payload_file": ops_client.PREPARED_RENDER_FILENAME,
    }
    out = tmp_path / "render-output"
    out.mkdir()
    (out / ops_client.PREPARED_RENDER_FILENAME).write_bytes(b'{"render":{}}')
    monkeypatch.setattr(ops_client, "_run_worker", lambda *_args: {"ok": True, "prepared_render": prepared})
    monkeypatch.setattr(sandbox, "environment", lambda _out: {})
    monkeypatch.setattr(
        ops_client.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _Worker({
            "ok": False,
            "failure": {"code": "RUNTIME_ERROR", "phase": "runtime", "message": "renderer failed"},
        }),
    )

    result = ops_client._run_mesh_render(
        {
            "kind": "part",
            "project_dir": str(tmp_path / "project"),
            "rev": 1,
            "args": {},
            "out_dir": str(out),
        },
        60.0,
        sandbox,
    )

    assert result["ok"] is False
    assert result["failure"]["phase"] == "render"
    assert result["failure"]["message"] == "renderer failed"
    assert brep.read_bytes() == b"original BREP"
    assert not (out / ops_client.PREPARED_RENDER_FILENAME).exists()


def test_parent_reads_prepared_mesh_file_with_a_separate_size_cap_and_no_follow(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_client, "MAX_RENDER_PAYLOAD_BYTES", 3)
    path = tmp_path / "prepared-render.json"
    path.write_bytes(b"123")
    assert ops_client._read_prepared_render_payload(path) == b"123"

    path.write_bytes(b"1234")
    with pytest.raises(ValueError, match="3-byte limit"):
        ops_client._read_prepared_render_payload(path)

    target = tmp_path / "outside.json"
    target.write_bytes(b"123")
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(OSError):
        ops_client._read_prepared_render_payload(path)
    assert target.read_bytes() == b"123"


def test_render_prepare_writes_only_mesh_to_bounded_sidecar(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import aad_render
    from aad_platform import ops

    render = {
        "version": 1,
        "request": {"backend": "vtk", "views": ["iso"], "style": "shaded", "labels": "none"},
        "scene": {"faces": [{"points": [[0, 0, 0]]}]},
    }
    inspection = {"faces": [{"surface": "plane"}]}
    monkeypatch.setattr(aad_render, "prepare_render", lambda _ctx, _request: {
        "version": 1, "render": render, "inspection": inspection
    })

    result = ops.op_render_prepare(SimpleNamespace(kind="part"), {}, tmp_path)
    prepared = result["prepared_render"]
    assert prepared["render"] == {"request": render["request"]}
    assert prepared["inspection"] == inspection
    assert prepared["render_payload_file"] == ops.PREPARED_RENDER_FILENAME
    payload_path = tmp_path / ops.PREPARED_RENDER_FILENAME
    payload_bytes = payload_path.read_bytes()
    assert payload_bytes == json.dumps({"render": render}, separators=(",", ":"), allow_nan=False).encode()
    assert json.loads(payload_bytes) == {"render": render}

    symlink_out = tmp_path / "symlink-out"
    symlink_out.mkdir()
    target = tmp_path / "outside.json"
    target.write_bytes(b"keep")
    (symlink_out / ops.PREPARED_RENDER_FILENAME).symlink_to(target)
    with pytest.raises(FileExistsError):
        ops.op_render_prepare(SimpleNamespace(kind="part"), {}, symlink_out)
    assert target.read_bytes() == b"keep"


@pytest.mark.parametrize(("fd", "stream"), [(1, "stdout"), (2, "stderr")])
def test_parent_caps_worker_pipe_reads(tmp_path, fd, stream):
    script = f"import os; os.write({fd}, b'x' * 10000)"
    process = ops_client.subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=ops_client.subprocess.PIPE,
        stdout=ops_client.subprocess.PIPE,
        stderr=ops_client.subprocess.PIPE,
        start_new_session=True,
    )

    result = ops_client._communicate_result(
        process,
        b"",
        10.0,
        stdout_limit=64,
        stderr_limit=64,
    )

    assert result["ok"] is False
    assert result["failure"]["code"] == "WORKER_OUTPUT_LIMIT"
    assert stream in result["failure"]["message"]
