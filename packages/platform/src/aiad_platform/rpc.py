"""Newline-delimited JSON-RPC 2.0 between adapter and daemon (CONTRACTS section 10)."""

from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from typing import Any

PROTOCOL = 1
READ_LIMIT = 16 * 1024 * 1024  # One message; a scene chunk is about 175 KiB after base64.

METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def encode(message: dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


class RpcClient:
    """One multiplexed connection. Safe for concurrent calls from one event loop."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._task = asyncio.create_task(self._read_loop())

    @classmethod
    async def connect(cls, path: Path) -> RpcClient:
        reader, writer = await asyncio.open_unix_connection(str(path), limit=READ_LIMIT)
        return cls(reader, writer)

    @property
    def closed(self) -> bool:
        return self._task.done()

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self.closed:
            raise ConnectionError("daemon connection is closed")
        request_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._writer.write(
            encode({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        )
        await self._writer.drain()
        return await future

    async def close(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass
        self._task.cancel()

    async def _read_loop(self) -> None:
        error: BaseException = ConnectionError("daemon closed the connection")
        try:
            while line := await self._reader.readline():
                message = json.loads(line)
                future = self._pending.pop(message.get("id"), None)
                if future is None or future.done():
                    continue
                if "error" in message:
                    err = message["error"]
                    future.set_exception(RpcError(err.get("code", INTERNAL_ERROR), err.get("message", "")))
                else:
                    future.set_result(message.get("result"))
        except (ConnectionError, ValueError, asyncio.LimitOverrunError) as exc:
            error = ConnectionError(f"daemon connection failed: {exc}")
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()
