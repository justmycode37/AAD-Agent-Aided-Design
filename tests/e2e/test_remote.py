"""`aiad serve --http` over a real socket: authentication, isolation and the build flow."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

MODEL = "from build123d import Box\nresult = Box(60, 40, 6)\n"
CLI = [sys.executable, "-m", "aiad_platform.cli"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Remote:
    def __init__(self, port: int):
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.ids = iter(range(1, 10_000))

    def post(self, token: str | None, method: str, params: dict[str, Any], headers: dict[str, str] | None = None, session: str | None = None) -> tuple[dict[str, Any], str | None]:
        body = {"jsonrpc": "2.0", "id": next(self.ids), "method": method, "params": params}
        all_headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
        if token:
            all_headers["Authorization"] = f"Bearer {token}"
        if session:
            all_headers["Mcp-Session-Id"] = session
        all_headers.update(headers or {})
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=all_headers)
        with urllib.request.urlopen(request, timeout=240) as response:
            return json.load(response), response.headers.get("Mcp-Session-Id")

    def open_session(self, token: str) -> str:
        reply, session = self.post(
            token,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}},
        )
        assert reply["result"]["serverInfo"]["name"] == "aiad"
        assert session
        notification = urllib.request.Request(
            self.url,
            data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": f"Bearer {token}", "Mcp-Session-Id": session, "MCP-Protocol-Version": "2025-11-25"},
        )
        urllib.request.urlopen(notification, timeout=30).close()
        return session

    def call(self, token: str, session: str, tool: str, **arguments: Any) -> dict[str, Any]:
        reply, _ = self.post(token, "tools/call", {"name": tool, "arguments": arguments}, session=session)
        return reply["result"]


@pytest.fixture
def env() -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="aiad-r-", dir="/tmp") as tmp:
        home = Path(tmp).resolve()
        yield {**os.environ, "HOME": str(home), "AIAD_HOME": str(home / ".aiad"), "AIAD_IDLE_TIMEOUT_S": "60", "AIAD_NO_BROWSER": "1"}
        for info in (home / ".aiad" / "users").glob("*/.aiad/run/aiadd.json"):
            try:
                os.kill(json.loads(info.read_text())["pid"], 15)
            except (OSError, ValueError):
                pass


def _add_user(env: dict[str, str], name: str) -> str:
    done = subprocess.run([*CLI, "serve", "--add-user", name, "--json"], env=env, capture_output=True, text=True, timeout=30)
    return json.loads(done.stdout)["token"]


@pytest.fixture
def server(env: dict[str, str]) -> Iterator[tuple[Remote, str, str]]:
    alice, bob = _add_user(env, "alice"), _add_user(env, "bob")
    port = _free_port()
    process = subprocess.Popen([*CLI, "serve", "--http", "--port", str(port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("server did not start")
        yield Remote(port), alice, bob
    finally:
        process.terminate()
        process.wait(timeout=15)
        if process.stderr:
            process.stderr.close()


def test_refuses_to_start_without_users(env: dict[str, str]) -> None:
    done = subprocess.run([*CLI, "serve", "--http", "--port", str(_free_port()), "--json"], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode != 0
    assert "requires authentication" in json.loads(done.stdout)["error"]


@pytest.mark.parametrize("extra", [[], ["--public-url", "http://example.com"]])
def test_refuses_network_bind_without_tls(env: dict[str, str], extra: list[str]) -> None:
    _add_user(env, "alice")
    done = subprocess.run([*CLI, "serve", "--http", "--host", "0.0.0.0", "--port", str(_free_port()), "--json", *extra], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode != 0
    assert "https" in json.loads(done.stdout)["error"]


def test_tokens_are_stored_hashed(env: dict[str, str]) -> None:
    token = _add_user(env, "alice")
    stored = Path(env["AIAD_HOME"], "remote_users.json")
    assert token not in stored.read_text()
    assert stored.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("token", [None, "wrong"])
def test_rejects_missing_and_wrong_tokens(server: tuple[Remote, str, str], token: str | None) -> None:
    remote, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as denied:
        remote.post(token, "tools/list", {})
    assert denied.value.code == 401


def test_rejects_a_foreign_host_header(server: tuple[Remote, str, str]) -> None:
    remote, alice, _ = server
    with pytest.raises(urllib.error.HTTPError) as denied:
        remote.post(alice, "tools/list", {}, headers={"Host": "evil.example"})
    assert denied.value.code in (403, 421)


def test_users_cannot_see_each_other(server: tuple[Remote, str, str]) -> None:
    remote, alice, bob = server
    a, b = remote.open_session(alice), remote.open_session(bob)
    assert remote.call(alice, a, "aiad_new", name="secret")["structuredContent"]["ok"]

    other = remote.call(bob, b, "aiad_history", project="secret")["structuredContent"]
    assert other["failure"]["code"] == "BAD_INPUT"
    # Bob may use the same name without touching Alice's project.
    assert remote.call(bob, b, "aiad_new", name="secret")["structuredContent"]["ok"]


def test_import_cannot_read_server_files(server: tuple[Remote, str, str]) -> None:
    remote, alice, _ = server
    session = remote.open_session(alice)
    refused = remote.call(alice, session, "aiad_import", path="/etc/hosts")["structuredContent"]
    assert refused["failure"]["code"] == "IMPORT_FAILED"


@pytest.mark.usefixtures("engine")
def test_build_show_and_chunks_over_http(server: tuple[Remote, str, str]) -> None:
    remote, alice, _ = server
    session = remote.open_session(alice)
    remote.call(alice, session, "aiad_new", name="part")
    built = remote.call(alice, session, "aiad_build", project="part", source=MODEL, wait_s=200)["structuredContent"]
    assert built["ok"] and built["rev"] == 1, built

    shown = remote.call(alice, session, "aiad_show", project="part")["structuredContent"]
    assert shown["viewer"]["mode"] == "inline"
    manifest = shown["scene"]

    data = b""
    while True:
        chunk = remote.call(alice, session, "aiad_scene_read", scene_id=manifest["scene_id"], offset=len(data))["structuredContent"]
        data += base64.b64decode(chunk["data_b64"])
        if not chunk["has_more"]:
            break
    assert hashlib.sha256(data).hexdigest() == manifest["scene_id"]
