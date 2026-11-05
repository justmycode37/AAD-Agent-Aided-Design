"""Drives `aiad mcp` over real stdio, with HOME and AIAD_HOME in a temp dir."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

RESPONSE_TIMEOUT_S = 240.0


class McpProcess:
    """A real adapter subprocess speaking newline-delimited JSON-RPC."""

    def __init__(self, env: dict[str, str]):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "aiad_platform.cli", "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            encoding="utf-8",
        )
        self._next_id = 1

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self.send({"id": request_id, "method": method, "params": params or {}})
        assert self.process.stdout is not None
        deadline = time.monotonic() + RESPONSE_TIMEOUT_S
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                raise AssertionError(f"adapter exited: {self.process.stderr.read() if self.process.stderr else ''}")
            message = json.loads(line)
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise AssertionError(f"{method} failed: {message['error']}")
                return message["result"]
            if "method" in message and "id" in message:
                # A server-to-client request, such as roots/list. We declare no roots.
                self.send({"id": message["id"], "error": {"code": -32601, "message": "unsupported"}})
        raise AssertionError(f"{method} timed out")

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        for stream in (self.process.stdout, self.process.stderr):
            if stream:
                stream.close()


@pytest.fixture
def engine() -> None:
    """Skip until the S1 engine is on main. After that, the workspace always has it."""
    pytest.importorskip("aiad.engine.jobs")


@pytest.fixture
def aiad_env() -> Iterator[dict[str, str]]:
    # Short path: a unix socket path must stay under about 100 bytes.
    with tempfile.TemporaryDirectory(prefix="aiad-e2e-", dir="/tmp") as tmp:
        home = Path(tmp).resolve()
        env = {
            **os.environ,
            "HOME": str(home),
            "AIAD_HOME": str(home / ".aiad"),
            "AIAD_IDLE_TIMEOUT_S": "60",
            "AIAD_NO_BROWSER": "1",
        }
        yield env
        info = home / ".aiad" / "run" / "aiadd.json"
        if info.exists():
            pid = json.loads(info.read_text())["pid"]
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass


@pytest.fixture
def mcp_process(aiad_env: dict[str, str]) -> Iterator[McpProcess]:
    process = McpProcess(aiad_env)
    yield process
    process.close()
