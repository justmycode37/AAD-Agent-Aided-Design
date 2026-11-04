"""`aiad mcp`: thin stdio MCP adapter. Forwards every tool to the daemon, never imports the kernel."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

from . import __version__, paths, viewer
from .rpc import PROTOCOL, RpcClient, RpcError
from .tools import INSTRUCTIONS, TOOL_NAMES, TOOLS, UI_EXTENSION, VIEWER_MIME, VIEWER_URI

log = logging.getLogger("aiad.adapter")
DAEMON_START_TIMEOUT_S = 15.0


def spawn_daemon() -> None:
    paths.ensure_private_dir(paths.aiad_home())
    paths.ensure_private_dir(paths.run_dir())
    paths.ensure_private_dir(paths.logs_dir())
    with open(paths.logs_dir() / "aiadd.out", "ab") as out:
        subprocess.Popen(
            [sys.executable, "-m", "aiad_platform.daemon"],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            start_new_session=True,
            close_fds=True,
        )


class DaemonLink:
    """The adapter's connection to aiadd. Starts it when needed, restarts it on a version mismatch."""

    def __init__(self) -> None:
        self._client: RpcClient | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> RpcClient:
        async with self._lock:
            if self._client is None or self._client.closed:
                self._client = await self._connect()
            return self._client

    async def _connect(self) -> RpcClient:
        spawned = False
        deadline = time.monotonic() + DAEMON_START_TIMEOUT_S
        while True:
            try:
                client = await RpcClient.connect(paths.socket_path())
            except (ConnectionError, FileNotFoundError, OSError):
                if not spawned:
                    spawn_daemon()
                    spawned = True
                if time.monotonic() > deadline:
                    raise ConnectionError(
                        f"aiadd did not start within {DAEMON_START_TIMEOUT_S:.0f} s. "
                        f"See {paths.logs_dir() / 'aiadd.log'} or run aiad doctor."
                    ) from None
                await asyncio.sleep(0.05)
                continue
            hello = await client.call("hello", {"adapter_version": __version__, "protocol": PROTOCOL})
            if hello.get("compatible"):
                return client
            log.warning("restarting aiadd %s for adapter %s", hello.get("daemon_version"), __version__)
            await client.call("shutdown")
            await client.close()
            spawned = False
            await asyncio.sleep(0.2)

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        try:
            return await (await self.client()).call(method, params)
        except ConnectionError:
            # The daemon died or idled out between calls. Start a new one and retry once.
            return await (await self.client()).call(method, params)


def _client_info(ctx: ServerRequestContext[Any]) -> dict[str, Any]:
    params = ctx.session.client_params
    capabilities = ctx.session.client_capabilities
    extensions = getattr(capabilities, "extensions", None) or {}
    info: dict[str, Any] = {"supports_ui": UI_EXTENSION in extensions}
    if params is not None:
        info["name"] = params.client_info.name
        info["version"] = params.client_info.version
    return info


async def _roots(ctx: ServerRequestContext[Any], cache: dict[str, str | None]) -> str | None:
    """The first file:// root, when the host offers roots. Asked once per connection."""
    if "root" in cache:
        return cache["root"]
    root = None
    capabilities = ctx.session.client_capabilities
    if capabilities is not None and capabilities.roots is not None:
        try:
            result = await asyncio.wait_for(ctx.session.list_roots(), 2.0)
        except Exception as exc:  # Roots are optional; ~/AIAD is the documented fallback.
            log.info("roots/list failed: %s", exc)
        else:
            for entry in result.roots:
                parsed = urlparse(str(entry.uri))
                if parsed.scheme == "file" and Path(unquote(parsed.path)).is_dir():
                    root = unquote(parsed.path)
                    break
    cache["root"] = root
    return root


def _result(reply: dict[str, Any]) -> types.CallToolResult:
    content: list[Any] = [types.TextContent(type="text", text=reply.get("text", ""))]
    for image in reply.get("images") or []:
        data = base64.b64encode(Path(image["path"]).read_bytes()).decode("ascii")
        content.append(types.ImageContent(type="image", data=data, mime_type=image.get("mime", "image/png")))
    structured = reply.get("structured")
    is_error = isinstance(structured, dict) and structured.get("ok") is False
    return types.CallToolResult(content=content, structured_content=structured, is_error=is_error)


def _error_result(tool: str, message: str) -> types.CallToolResult:
    envelope = {
        "ok": False,
        "project": "",
        "status": "failed",
        "summary": f"{tool} failed: {message}",
        "failure": {"code": "WORKER_CRASH", "phase": "crash", "message": message},
    }
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=envelope["summary"])],
        structured_content=envelope,
        is_error=True,
    )


def build_server() -> Server[Any]:
    link = DaemonLink()
    roots: dict[str, str | None] = {}
    tools = [types.Tool.model_validate(tool) for tool in TOOLS]

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> types.CallToolResult:
        if params.name not in TOOL_NAMES:
            return _error_result(params.name, "unknown tool")
        client = _client_info(ctx)
        if root := await _roots(ctx, roots):
            client["root"] = root
        try:
            reply = await link.call(params.name, {**(params.arguments or {}), "client": client})
        except (ConnectionError, RpcError, OSError) as exc:
            return _error_result(params.name, str(exc))
        return _result(reply)

    async def list_resources(ctx: Any, params: Any) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri=VIEWER_URI,
                    name="AIAD viewer",
                    description="Interactive 3D and 2D viewer for aiad_show.",
                    mime_type=VIEWER_MIME,
                )
            ]
        )

    async def list_templates(ctx: Any, params: Any) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(resource_templates=[])

    async def read_resource(ctx: Any, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        if str(params.uri) != VIEWER_URI:
            raise ValueError(f"Unknown resource {params.uri}")
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=VIEWER_URI, mime_type=VIEWER_MIME, text=viewer.load_html())]
        )

    server: Server[Any] = Server(
        "aiad",
        version=__version__,
        title="AIAD",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_list_resource_templates=list_templates,
        on_read_resource=read_resource,
    )
    server.extensions[UI_EXTENSION] = {"mimeTypes": [VIEWER_MIME]}
    return server


async def serve_stdio() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> int:
    if os.environ.get("AIAD_ADAPTER_DEBUG"):
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
    asyncio.run(serve_stdio())
    return 0
