"""`aad mcp`: thin stdio MCP adapter. Forwards every tool to the daemon, never imports the kernel."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

from . import __version__, paths, viewer
from .daemon_ctl import spawn_daemon
from .image_payload import read_render_image
from .rpc import INVALID_PARAMS, PROTOCOL, RpcClient, RpcError
from .tools import INSTRUCTIONS, TOOL_NAMES, TOOLS, UI_EXTENSION, VIEWER_MIME, VIEWER_URI

log = logging.getLogger("aad.adapter")
DAEMON_START_TIMEOUT_S = 15.0
REPLAYABLE = frozenset(
    {"aad_inspect", "aad_query", "aad_measure", "aad_library", "aad_scene_read", "aad_pick_info", "aad_file_read"}
)


class DaemonLink:
    """The adapter's connection to aadd. Starts it when needed, restarts it on a version mismatch."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self._client: RpcClient | None = None
        self._lock = asyncio.Lock()
        self._env = env  # A separate daemon's environment. Remote mode runs one per user.

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
                home = Path(self._env["AAD_HOME"]) if self._env else None
                client = await RpcClient.connect(paths.socket_path(home))
            except (ConnectionError, FileNotFoundError, OSError):
                if not spawned:
                    spawn_daemon(self._env)
                    spawned = True
                if time.monotonic() > deadline:
                    raise ConnectionError(
                        f"aadd did not start within {DAEMON_START_TIMEOUT_S:.0f} s. "
                        f"See {paths.logs_dir() / 'aadd.log'} or run aad doctor."
                    ) from None
                await asyncio.sleep(0.05)
                continue
            remaining = max(deadline - time.monotonic(), 1.0)
            try:
                hello = await asyncio.wait_for(
                    client.call("hello", {"adapter_version": __version__, "protocol": PROTOCOL}), remaining
                )
            except (ConnectionError, TimeoutError) as exc:
                await client.close()
                raise ConnectionError(f"aadd did not answer hello: {exc}") from exc
            if hello.get("compatible"):
                return client
            log.warning("restarting aadd %s for adapter %s", hello.get("daemon_version"), __version__)
            await client.call("shutdown")
            await client.close()
            spawned = False
            await asyncio.sleep(0.2)

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        client = await self.client()
        try:
            return await client.call(method, params)
        except ConnectionError:
            # The daemon may have acted on a mutating call before it died, so only reads replay.
            if method not in REPLAYABLE:
                raise ConnectionError(
                    f"The connection to aadd was lost during {method}, so it may or may not have run. "
                    "Check aad_history before you retry."
                ) from None
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
        except Exception as exc:  # Roots are optional; ~/AAD is the documented fallback.
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
        data = base64.b64encode(read_render_image(image["path"])).decode("ascii")
        content.append(types.ImageContent(type="image", data=data, mime_type=image.get("mime", "image/png")))
    structured = reply.get("structured")
    is_error = isinstance(structured, dict) and structured.get("ok") is False
    return types.CallToolResult(content=content, structured_content=structured, is_error=is_error)


def _error_result(tool: str, message: str, *, bad_input: bool = False) -> types.CallToolResult:
    failure = {"code": "BAD_INPUT", "phase": "input"} if bad_input else {"code": "WORKER_CRASH", "phase": "crash"}
    envelope = {
        "ok": False,
        "project": "",
        "status": "failed",
        "summary": f"{tool} failed: {message}",
        "failure": {**failure, "message": message},
    }
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=envelope["summary"])],
        structured_content=envelope,
        is_error=True,
    )


def build_server(
    link_for: Callable[[], DaemonLink] | None = None, *, use_roots: bool = True
) -> Server[Any]:
    default_link = DaemonLink() if link_for is None else None
    roots: dict[str, str | None] = {}
    tools = [types.Tool.model_validate(tool) for tool in TOOLS]

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> types.CallToolResult:
        if params.name not in TOOL_NAMES:
            return _error_result(params.name, "unknown tool")
        client = _client_info(ctx)
        # A remote client's roots name directories on its own machine, so they are never asked for.
        if use_roots and (root := await _roots(ctx, roots)):
            client["root"] = root
        try:
            link = default_link if link_for is None else link_for()
            reply = await link.call(params.name, {**(params.arguments or {}), "client": client})
        except RpcError as exc:
            return _error_result(params.name, str(exc), bad_input=exc.code == INVALID_PARAMS)
        except (ConnectionError, OSError) as exc:
            return _error_result(params.name, str(exc))
        return _result(reply)

    async def list_resources(ctx: Any, params: Any) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri=VIEWER_URI,
                    name="AAD viewer",
                    description="Interactive 3D and 2D viewer for aad_show.",
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
        "aad",
        version=__version__,
        title="AAD",
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
        # The SDK moves fd 1 to stderr while serving, then restores it. A buffered print() would
        # otherwise flush onto the wire at exit, so Python-level output goes to stderr too.
        sys.stdout = sys.stderr
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> int:
    if os.environ.get("AAD_ADAPTER_DEBUG"):
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
    asyncio.run(serve_stdio())
    return 0
