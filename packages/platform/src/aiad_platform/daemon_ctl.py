"""Synchronous lifecycle helpers for the local ``aiadd`` process."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__, paths
from .rpc import PROTOCOL, RpcClient

DEFAULT_START_TIMEOUT_S = 15.0
DEFAULT_STOP_TIMEOUT_S = 3.0


def spawn_daemon() -> None:
    """Start aiadd detached, with its output in the private AIAD log directory."""
    paths.ensure_private_dir(paths.aiad_home())
    paths.ensure_private_dir(paths.run_dir())
    paths.ensure_private_dir(paths.logs_dir())
    with (paths.logs_dir() / "aiadd.out").open("ab") as out:
        subprocess.Popen(
            [sys.executable, "-m", "aiad_platform.daemon"],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            start_new_session=True,
            close_fds=True,
            env=os.environ.copy(),
        )


def _read_info() -> dict[str, Any]:
    try:
        value = json.loads(paths.daemon_info_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _run(coro: Any) -> Any:
    """Run the private async RPC helpers from this synchronous API."""
    return asyncio.run(coro)


async def _connect_and_hello(timeout_s: float) -> tuple[RpcClient, dict[str, Any]]:
    client = await asyncio.wait_for(RpcClient.connect(paths.socket_path()), timeout=max(timeout_s, 0.01))
    try:
        result = await asyncio.wait_for(
            client.call("hello", {"adapter_version": __version__, "protocol": PROTOCOL}),
            timeout=max(timeout_s, 0.01),
        )
    except BaseException:
        await client.close()
        raise
    if not isinstance(result, dict):
        await client.close()
        raise ConnectionError("aiadd returned an invalid hello response")
    return client, result


def _public_status(info: dict[str, Any], hello: dict[str, Any] | None = None) -> dict[str, Any]:
    stored_started = info.get("started")
    try:
        uptime_s = max(0.0, time.time() - float(stored_started)) if stored_started is not None else None
    except (TypeError, ValueError):
        uptime_s = None
    return {
        "running": hello is not None,
        "pid": (hello or {}).get("pid", info.get("pid")),
        "version": (hello or {}).get("daemon_version", info.get("version")),
        "uptime_s": round(uptime_s, 1) if uptime_s is not None else None,
        "socket": str(paths.socket_path()),
        "http_port": info.get("http_port"),
        "protocol": (hello or {}).get("protocol", info.get("protocol")),
        "compatible": (hello or {}).get("compatible", False),
    }


async def _ensure_running(timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, timeout_s)
    spawned = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"aiadd did not start within {timeout_s:.0f} s")
        try:
            client, hello = await _connect_and_hello(min(remaining, 1.0))
        except (ConnectionError, FileNotFoundError, OSError, TimeoutError, asyncio.TimeoutError):
            if not spawned:
                spawn_daemon()
                spawned = True
            await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            continue

        if hello.get("compatible") and hello.get("daemon_version") == __version__:
            await client.close()
            return _public_status(_read_info(), hello)

        # A daemon from a different package version owns this socket. Ask it to exit,
        # then start the version that launched this CLI, as the adapter does.
        try:
            await asyncio.wait_for(client.call("shutdown"), min(max(remaining, 0.05), 1.0))
        except (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError):
            pass
        finally:
            await client.close()
        spawned = False
        await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


def ensure_running(timeout_s: float = DEFAULT_START_TIMEOUT_S) -> dict[str, Any]:
    """Ensure a compatible daemon is listening, starting or replacing it if needed."""
    return _run(_ensure_running(timeout_s))


async def _status() -> dict[str, Any]:
    info = _read_info()
    try:
        client, hello = await _connect_and_hello(0.5)
    except (ConnectionError, FileNotFoundError, OSError, TimeoutError, asyncio.TimeoutError):
        result = _public_status(info)
        result["running"] = False
        result["compatible"] = False
        return result
    await client.close()
    return _public_status(info, hello)


def status() -> dict[str, Any]:
    """Report the daemon metadata for this AIAD_HOME without starting it."""
    return _run(_status())


def _process_command(pid: int) -> str:
    """Read one exact pid's command line; never use a pattern-based process lookup."""
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        raw = proc_cmdline.read_bytes()
        if raw:
            return raw.replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


async def _request_shutdown() -> None:
    client = await asyncio.wait_for(RpcClient.connect(paths.socket_path()), timeout=0.5)
    try:
        await asyncio.wait_for(client.call("shutdown"), timeout=1.0)
    finally:
        await client.close()


async def _stop(timeout_s: float) -> bool:
    info = _read_info()
    if not info:
        # Metadata can be missing or corrupt while its daemon is still listening.
        try:
            client, hello = await _connect_and_hello(0.5)
        except (ConnectionError, OSError, TimeoutError):
            return not paths.socket_path().exists()
        await client.close()
        info = {"pid": hello.get("pid"), "socket": str(paths.socket_path())}
    try:
        pid = int(info.get("pid"))
    except (TypeError, ValueError):
        pid = 0

    # Only address the socket recorded for this AIAD_HOME.
    recorded_socket = info.get("socket")
    if recorded_socket and Path(recorded_socket) == paths.socket_path():
        try:
            await _request_shutdown()
        except (ConnectionError, FileNotFoundError, OSError, TimeoutError, asyncio.TimeoutError):
            pass

    deadline = time.monotonic() + max(0.0, timeout_s)
    while time.monotonic() < deadline:
        current = _read_info()
        if not paths.socket_path().exists() or (pid and current.get("pid") is not None and current["pid"] != pid):
            return True
        await asyncio.sleep(0.05)

    # The RPC shutdown should normally remove the socket. If it did not, verify the
    # exact recorded process before sending SIGTERM; a reused or unrelated pid is left alone.
    if pid <= 0 or recorded_socket != str(paths.socket_path()):
        return False
    command = _process_command(pid)
    if "aiad_platform" not in command:
        return False
    try:
        client, hello = await _connect_and_hello(0.5)
    except (ConnectionError, FileNotFoundError, OSError, TimeoutError, asyncio.TimeoutError):
        return False
    await client.close()
    if hello.get("pid") != pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not paths.socket_path().exists():
            return True
        await asyncio.sleep(0.05)
    return not paths.socket_path().exists()


def stop(timeout_s: float = DEFAULT_STOP_TIMEOUT_S) -> bool:
    """Ask this AIAD_HOME's daemon to stop and use a guarded SIGTERM as a fallback."""
    return bool(_run(_stop(timeout_s)))
