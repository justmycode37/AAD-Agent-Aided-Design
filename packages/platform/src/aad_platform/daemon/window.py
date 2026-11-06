"""Loopback HTTP and WebSocket transport for hosts without inline viewer support."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import secrets
import socket
import webbrowser
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from ..image_payload import read_render_image

_PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ALLOWED_RPC_METHODS = frozenset(
    {
        "aad_show",
        "aad_scene_read",
        "aad_set_param",
        "aad_pick_info",
        "aad_file_read",
    }
)
_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_SELECTION_BYTES = 256 * 1024
_BROADCAST_TIMEOUT_S = 0.5


class WindowServer:
    """Serve a local viewer and its app-only RPC and context routes."""

    def __init__(
        self,
        token: str,
        dispatch: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
        viewer_html: Callable[[], str],
    ) -> None:
        self._token = token
        self._dispatch = dispatch
        self._viewer_html = viewer_html
        self._bound_port: int | None = None
        self._listener: socket.socket | None = None
        self._uvicorn: Any = None
        self._server_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._opened_projects: set[str] = set()
        self._selections: dict[str, list[dict[str, Any]]] = {}
        self._viewers: dict[str, set[Any]] = {}

    @property
    def port(self) -> int | None:
        task = self._server_task
        if self._bound_port is None or task is None or task.done():
            return None
        return self._bound_port

    @property
    def connection_count(self) -> int:
        return sum(len(sockets) for sockets in self._viewers.values())

    async def start(self) -> int:
        """Bind a loopback socket and run Uvicorn in the current event loop."""
        if self.port is not None:
            return self.port

        # Keep the daemon's common import path independent of the HTTP stack.
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route, WebSocketRoute
        from starlette.websockets import WebSocket

        async def get_page(request: Request) -> Response:
            if not self._valid_token(request.query_params.get("token")):
                return Response(status_code=401)
            if not self._valid_host(request.headers.get("host")):
                return Response(status_code=403)
            if not self._valid_origin(request.headers.get("origin")):
                return Response(status_code=403)

            project = request.path_params["project"]
            if not self._valid_project(project):
                return Response(status_code=404)

            html = self._inject_window_config(self._viewer_html(), project)
            port = self._bound_port
            assert port is not None
            headers = {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'none'; "
                    "script-src 'unsafe-inline'; "
                    "style-src 'unsafe-inline'; "
                    f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; "
                    "img-src blob: data:"
                ),
            }
            return Response(content=html, media_type="text/html; charset=utf-8", headers=headers)

        async def post_rpc(request: Request) -> Response:
            if not self._valid_bearer(request.headers.get("authorization")):
                return Response(status_code=401)
            if not self._valid_host(request.headers.get("host")):
                return Response(status_code=403)
            if not self._valid_origin(request.headers.get("origin")):
                return Response(status_code=403)
            if not self._is_json(request.headers.get("content-type")):
                return Response(status_code=415)

            body = await self._read_limited(request, _MAX_REQUEST_BYTES)
            if body is None:
                return Response(status_code=413)
            payload = self._parse_json_object(body)
            if payload is None:
                return JSONResponse({"error": "invalid JSON object"}, status_code=400)

            method = payload.get("method")
            if not isinstance(method, str) or method not in _ALLOWED_RPC_METHODS:
                return Response(status_code=403)
            params = payload.get("params", {})
            if not isinstance(params, dict):
                return JSONResponse({"error": "params must be an object"}, status_code=400)

            try:
                reply = await self._dispatch(method, params)
            except Exception as exc:
                reply = self._dispatch_error(method, params, exc)
            return JSONResponse(self._tool_result(reply))

        async def post_context(request: Request) -> Response:
            if not self._valid_bearer(request.headers.get("authorization")):
                return Response(status_code=401)
            if not self._valid_host(request.headers.get("host")):
                return Response(status_code=403)
            if not self._valid_origin(request.headers.get("origin")):
                return Response(status_code=403)
            if not self._is_json(request.headers.get("content-type")):
                return Response(status_code=415)

            body = await self._read_limited(request, _MAX_REQUEST_BYTES)
            if body is None:
                return Response(status_code=413)
            payload = self._parse_json_object(body)
            if payload is None:
                return JSONResponse({"error": "invalid JSON object"}, status_code=400)

            project = payload.get("project")
            if not isinstance(project, str) or not self._valid_project(project):
                return Response(status_code=404)
            selection = payload.get("selection")
            if not isinstance(selection, list) or any(not isinstance(item, dict) for item in selection):
                return JSONResponse({"error": "selection must be an array of objects"}, status_code=400)
            try:
                selection_bytes = json.dumps(
                    selection, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeEncodeError):
                return JSONResponse({"error": "invalid selection"}, status_code=400)
            if len(selection_bytes) > _MAX_SELECTION_BYTES:
                return Response(status_code=413)

            self._selections[project] = selection
            return JSONResponse({"ok": True})

        async def websocket_endpoint(websocket: WebSocket) -> None:
            if not self._valid_token(websocket.query_params.get("token")):
                await websocket.send_denial_response(Response(status_code=401))
                return
            if not self._valid_host(websocket.headers.get("host")):
                await websocket.send_denial_response(Response(status_code=403))
                return
            if not self._valid_origin(websocket.headers.get("origin")):
                await websocket.send_denial_response(Response(status_code=403))
                return
            project = websocket.query_params.get("project", "")
            if not self._valid_project(project):
                await websocket.send_denial_response(Response(status_code=404))
                return
            if self._stopping:
                await websocket.send_denial_response(Response(status_code=503))
                return

            await websocket.accept()
            sockets = self._viewers.setdefault(project, set())
            sockets.add(websocket)
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
            finally:
                self._remove_viewer(project, websocket)

        app = Starlette(
            routes=[
                Route("/p/{project}", get_page, methods=["GET"]),
                Route("/rpc", post_rpc, methods=["POST"]),
                Route("/context", post_context, methods=["POST"]),
                WebSocketRoute("/ws", websocket_endpoint),
            ]
        )

        @contextlib.contextmanager
        def no_signal_handlers():
            yield

        class _WindowUvicornServer(uvicorn.Server):
            @contextlib.contextmanager
            def capture_signals(self):  # type: ignore[no-untyped-def]
                with no_signal_handlers():
                    yield

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_task: asyncio.Task[None] | None = None
        server: Any = None
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            listener.setblocking(False)
            self._bound_port = int(listener.getsockname()[1])
            self._listener = listener
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self._bound_port,
                ws="websockets-sansio",
                lifespan="off",
                log_config=None,
                access_log=False,
                timeout_graceful_shutdown=2,
            )
            server = _WindowUvicornServer(config)
            self._uvicorn = server
            self._stopping = False
            server_task = asyncio.create_task(server.serve(sockets=[listener]), name="aad-window-server")
            self._server_task = server_task

            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10.0
            while not server.started:
                if server_task.done():
                    await server_task
                    raise RuntimeError("window server exited before startup")
                if loop.time() >= deadline:
                    raise TimeoutError("window server did not start within 10 seconds")
                await asyncio.sleep(0.005)
            return self._bound_port
        except BaseException:
            if server is not None:
                server.should_exit = True
            if server_task is not None and not server_task.done():
                server_task.cancel()
            if server_task is not None:
                await asyncio.gather(server_task, return_exceptions=True)
            listener.close()
            self._reset_server_state()
            raise

    async def stop(self) -> None:
        """Close viewer sockets and wait for Uvicorn to release its resources."""
        task = self._server_task
        server = self._uvicorn
        if task is None:
            self._reset_server_state()
            return

        self._stopping = True
        close_tasks = [
            asyncio.create_task(self._close_viewer(project, websocket))
            for project, sockets in tuple(self._viewers.items())
            for websocket in tuple(sockets)
        ]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        if server is not None:
            server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except TimeoutError:
            if server is not None:
                server.force_exit = True
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            if self._listener is not None:
                self._listener.close()
            self._reset_server_state()

    def url(self, project: str) -> str:
        port = self.port
        if port is None:
            raise RuntimeError("window server is not running")
        return f"http://127.0.0.1:{port}/p/{quote(project, safe='')}?token={quote(self._token, safe='')}"

    def open_once(self, project: str) -> bool:
        if project in self._opened_projects:
            return False
        self._opened_projects.add(project)
        if os.environ.get("AAD_NO_BROWSER") == "1":
            return False
        return bool(webbrowser.open(self.url(project)))

    async def broadcast(self, project: str, envelope: dict[str, Any]) -> int:
        sockets = tuple(self._viewers.get(project, ()))
        if not sockets:
            return 0
        message = {"type": "result", "envelope": envelope}
        results = await asyncio.gather(
            *(self._send_to_viewer(project, websocket, message) for websocket in sockets),
            return_exceptions=True,
        )
        return sum(result is True for result in results)

    def has_viewers(self, project: str) -> bool:
        return bool(self._viewers.get(project))

    def take_selection(self, project: str) -> list[dict[str, Any]]:
        return self._selections.pop(project, [])

    def _valid_token(self, supplied: str | None) -> bool:
        candidate = supplied if supplied is not None else ""
        return secrets.compare_digest(candidate.encode("utf-8"), self._token.encode("utf-8"))

    def _valid_bearer(self, authorization: str | None) -> bool:
        candidate = ""
        if authorization is not None:
            scheme, separator, credentials = authorization.partition(" ")
            if separator and scheme.casefold() == "bearer" and credentials and not any(
                character.isspace() for character in credentials
            ):
                candidate = credentials
        return secrets.compare_digest(candidate.encode("utf-8"), self._token.encode("utf-8"))

    def _valid_host(self, host: str | None) -> bool:
        if host is None or self._bound_port is None:
            return False
        allowed = {
            f"127.0.0.1:{self._bound_port}",
            f"localhost:{self._bound_port}",
        }
        return host.casefold() in allowed

    def _valid_origin(self, origin: str | None) -> bool:
        if origin is None:
            return True
        if self._bound_port is None:
            return False
        return origin in {
            f"http://127.0.0.1:{self._bound_port}",
            f"http://localhost:{self._bound_port}",
        }

    @staticmethod
    def _valid_project(project: Any) -> bool:
        return isinstance(project, str) and _PROJECT_RE.fullmatch(project) is not None

    @staticmethod
    def _is_json(content_type: str | None) -> bool:
        return content_type is not None and content_type.partition(";")[0].strip().casefold() == "application/json"

    @staticmethod
    async def _read_limited(request: Any, limit: int) -> bytes | None:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            with contextlib.suppress(ValueError):
                if int(content_length) > limit:
                    return None
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > limit:
                return None
            body.extend(chunk)
        return bytes(body)

    @staticmethod
    def _parse_json_object(body: bytes) -> dict[str, Any] | None:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None
        return payload if isinstance(payload, dict) else None

    def _inject_window_config(self, html: str, project: str) -> str:
        config = json.dumps(
            {"project": project, "token": self._token}, ensure_ascii=False, separators=(",", ":")
        ).translate(
            str.maketrans(
                {
                    "<": r"\u003c",
                    ">": r"\u003e",
                    "&": r"\u0026",
                    "\u2028": r"\u2028",
                    "\u2029": r"\u2029",
                }
            )
        )
        script = f"<script>window.__AAD_WINDOW__ = {config};</script>"
        head = re.search(r"<head(?:\s[^>]*)?>", html, flags=re.IGNORECASE)
        if head is None:
            return f"<head>{script}</head>{html}"
        return f"{html[:head.end()]}{script}{html[head.end():]}"

    @staticmethod
    def _tool_result(reply: dict[str, Any]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": reply.get("text", "")}]
        for image in reply.get("images") or []:
            content.append(
                {
                    "type": "image",
                    "data": base64.b64encode(read_render_image(image["path"])).decode("ascii"),
                    "mimeType": image.get("mime", "image/png"),
                }
            )
        structured = reply.get("structured")
        return {
            "content": content,
            "structuredContent": structured,
            "isError": isinstance(structured, dict) and structured.get("ok") is False,
        }

    @staticmethod
    def _dispatch_error(method: str, params: dict[str, Any], error: Exception) -> dict[str, Any]:
        message = str(error)
        project = params.get("project") if isinstance(params.get("project"), str) else ""
        summary = f"{method} failed: {message}"
        return {
            "structured": {
                "ok": False,
                "project": project,
                "status": "failed",
                "summary": summary,
                "failure": {"code": "WORKER_CRASH", "phase": "crash", "message": message},
            },
            "text": summary,
        }

    async def _send_to_viewer(self, project: str, websocket: Any, message: dict[str, Any]) -> bool:
        try:
            await asyncio.wait_for(websocket.send_json(message), timeout=_BROADCAST_TIMEOUT_S)
            return True
        except Exception:
            self._remove_viewer(project, websocket)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(websocket.close(code=1011), timeout=0.1)
            return False

    async def _close_viewer(self, project: str, websocket: Any) -> None:
        try:
            await asyncio.wait_for(websocket.close(code=1001, reason="Server shutting down"), timeout=0.5)
        except Exception:
            pass
        finally:
            self._remove_viewer(project, websocket)

    def _remove_viewer(self, project: str, websocket: Any) -> None:
        sockets = self._viewers.get(project)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            self._viewers.pop(project, None)

    def _reset_server_state(self) -> None:
        self._bound_port = None
        self._listener = None
        self._uvicorn = None
        self._server_task = None
        self._stopping = False
        self._viewers.clear()
