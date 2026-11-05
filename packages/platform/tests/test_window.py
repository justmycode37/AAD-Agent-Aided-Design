import asyncio
import base64
import http.client
import json
import os
import socket
from contextlib import asynccontextmanager
from urllib.parse import quote, urlsplit

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from aad_platform.daemon.window import WindowServer


def _dispatch(calls):
    async def dispatch(method, params):
        calls.append((method, params))
        ok = not params.get("force_error", False)
        return {
            "structured": {"ok": ok, "project": params.get("project", "demo"), "method": method},
            "text": f"reply for {method}",
        }

    return dispatch


@asynccontextmanager
async def _running_server(token="window-test-token", viewer_html="<!doctype html><head></head><body>stub</body>"):
    calls = []
    server = WindowServer(token, _dispatch(calls), lambda: viewer_html)
    port = await server.start()
    try:
        yield server, port, calls
    finally:
        await server.stop()


def _request(port, path, *, method="GET", headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
    finally:
        connection.close()


async def _http(port, path, *, method="GET", headers=None, body=None):
    return await asyncio.to_thread(_request, port, path, method=method, headers=headers, body=body)


def _path(url):
    parsed = urlsplit(url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _raw_websocket_handshake(port, path, *, host, origin=None):
    connection = socket.create_connection(("127.0.0.1", port), timeout=4)
    connection.settimeout(4)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    headers = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if origin is not None:
        headers.append(f"Origin: {origin}")
    connection.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
    response = bytearray()
    try:
        while b"\r\n\r\n" not in response:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
    finally:
        connection.close()
    head, _, body = bytes(response).partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    response_headers = {}
    for line in head.split(b"\r\n")[1:]:
        if b":" in line:
            name, value = line.split(b":", 1)
            response_headers[name.decode("latin-1").lower()] = value.decode("latin-1").strip()
    return status, response_headers, body


async def _websocket_handshake(port, path, *, host, origin=None):
    return await asyncio.to_thread(
        _raw_websocket_handshake,
        port,
        path,
        host=host,
        origin=origin,
    )


def test_server_binds_loopback_and_serves_injected_html_with_security_headers():
    async def scenario():
        token = "x</script><>&\u2028\u2029"
        async with _running_server(token=token, viewer_html="<!doctype html><head data-view='stub'></head>") as (
            server,
            port,
            _,
        ):
            assert server._listener.getsockname()[0] == "127.0.0.1"
            assert server.port == port
            expected_url = f"http://127.0.0.1:{port}/p/bracket?token={quote(token, safe='')}"
            assert server.url("bracket") == expected_url
            status, headers, body = await _http(port, _path(expected_url))
            html = body.decode("utf-8")
            assert status == 200
            assert "<head data-view='stub'><script>window.__AAD_WINDOW__ = {\"project\":\"bracket\",\"token\":\"x\\u003c/script\\u003e\\u003c\\u003e\\u0026\\u2028\\u2029\"};</script>" in html
            assert headers["cache-control"] == "no-store"
            assert headers["referrer-policy"] == "no-referrer"
            assert headers["x-content-type-options"] == "nosniff"
            csp = headers["content-security-policy"]
            assert "script-src 'unsafe-inline'" in csp
            assert "style-src 'unsafe-inline'" in csp
            assert f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}" in csp
            assert "img-src blob: data:" in csp
            assert "https://" not in csp

    asyncio.run(scenario())


def test_every_route_requires_the_token_and_invalid_projects_are_not_served():
    async def scenario():
        async with _running_server() as (server, port, _):
            page = "/p/demo"
            status, _, body = await _http(port, page)
            assert status == 401 and body == b""
            status, _, body = await _http(port, page + "?token=wrong")
            assert status == 401 and body == b""

            headers = {"Content-Type": "application/json"}
            for route in ("/rpc", "/context"):
                for authorization in (None, "Bearer wrong"):
                    request_headers = dict(headers)
                    if authorization is not None:
                        request_headers["Authorization"] = authorization
                    status, _, body = await _http(
                        port,
                        route,
                        method="POST",
                        headers=request_headers,
                        body=b"{}",
                    )
                    assert status == 401 and body == b""

            ws_path = "/ws?project=demo"
            status, _, body = await _websocket_handshake(port, ws_path, host=f"127.0.0.1:{port}")
            assert status == 401 and body == b""
            status, _, body = await _websocket_handshake(
                port,
                ws_path + "&token=wrong",
                host=f"127.0.0.1:{port}",
            )
            assert status == 401 and body == b""

            good_url = server.url("demo")
            status, _, _ = await _http(port, _path(good_url.replace("/p/demo", "/p/-invalid")))
            assert status == 404
            status, _, _ = await _http(port, _path(good_url.replace("/p/demo", "/p/" + "a" * 65)))
            assert status == 404

    asyncio.run(scenario())


def test_host_and_origin_checks_cover_http_and_websocket_handshakes():
    async def scenario():
        async with _running_server() as (server, port, _):
            path = _path(server.url("demo"))
            good_origin = f"http://127.0.0.1:{port}"
            status, _, _ = await _http(port, path, headers={"Host": f"evil.example:{port}"})
            assert status == 403
            status, _, _ = await _http(port, path, headers={"Host": f"localhost:{port}"})
            assert status == 200
            status, _, _ = await _http(port, path, headers={"Origin": "https://evil.example"})
            assert status == 403
            status, _, _ = await _http(port, path, headers={"Origin": good_origin})
            assert status == 200

            post_headers = {
                "Authorization": "Bearer window-test-token",
                "Content-Type": "application/json",
                "Origin": "http://evil.example",
            }
            status, _, _ = await _http(
                port,
                "/rpc",
                method="POST",
                headers=post_headers,
                body=json.dumps({"method": "aad_show", "params": {}}),
            )
            assert status == 403
            status, _, _ = await _http(
                port,
                "/context",
                method="POST",
                headers=post_headers,
                body=json.dumps({"project": "demo", "selection": []}),
            )
            assert status == 403

            ws_path = "/ws?project=demo&token=window-test-token"
            status, _, body = await _websocket_handshake(
                port,
                ws_path,
                host=f"127.0.0.1:{port}",
                origin="http://evil.example",
            )
            assert status == 403 and body == b""
            status, _, body = await _websocket_handshake(
                port,
                ws_path,
                host=f"attacker.example:{port}",
                origin=good_origin,
            )
            assert status == 403 and body == b""

            async with connect(
                f"ws://127.0.0.1:{port}{ws_path}", origin=f"http://localhost:{port}", proxy=None
            ):
                pass

    asyncio.run(scenario())


def test_post_requires_json_but_accepts_same_origin_or_no_origin():
    async def scenario():
        async with _running_server() as (server, port, calls):
            rpc_body = json.dumps({"method": "aad_show", "params": {"project": "demo"}})
            context_body = json.dumps({"project": "demo", "selection": []})
            base = {"Authorization": "Bearer window-test-token"}
            status, _, _ = await _http(
                port,
                "/rpc",
                method="POST",
                headers=base,
                body=rpc_body,
            )
            assert status == 415
            status, _, _ = await _http(
                port,
                "/context",
                method="POST",
                headers={**base, "Content-Type": "text/plain"},
                body=context_body,
            )
            assert status == 415

            status, _, body = await _http(
                port,
                "/rpc",
                method="POST",
                headers={**base, "Content-Type": "application/json"},
                body=rpc_body,
            )
            assert status == 200
            assert json.loads(body)["structuredContent"]["ok"] is True
            status, _, body = await _http(
                port,
                "/context",
                method="POST",
                headers={**base, "Content-Type": "application/json", "Origin": f"http://localhost:{port}"},
                body=context_body,
            )
            assert status == 200 and json.loads(body) == {"ok": True}
            status, _, body = await _http(
                port,
                "/context",
                method="POST",
                headers={**base, "Content-Type": "application/json"},
                body=context_body,
            )
            assert status == 200 and json.loads(body) == {"ok": True}
            assert calls == [("aad_show", {"project": "demo"})]

    asyncio.run(scenario())


def test_rpc_allowlist_happy_path_error_mapping_and_mcp_result_shape():
    async def scenario():
        async with _running_server() as (server, port, calls):
            headers = {
                "Authorization": "Bearer window-test-token",
                "Content-Type": "application/json; charset=utf-8",
            }
            async def call(method, params):
                return await _http(
                    port,
                    "/rpc",
                    method="POST",
                    headers=headers,
                    body=json.dumps({"method": method, "params": params}),
                )

            status, _, body = await call("aad_show", {"project": "demo"})
            result = json.loads(body)
            assert status == 200
            assert result == {
                "content": [{"type": "text", "text": "reply for aad_show"}],
                "structuredContent": {"ok": True, "project": "demo", "method": "aad_show"},
                "isError": False,
            }

            status, _, body = await call("aad_set_param", {"project": "demo", "force_error": True})
            assert status == 200
            assert json.loads(body)["isError"] is True

            for method in ("aad_scene_read", "aad_pick_info", "aad_file_read"):
                status, _, body = await call(method, {})
                assert status == 200
                assert json.loads(body)["structuredContent"]["method"] == method

            count = len(calls)
            status, _, body = await call("aad_build", {})
            assert status == 403 and body == b""
            status, _, body = await call(["aad_show"], {})
            assert status == 403 and body == b""
            assert len(calls) == count

    asyncio.run(scenario())


def test_request_and_selection_bodies_are_bounded():
    async def scenario():
        async with _running_server() as (_, port, _):
            headers = {
                "Authorization": "Bearer window-test-token",
                "Content-Type": "application/json",
            }
            large = b" " * (1024 * 1024 + 1)
            for route in ("/rpc", "/context"):
                status, _, _ = await _http(
                    port,
                    route,
                    method="POST",
                    headers=headers,
                    body=large,
                )
                assert status == 413

            selection = [{"project": "demo", "selector": "x" * (256 * 1024)}]
            status, _, _ = await _http(
                port,
                "/context",
                method="POST",
                headers=headers,
                body=json.dumps({"project": "demo", "selection": selection}),
            )
            assert status == 413

    asyncio.run(scenario())


def test_context_replaces_selection_and_take_clears_it():
    async def scenario():
        async with _running_server() as (server, port, _):
            headers = {
                "Authorization": "Bearer window-test-token",
                "Content-Type": "application/json",
            }
            first = [
                {"project": "demo", "rev": 2, "kind": "face", "selector": "face('top')"},
                {"project": "demo", "rev": 2, "kind": "edge", "selector": "edge('rim')"},
            ]
            status, _, _ = await _http(
                port,
                "/context",
                method="POST",
                headers=headers,
                body=json.dumps({"project": "demo", "selection": first, "text": "top face"}),
            )
            assert status == 200
            assert server.take_selection("other") == []
            assert server.take_selection("demo") == first
            assert server.take_selection("demo") == []

            replacement = [{"project": "demo", "rev": 3, "kind": "vertex", "selector": "vertex('tip')"}]
            status, _, _ = await _http(
                port,
                "/context",
                method="POST",
                headers=headers,
                body=json.dumps({"project": "demo", "selection": replacement}),
            )
            assert status == 200
            assert server.take_selection("demo") == replacement

    asyncio.run(scenario())


def test_broadcast_targets_only_matching_project_and_dead_socket_does_not_block():
    async def scenario():
        async with _running_server() as (server, port, _):
            token = "window-test-token"
            origin = f"http://127.0.0.1:{port}"
            async with connect(
                f"ws://127.0.0.1:{port}/ws?token={token}&project=alpha", origin=origin, proxy=None
            ) as alpha:
                async with connect(
                    f"ws://127.0.0.1:{port}/ws?token={token}&project=beta", origin=origin, proxy=None
                ) as beta:
                    assert server.has_viewers("alpha")
                    assert server.has_viewers("beta")
                    assert server.connection_count == 2
                    envelope = {"ok": True, "project": "alpha", "rev": 4}
                    assert await server.broadcast("alpha", envelope) == 1
                    assert json.loads(await alpha.recv()) == {"type": "result", "envelope": envelope}
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(beta.recv(), timeout=0.1)

            async with connect(
                f"ws://127.0.0.1:{port}/ws?token={token}&project=alpha", origin=origin, proxy=None
            ) as dead:
                async with connect(
                    f"ws://127.0.0.1:{port}/ws?token={token}&project=alpha", origin=origin, proxy=None
                ) as live:
                    assert server.connection_count == 2
                    dead.transport.abort()
                    await asyncio.sleep(0.05)
                    envelope = {"ok": True, "project": "alpha", "rev": 5}
                    assert await asyncio.wait_for(server.broadcast("alpha", envelope), timeout=1.0) == 1
                    assert json.loads(await live.recv()) == {"type": "result", "envelope": envelope}

    asyncio.run(scenario())


def test_open_once_suppresses_browser_but_records_project(monkeypatch):
    monkeypatch.setenv("AAD_NO_BROWSER", "1")
    opened = []

    def forbidden_open(url):
        opened.append(url)
        raise AssertionError("browser must remain closed in tests")

    monkeypatch.setattr("aad_platform.daemon.window.webbrowser.open", forbidden_open)

    async def scenario():
        async with _running_server() as (server, _, _):
            assert server.open_once("demo") is False
            assert server.open_once("demo") is False
            assert opened == []

    asyncio.run(scenario())


def test_stop_closes_sockets_and_finishes_server_task():
    async def scenario():
        server = WindowServer("window-test-token", _dispatch([]), lambda: "<head></head>")
        port = await server.start()
        websocket = await connect(
            f"ws://127.0.0.1:{port}/ws?token=window-test-token&project=demo",
            origin=f"http://127.0.0.1:{port}",
            proxy=None,
        )
        server_task = server._server_task
        assert server.connection_count == 1
        await server.stop()
        assert server.port is None
        assert server.connection_count == 0
        assert server_task is not None and server_task.done()
        with pytest.raises(ConnectionClosed):
            await websocket.recv()
        await websocket.close()

    asyncio.run(scenario())
