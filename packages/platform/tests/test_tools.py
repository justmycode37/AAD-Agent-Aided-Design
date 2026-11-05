from aad_platform.tools import TOOL_NAMES, TOOLS, VIEWER_URI

import pytest


def test_thirteen_model_visible_and_four_app_only_tools():
    app_only = {t["name"] for t in TOOLS if t.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]}
    assert app_only == {"aad_scene_read", "aad_set_param", "aad_pick_info", "aad_file_read"}
    assert len(TOOL_NAMES) == 17


def test_only_show_has_a_ui_resource():
    with_ui = [t["name"] for t in TOOLS if "resourceUri" in t.get("_meta", {}).get("ui", {})]
    assert with_ui == ["aad_show"]
    assert next(t for t in TOOLS if t["name"] == "aad_show")["_meta"]["ui"]["resourceUri"] == VIEWER_URI


def test_aad_show_schema_accepts_materials_and_studio_at_both_levels():
    from jsonschema import Draft202012Validator

    tool = next(t for t in TOOLS if t["name"] == "aad_show")
    payload = {
        "project": "gearbox",
        "presentation": {"environment": "studio", "materials": [
            {"parts": ["/Group/case"], "color": "#aabbcc", "metalness": 0.8, "roughness": 0.25, "opacity": 0.9},
        ]},
        "views": [{"id": "finish", "label": "Finish", "presentation": {
            "environment": "studio", "materials": [{"parts": ["/Group/gear"], "roughness": 0.4}],
        }}],
    }
    Draft202012Validator.check_schema(tool["inputSchema"])
    Draft202012Validator(tool["inputSchema"]).validate(payload)


def test_app_only_descriptions_warn_the_model():
    for tool in TOOLS:
        if tool.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]:
            assert tool["description"].startswith("Internal viewer tool. Do not call.")


def test_builds_are_not_marked_read_only():
    annotations = {t["name"]: t["annotations"] for t in TOOLS}
    assert annotations["aad_build"]["readOnlyHint"] is False
    assert annotations["aad_scene_read"]["readOnlyHint"] is True


@pytest.mark.parametrize("resource_delay_s", [0.0, 0.05])
def test_stray_stdout_cannot_corrupt_the_mcp_stream(tmp_path, resource_delay_s):
    """Issue #11: native code writing to fd 1 must not land in the protocol stream."""
    import json
    import asyncio
    import os
    import sys

    script = f"""
import asyncio, os, time
from aad_platform import adapter, viewer
real = viewer.load_html
def noisy():
    os.write(1, b"native library noise\\n")
    print("python noise")
    time.sleep({resource_delay_s})
    return real()
viewer.load_html = noisy
asyncio.run(adapter.serve_stdio())
"""
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "ui://aad/viewer"}},
    ]
    async def exchange():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
            env={**os.environ, "HOME": str(tmp_path), "AAD_HOME": str(tmp_path)},
        )
        messages = []

        async def send(request):
            process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            await process.stdin.drain()

        async def receive(request_id):
            while True:
                line = await process.stdout.readline()
                assert line, f"adapter exited before response {request_id}: {messages!r}"
                message = json.loads(line)  # Any protocol noise fails the parse.
                messages.append(message)
                if message.get("id") == request_id:
                    return message

        try:
            async with asyncio.timeout(60):
                await send(requests[0])
                initialized = await receive(1)
                assert "result" in initialized, initialized
                await send(requests[1])
                await send(requests[2])
                await receive(2)
                # Keep the connection open until the handler has replied;
                # EOF can cancel outstanding server response tasks.
                process.stdin.close()
                remaining, stderr = await process.communicate()
                messages.extend(json.loads(line) for line in remaining.splitlines() if line.strip())
                assert process.returncode == 0, stderr[-500:]
                return messages, stderr.decode("utf-8")
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()

    messages, stderr = asyncio.run(exchange())
    assert any(m.get("id") == 2 and "contents" in m.get("result", {}) for m in messages), (messages, stderr[-500:])
    assert "native library noise" in stderr
    assert "python noise" in stderr
