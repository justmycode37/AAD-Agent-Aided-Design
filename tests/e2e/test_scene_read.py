"""aiad_scene_read returns the part's real mesh, not just bytes that happen to hash correctly."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from typing import Any

import pytest

WAIT_S = 200
BRACKET = """
from build123d import Align, Box, BuildPart, Cylinder, Mode
with BuildPart() as bp:
    Box(60, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    Cylinder(radius=5, height=6, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
result = bp.part
"""


def call(mcp: Any, tool: str, /, **arguments: Any) -> dict[str, Any]:
    return mcp.request("tools/call", {"name": tool, "arguments": arguments})


def read_all(mcp: Any, manifest: dict[str, Any]) -> tuple[bytes, list[int]]:
    data, sizes = b"", []
    while True:
        chunk = call(mcp, "aiad_scene_read", scene_id=manifest["scene_id"], offset=len(data))["structuredContent"]
        assert chunk["total_bytes"] == manifest["total_bytes"]
        data += base64.b64decode(chunk["data_b64"])
        sizes.append(chunk["byte_count"])
        if not chunk["has_more"]:
            return data, sizes


def typed(buffer: dict[str, Any]) -> list[float]:
    import struct

    code = {"float32": "f", "int32": "i"}[buffer["dtype"]]
    raw = base64.b64decode(buffer["buffer"])
    return list(struct.unpack(f"<{len(raw) // struct.calcsize(code)}{code}", raw))


@pytest.mark.usefixtures("engine")
def test_scene_read_returns_the_built_geometry(mcp_process: Any) -> None:
    mcp_process.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "aiad-e2e", "version": "1"}})
    mcp_process.send({"method": "notifications/initialized"})
    call(mcp_process, "aiad_new", name="scene")
    built = call(mcp_process, "aiad_build", project="scene", source=BRACKET, wait_s=WAIT_S)["structuredContent"]
    assert built["ok"], built
    assert not [c for c in built.get("checks", []) if "scene" in c["id"] and c["status"] != "pass"], built["checks"]

    shown = call(mcp_process, "aiad_show", project="scene")["structuredContent"]
    manifest = shown["scene"]
    assert manifest["format"] == "tcv-5" and manifest["bbox"] == pytest.approx([-30, -20, 0, 30, 20, 6], abs=1e-6)
    assert manifest["project"] == "scene" and manifest["rev"] == 1 and manifest["triangles"] > 12

    payload, sizes = read_all(mcp_process, manifest)
    assert len(payload) == manifest["total_bytes"]
    assert hashlib.sha256(payload).hexdigest() == manifest["scene_id"]
    assert all(size <= 128 * 1024 for size in sizes)
    scene = json.loads(gzip.decompress(payload))

    leaf = scene["shapes"]["parts"][0]
    mesh = scene["instances"][leaf["shape"]["ref"]]
    vertices = typed(mesh["vertices"])
    triangles = typed(mesh["triangles"])
    xs, ys, zs = vertices[0::3], vertices[1::3], vertices[2::3]
    assert [min(xs), max(xs)] == pytest.approx([-30, 30], abs=1e-3)
    assert [min(ys), max(ys)] == pytest.approx([-20, 20], abs=1e-3)
    assert [min(zs), max(zs)] == pytest.approx([0, 6], abs=1e-3)
    assert len(triangles) // 3 == manifest["triangles"]
    assert 0 <= min(triangles) and max(triangles) < len(vertices) // 3

    # The bore is in the mesh: vertices on its wall sit 5 mm from the axis, and none inside it.
    radii = [(x * x + y * y) ** 0.5 for x, y in zip(xs, ys, strict=True)]
    assert min(radii) == pytest.approx(5, abs=1e-3)

    features = scene["shapes"]["feature_map"][manifest["parts"][0]["path"]]
    assert len(features["faces"]) == len(typed(mesh["triangles_per_face"]))
    assert len(features["edges"]) == len(typed(mesh["segments_per_edge"]))
