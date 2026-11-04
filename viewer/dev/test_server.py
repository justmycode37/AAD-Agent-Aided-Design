"""Stand-in MCP server for testing the viewer on real hosts until the platform adapter is ready.

Serves ui://aiad/viewer, aiad_show and the app-only aiad_scene_read over stdio.

Environment:
  AIAD_DEV_HTML   path to dist/aiad-viewer.html
  AIAD_DEV_SCENE  directory with manifest.json and <scene_id>.gz (see dev/gen_scene.py)
  AIAD_DEV_LOG    optional JSONL log of tool calls and viewer reports
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

HTML = Path(os.environ["AIAD_DEV_HTML"]).read_text()
SCENE_DIR = Path(os.environ["AIAD_DEV_SCENE"])
LOG = os.environ.get("AIAD_DEV_LOG")
MANIFEST = json.loads((SCENE_DIR / "manifest.json").read_text())
BLOB = (SCENE_DIR / f"{MANIFEST['scene_id']}.gz").read_bytes()
URI = "ui://aiad/viewer"
MAX_CHUNK = 128 * 1024


def log(**fields: object) -> None:
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"t": time.time(), **fields}) + "\n")


def text(value: str) -> list[TextContent]:
    return [TextContent(type="text", text=value)]


apps = Apps()
apps.add_html_resource(URI, HTML, name="AIAD viewer")


@apps.tool(
    resource_uri=URI,
    name="aiad_show",
    description="Show the interactive 3D viewer of the current part to the user.",
)
def aiad_show(project: str = "dev", title: str = "") -> CallToolResult:
    log(ev="show", project=project, title=title)
    summary = f"Showing rev {MANIFEST['rev']}: {MANIFEST.get('triangles')} triangles."
    envelope = {
        "ok": True,
        "project": project,
        "rev": MANIFEST["rev"],
        "status": MANIFEST["status"],
        "summary": summary,
        "scene": MANIFEST,
        "viewer": {"mode": "inline"},
    }
    return CallToolResult(content=text(f"{summary} The viewer is displayed to the user."), structured_content=envelope)


@apps.tool(
    resource_uri=URI,
    visibility=["app"],
    name="aiad_scene_read",
    description="Internal viewer tool. Do not call.",
)
def aiad_scene_read(scene_id: str, offset: int, length: int = MAX_CHUNK) -> CallToolResult:
    if scene_id != MANIFEST["scene_id"]:
        return CallToolResult(content=text(f"Unknown scene {scene_id}."), is_error=True)
    part = BLOB[offset : offset + min(length, MAX_CHUNK)]
    log(ev="chunk", offset=offset, n=len(part))
    return CallToolResult(
        content=text(f"{len(part)} bytes"),
        structured_content={
            "data_b64": base64.b64encode(part).decode(),
            "offset": offset,
            "byte_count": len(part),
            "total_bytes": len(BLOB),
            "has_more": offset + len(part) < len(BLOB),
        },
    )


@apps.tool(
    resource_uri=URI,
    visibility=["app"],
    name="aiad_dev_report",
    description="Internal viewer tool. Do not call.",
)
def aiad_dev_report(report: str, image: str = "") -> CallToolResult:
    log(ev="report", report=json.loads(report), image_chars=len(image))
    if image and LOG:
        target = Path(LOG).with_suffix(f".{int(time.time() * 1000)}.jpg")
        target.write_bytes(base64.b64decode(image.split(",", 1)[-1]))
    return CallToolResult(content=text("ok"))


mcp = MCPServer("aiad-dev", extensions=[apps])

if __name__ == "__main__":
    log(ev="start", pid=os.getpid())
    mcp.run("stdio")
