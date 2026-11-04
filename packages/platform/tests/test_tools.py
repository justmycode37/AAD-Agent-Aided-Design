from aiad_platform.tools import TOOL_NAMES, TOOLS, VIEWER_URI


def test_thirteen_model_visible_and_four_app_only_tools():
    app_only = {t["name"] for t in TOOLS if t.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]}
    assert app_only == {"aiad_scene_read", "aiad_set_param", "aiad_pick_info", "aiad_file_read"}
    assert len(TOOL_NAMES) == 17


def test_only_show_has_a_ui_resource():
    with_ui = [t["name"] for t in TOOLS if "resourceUri" in t.get("_meta", {}).get("ui", {})]
    assert with_ui == ["aiad_show"]
    assert next(t for t in TOOLS if t["name"] == "aiad_show")["_meta"]["ui"]["resourceUri"] == VIEWER_URI


def test_app_only_descriptions_warn_the_model():
    for tool in TOOLS:
        if tool.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]:
            assert tool["description"].startswith("Internal viewer tool. Do not call.")


def test_builds_are_not_marked_read_only():
    annotations = {t["name"]: t["annotations"] for t in TOOLS}
    assert annotations["aiad_build"]["readOnlyHint"] is False
    assert annotations["aiad_scene_read"]["readOnlyHint"] is True


def test_stray_stdout_cannot_corrupt_the_mcp_stream(tmp_path):
    """Issue #11: native code writing to fd 1 must not land in the protocol stream."""
    import json
    import os
    import subprocess
    import sys

    script = """
import asyncio, os
from aiad_platform import adapter, viewer
real = viewer.load_html
def noisy():
    os.write(1, b"native library noise\\n")
    print("python noise")
    return real()
viewer.load_html = noisy
asyncio.run(adapter.serve_stdio())
"""
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "ui://aiad/viewer"}},
    ]
    done = subprocess.run(
        [sys.executable, "-c", script],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "HOME": str(tmp_path), "AIAD_HOME": str(tmp_path)},
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    messages = [json.loads(line) for line in lines]  # Any noise here fails the parse.
    assert any(m.get("id") == 2 and "contents" in m.get("result", {}) for m in messages), done.stderr[-500:]
    assert "native library noise" in done.stderr
