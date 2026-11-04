"""The walking skeleton: build a real part, show it, fetch the mesh by chunks."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import time
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
