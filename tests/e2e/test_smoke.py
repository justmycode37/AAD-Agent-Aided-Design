"""The walking skeleton: build a real part, show it, fetch the mesh by chunks."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

WAIT_S = 200  # A cold kernel import can take a minute before aiad install pre-warms it.
LEGACY_VERSION = "2025-11-25"
MODERN_VERSION = "2026-07-28"
APP_ONLY = {"aiad_scene_read", "aiad_set_param", "aiad_pick_info", "aiad_file_read"}
MODEL_VISIBLE = {
    "aiad_new", "aiad_build", "aiad_inspect", "aiad_query", "aiad_measure", "aiad_check", "aiad_render",
    "aiad_show", "aiad_drawing", "aiad_history", "aiad_import", "aiad_export", "aiad_library",
}  # fmt: skip
MODEL = """
from build123d import Box, BuildPart, Cylinder, Mode
with BuildPart() as bp:
    Box(60, 40, 6)
    Cylinder(radius=5, height=6, mode=Mode.SUBTRACT)
result = bp.part
"""


def initialize(mcp: Any) -> dict[str, Any]:
    result = mcp.request(
        "initialize",
        {
            "protocolVersion": LEGACY_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "aiad-e2e", "version": "1"},
        },
    )
    mcp.send({"method": "notifications/initialized"})
    return result


def call(mcp: Any, tool: str, /, **arguments: Any) -> dict[str, Any]:
    return mcp.request("tools/call", {"name": tool, "arguments": arguments})


def read_scene(mcp: Any, manifest: dict[str, Any]) -> bytes:
    data = b""
    while True:
        chunk = call(mcp, "aiad_scene_read", scene_id=manifest["scene_id"], offset=len(data))
        assert not chunk.get("isError"), chunk
        part = chunk["structuredContent"]
        assert part["offset"] == len(data)
        assert part["total_bytes"] == manifest["total_bytes"]
        data += base64.b64decode(part["data_b64"])
        if not part["has_more"]:
            return data


def test_tools_and_viewer_are_always_declared(mcp_process: Any) -> None:
    started = time.monotonic()
    result = initialize(mcp_process)
    first_response_s = time.monotonic() - started
    assert first_response_s < 5, f"handshake took {first_response_s:.2f} s"
    assert result["serverInfo"]["name"] == "aiad"

    tools = {tool["name"]: tool for tool in mcp_process.request("tools/list")["tools"]}
    assert set(tools) == MODEL_VISIBLE | APP_ONLY
    assert tools["aiad_show"]["_meta"]["ui"]["resourceUri"] == "ui://aiad/viewer"
    assert sum("ui" in tool.get("_meta", {}) and "resourceUri" in tool["_meta"]["ui"] for tool in tools.values()) == 1
    for name in APP_ONLY:
        assert tools[name]["_meta"]["ui"]["visibility"] == ["app"]
        assert tools[name]["description"].startswith("Internal viewer tool. Do not call.")

    resource = mcp_process.request("resources/read", {"uri": "ui://aiad/viewer"})["contents"][0]
    assert resource["mimeType"] == "text/html;profile=mcp-app"
    assert resource["text"].lstrip().lower().startswith("<!doctype html")


@pytest.mark.usefixtures("engine")
def test_build_show_and_chunked_scene(mcp_process: Any) -> None:
    initialize(mcp_process)
    created = call(mcp_process, "aiad_new", name="bracket")
    assert created["structuredContent"]["ok"], created

    built = call(mcp_process, "aiad_build", project="bracket", source=MODEL, wait_s=WAIT_S)
    envelope = built["structuredContent"]
    assert envelope["ok"], built
    assert envelope["rev"] == 1 and envelope["head_rev"] == 1
    assert envelope["status"] == "draft"  # No requirements, so it must not claim verified.
    assert envelope["metrics"]["valid"] is True
    assert envelope["metrics"]["bbox"] == pytest.approx([60, 40, 6], abs=1e-3)
    assert "base64" not in json.dumps(envelope)

    shown = call(mcp_process, "aiad_show", project="bracket")
    assert shown["structuredContent"]["ok"], shown
    manifest = shown["structuredContent"]["scene"]
    assert manifest["format"] == "tcv-5" and manifest["encoding"] == "gzip"
    assert len(json.dumps(shown["structuredContent"])) < 8192  # No mesh in the envelope.

    payload = read_scene(mcp_process, manifest)
    assert len(payload) == manifest["total_bytes"]
    assert hashlib.sha256(payload).hexdigest() == manifest["scene_id"]
    assert json.loads(gzip.decompress(payload))


@pytest.mark.usefixtures("engine")
def test_failed_build_keeps_head(mcp_process: Any) -> None:
    initialize(mcp_process)
    call(mcp_process, "aiad_new", name="edits")
    call(mcp_process, "aiad_build", project="edits", source=MODEL, wait_s=WAIT_S)

    broken = call(mcp_process, "aiad_build", project="edits", source="raise ValueError('boom')\n", wait_s=WAIT_S)
    assert broken["isError"] is True
    envelope = broken["structuredContent"]
    assert envelope["status"] == "failed"
    assert envelope["head_rev"] == 1
    assert envelope["failure"]["code"] == "RUNTIME_ERROR"
    assert envelope["failure"]["last_good_rev"] == 1

    conflict = call(mcp_process, "aiad_build", project="edits", base_rev=99, source=MODEL)
    assert conflict["structuredContent"]["failure"]["code"] == "BASE_REV_CONFLICT"

    duplicate = "requirements:\n  - id: size\n    bbox: {x: 10}\n    bbox: {x: 5}\n"
    rejected = call(mcp_process, "aiad_build", project="edits", source=MODEL, spec=duplicate, wait_s=WAIT_S)
    assert rejected["structuredContent"]["failure"]["code"] == "BAD_INPUT"
    assert "duplicate YAML key" in rejected["structuredContent"]["failure"]["message"]
    assert rejected["structuredContent"]["head_rev"] == 1


def test_stateless_protocol_envelope(mcp_process: Any) -> None:
    meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "aiad-e2e-modern", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    tools = mcp_process.request("tools/list", {"_meta": meta})["tools"]
    assert {tool["name"] for tool in tools} == MODEL_VISIBLE | APP_ONLY

    created = mcp_process.request(
        "tools/call", {"name": "aiad_new", "arguments": {"name": "modern"}, "_meta": meta}
    )
    assert created["structuredContent"]["ok"], created


@pytest.mark.usefixtures("engine")
def test_host_without_apps_gets_a_window_that_serves_the_scene(mcp_process: Any) -> None:
    initialize(mcp_process)  # This client advertises no MCP Apps support.
    call(mcp_process, "aiad_new", name="windowed")
    call(mcp_process, "aiad_build", project="windowed", source=MODEL, wait_s=WAIT_S)

    shown = call(mcp_process, "aiad_show", project="windowed")["structuredContent"]
    assert shown["viewer"]["mode"] == "window"
    url = shown["viewer"]["url"]
    assert url.startswith("http://127.0.0.1:")

    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.status == 200
        page = response.read().decode()
    assert "window.__AIAD_WINDOW__" in page

    token = url.split("token=")[1]
    request = urllib.request.Request(
        url.split("/p/")[0] + "/rpc",
        data=json.dumps({"method": "aiad_show", "params": {"project": "windowed"}}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    assert result["structuredContent"]["scene"]["scene_id"] == shown["scene"]["scene_id"]

    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(url.split("?")[0], timeout=10)
    assert denied.value.code == 401


@pytest.mark.usefixtures("engine")
def test_edit_export_and_history(mcp_process: Any) -> None:
    initialize(mcp_process)
    call(mcp_process, "aiad_new", name="edits2")
    call(mcp_process, "aiad_build", project="edits2", source=MODEL, wait_s=WAIT_S)

    patch = "@@ -4 +4 @@\n-    Box(60, 40, 6)\n+    Box(80, 40, 6)\n"
    patched = call(mcp_process, "aiad_build", project="edits2", base_rev=1, patch=patch, wait_s=WAIT_S)
    assert patched["structuredContent"]["rev"] == 2, patched
    assert patched["structuredContent"]["metrics"]["bbox"][0] == pytest.approx(80, abs=1e-3)

    stale = call(mcp_process, "aiad_build", project="edits2", patch="@@ -3 +3 @@\n-nope\n+x\n", wait_s=WAIT_S)
    assert stale["structuredContent"]["failure"]["code"] == "BAD_INPUT"
    assert stale["structuredContent"]["head_rev"] == 2

    refused = call(mcp_process, "aiad_export", project="edits2", formats=["step"])
    assert refused["structuredContent"]["failure"]["code"] == "NOT_VERIFIED"

    exported = call(mcp_process, "aiad_export", project="edits2", formats=["step", "stl", "glb", "3mf"], allow_unverified=True)
    files = exported["structuredContent"]["artifacts"]
    assert exported["structuredContent"]["ok"], exported
    assert set(files) == {"step", "stl", "glb", "3mf"}
    assert all("UNVERIFIED" in path for path in files.values())
    assert open(files["glb"], "rb").read(4) == b"glTF"
    assert b"ISO-10303" in open(files["step"], "rb").read(32)

    diff = call(mcp_process, "aiad_history", project="edits2", action="diff", rev=2)["structuredContent"]
    assert "Box(80, 40, 6)" in diff["diff"]["source"]
    assert diff["diff"]["metrics"]["bbox"]["to"][0] == pytest.approx(80, abs=1e-3)

    checkout = call(mcp_process, "aiad_history", project="edits2", action="checkout", rev=1)["structuredContent"]
    assert checkout["head_rev"] == 1
    listing = call(mcp_process, "aiad_history", project="edits2")["structuredContent"]
    assert listing["head_rev"] == 1 and "2 revision" in listing["summary"]

    topology = call(mcp_process, "aiad_inspect", project="edits2", what="topology")["structuredContent"]
    assert topology["topology"]["faces"] == 7 and topology["topology"]["solids"] == 1
    checks = call(mcp_process, "aiad_check", project="edits2", suites=["hard"])["structuredContent"]
    assert checks["checks_summary"]["fail"] == 0


@pytest.mark.usefixtures("engine")
def test_unbuilt_sector_tools_say_so(mcp_process: Any) -> None:
    initialize(mcp_process)
    call(mcp_process, "aiad_new", name="honest")
    call(mcp_process, "aiad_build", project="honest", source=MODEL, wait_s=WAIT_S)
    measured = call(mcp_process, "aiad_measure", project="honest", kind="volume")["structuredContent"]
    if not measured["ok"]:
        assert "Not implemented" in measured["summary"]


@pytest.mark.usefixtures("engine")
def test_render_drawing_and_check_run_in_workers(mcp_process: Any) -> None:
    pytest.importorskip("vtk")
    initialize(mcp_process)
    call(mcp_process, "aiad_new", name="ops")
    call(mcp_process, "aiad_build", project="ops", source=MODEL, wait_s=WAIT_S)

    rendered = call(mcp_process, "aiad_render", project="ops", views=["iso"], size=256)
    assert rendered["structuredContent"]["ok"], rendered
    images = [block for block in rendered["content"] if block["type"] == "image"]
    assert images and base64.b64decode(images[0]["data"]).startswith(b"\x89PNG")

    drawn = call(mcp_process, "aiad_drawing", project="ops", formats=["svg"])
    assert drawn["structuredContent"]["ok"], drawn
    assert drawn["structuredContent"]["artifacts"]["svg"].endswith(".svg")

    checked = call(mcp_process, "aiad_check", project="ops", suites=["hard"])["structuredContent"]
    assert checked["checks_summary"]["fail"] == 0
