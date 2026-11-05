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


def test_macos_render_prepares_brep_in_sandbox_and_sends_render_payload_to_vtk(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_client.sys, "platform", "darwin")
    monkeypatch.delenv("AAD_REMOTE", raising=False)
    prepared = {
        "version": 1,
        "inspection": {"faces": [{"surface": "plane"}]},
        "render": {"version": 1, "scene": {"faces": [{"points": [[0, 0, 0]]}]}},
    }
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
    mesh_request = json.loads(mesh_worker.input_data)
    assert mesh_request == {"render": prepared["render"]}
    assert "inspection" not in mesh_request["render"]
    assert not {"project_dir", "rev", "read_paths", "brep"} & set(mesh_request)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt render path")
def test_macos_render_parses_a_real_brep_under_seatbelt_and_writes_pngs(tmp_path):
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
def test_macos_non_vtk_operations_still_use_sandbox(tmp_path, monkeypatch, op, kind):
    monkeypatch.setattr(ops_client.sys, "platform", "darwin")
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


def test_remote_render_remains_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_client.sys, "platform", "darwin")
    monkeypatch.setenv("AAD_REMOTE", "1")
    monkeypatch.setattr(sandbox, "popen", lambda *_a, **_k: pytest.fail("remote render must be refused"))
    result = ops_client._run_sandboxed(
        {"op": "render", "kind": "part", "out_dir": str(tmp_path), "args": {}},
        30.0,
    )
    assert result["ok"] is False
    assert result["failure"]["code"] == "SANDBOX_DENIED"
