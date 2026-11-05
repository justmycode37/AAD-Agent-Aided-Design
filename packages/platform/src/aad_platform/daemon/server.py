"""The aadd process: single-instance lock, unix socket, dispatch and idle exit."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import secrets
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .. import __version__, paths
from ..rpc import INTERNAL_ERROR, METHOD_NOT_FOUND, PROTOCOL, READ_LIMIT, RpcError, encode
from .handlers import Handlers

log = logging.getLogger("aadd")
DEFAULT_IDLE_TIMEOUT_S = 1800.0


def _idle_timeout() -> float:
    try:
        return float(os.environ.get("AAD_IDLE_TIMEOUT_S", DEFAULT_IDLE_TIMEOUT_S))
    except ValueError:
        return DEFAULT_IDLE_TIMEOUT_S


class Daemon:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(32)
        self.handlers = Handlers(self.token, self._on_window_start)
        self.http_port: int | None = None
        self.started = time.time()
        self.clients: set[asyncio.StreamWriter] = set()
        self.last_activity = time.monotonic()
        self.stop = asyncio.Event()

    async def serve(self) -> None:
        sock = paths.socket_path()
        sock.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._on_client, path=str(sock), limit=READ_LIMIT)
        os.chmod(sock, 0o600)
        self._write_info(sock)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop.set)
        idle = asyncio.create_task(self._idle_watch())
        log.info("aadd %s listening on %s, pid %s", __version__, sock, os.getpid())
        try:
            async with server:
                await self.stop.wait()
        finally:
            idle.cancel()
            await self.handlers.close()
            for writer in list(self.clients):
                writer.close()
            sock.unlink(missing_ok=True)
            paths.daemon_info_path().unlink(missing_ok=True)
            log.info("aadd stopped")

    def _on_window_start(self, port: int) -> None:
        self.http_port = port
        self._write_info(paths.socket_path())

    def _write_info(self, sock: Path) -> None:
        info = {
            "pid": os.getpid(),
            "http_port": self.http_port,
            "token": self.token,
            "version": __version__,
            "protocol": PROTOCOL,
            "socket": str(sock),
            "started": self.started,
        }
        path = paths.daemon_info_path()
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(info, fh)
        os.replace(tmp, path)

    async def _idle_watch(self) -> None:
        timeout = _idle_timeout()
        while True:
            await asyncio.sleep(min(timeout, 30.0))
            if self.clients or self.handlers.busy:
                self.last_activity = time.monotonic()
            elif time.monotonic() - self.last_activity >= timeout:
                log.info("idle for %.0f s with no clients, exiting", timeout)
                self.stop.set()
                return

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.add(writer)
        tasks: set[asyncio.Task[None]] = set()
        try:
            while line := await reader.readline():
                self.last_activity = time.monotonic()
                task = asyncio.create_task(self._handle_line(line, writer))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except (ConnectionError, asyncio.LimitOverrunError, ValueError) as exc:
            log.warning("client connection dropped: %s", exc)
        finally:
            self.clients.discard(writer)
            self.last_activity = time.monotonic()
            with contextlib.suppress(Exception):
                writer.close()

    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        try:
            request = json.loads(line)
            method, params = request["method"], request.get("params") or {}
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("bad request: %s", exc)
            return
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": request.get("id")}
        started = time.monotonic()
        try:
            response["result"] = await self._dispatch(method, params)
            client = params.get("client") or {}
            log.info(
                "%s client=%s/%s ui=%s %.0f ms",
                method,
                client.get("name"),
                client.get("version"),
                client.get("supports_ui"),
                (time.monotonic() - started) * 1000,
            )
        except KeyError:
            response["error"] = {"code": METHOD_NOT_FOUND, "message": f"unknown method {method!r}"}
        except RpcError as exc:
            response["error"] = {"code": exc.code, "message": exc.message}
        except Exception as exc:  # A bug in one call must not take the daemon down.
            log.exception("%s failed", method)
            response["error"] = {"code": INTERNAL_ERROR, "message": f"{type(exc).__name__}: {exc}"}
        with contextlib.suppress(ConnectionError):
            writer.write(encode(response))
            await writer.drain()

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "hello":
            return {
                "daemon_version": __version__,
                "protocol": PROTOCOL,
                "pid": os.getpid(),
                "compatible": params.get("protocol") == PROTOCOL
                and params.get("adapter_version") == __version__,
            }
        if method == "shutdown":
            asyncio.get_running_loop().call_later(0.05, self.stop.set)
            return {"stopping": True}
        return await self.handlers.call(method, params)


def main() -> int:
    paths.ensure_private_dir(paths.aad_home())
    paths.ensure_private_dir(paths.run_dir())
    paths.ensure_private_dir(paths.logs_dir())
    logging.basicConfig(
        filename=paths.logs_dir() / "aadd.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Held for the life of the process, so a crash can never leave a stale lock.
    lock = open(paths.daemon_lock_path(), "a")  # noqa: SIM115
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("aadd is already running", file=sys.stderr)
        return 0
    asyncio.run(Daemon().serve())
    return 0
