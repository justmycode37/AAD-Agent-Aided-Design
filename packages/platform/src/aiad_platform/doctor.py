"""Installation diagnostics. Every reported pass corresponds to a completed check."""

from __future__ import annotations

import asyncio
import base64
import ctypes.util
import hashlib
import importlib
import json
import os
import selectors
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import __version__, config, daemon_ctl, paths, viewer

MCP_FIRST_RESPONSE_LIMIT_S = 1.0
IMPORT_TIMEOUT_S = 120.0
SCENE_CHUNK_BYTES = 128 * 1024
# An inline-capable client makes aiad_show return the scene without asking the
# daemon to launch a desktop browser. AIAD_NO_BROWSER is also set for subprocesses.
DOCTOR_CLIENT = {"name": "aiad-doctor", "supports_ui": True}

_IMPORT_SCRIPT = r"""
import importlib, json, sys, time
name = sys.argv[1]
started = time.perf_counter()
try:
    importlib.import_module(name)
except ModuleNotFoundError as exc:
    missing = exc.name == name or name.startswith((exc.name or "") + ".")
    print(json.dumps({"status": "missing" if missing else "fail", "seconds": time.perf_counter() - started, "error": str(exc)}))
    raise SystemExit(0 if missing else 1)
except Exception as exc:
    print(json.dumps({"status": "fail", "seconds": time.perf_counter() - started, "error": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(1)
print(json.dumps({"status": "pass", "seconds": time.perf_counter() - started}))
"""


def _check(check_id: str, status: str, message: str, fix: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"id": check_id, "status": status, "message": message}
    if fix:
        result["fix"] = fix
    return result


def _skip(check_id: str, failed_step: str) -> dict[str, Any]:
    return _check(check_id, "skip", f"Skipped because {failed_step} failed.")


def _probe_import(module: str, timeout_s: float = IMPORT_TIMEOUT_S) -> dict[str, Any]:
    env = os.environ.copy()
    env["AIAD_NO_BROWSER"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-c", _IMPORT_SCRIPT, module],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "seconds": timeout_s, "error": f"import timed out after {timeout_s:.0f} s"}
    except OSError as exc:
        return {"status": "fail", "seconds": 0.0, "error": str(exc)}
    lines = result.stdout.strip().splitlines()
    if lines:
        try:
            info = json.loads(lines[-1])
            if isinstance(info, dict) and "status" in info:
                return info
        except (ValueError, TypeError):
            pass
    error = result.stderr.strip() or result.stdout.strip() or f"import exited with status {result.returncode}"
    return {"status": "fail", "seconds": 0.0, "error": error[-1200:]}


def _python_check() -> dict[str, Any]:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info[:2] == (3, 12):
        return _check("python", "pass", f"Python {version}.")
    return _check("python", "fail", f"Python {version}; AIAD requires Python 3.12.", "Install Python 3.12 and reinstall AIAD with `uv tool install aiad`.")


def _version_check() -> dict[str, Any]:
    if __version__ and __version__ != "0.0.0+unknown":
        return _check("version", "pass", f"AIAD {__version__}.")
    return _check("version", "warn", f"AIAD version is unknown ({__version__}).", "Install AIAD as a uv tool so package metadata is available.")


def _home_check() -> dict[str, Any]:
    home = paths.aiad_home()
    try:
        paths.ensure_private_dir(home)
        mode = stat.S_IMODE(home.stat().st_mode)
        fd, probe = tempfile.mkstemp(prefix=".doctor-", dir=home)
        os.close(fd)
        Path(probe).unlink(missing_ok=True)
    except OSError as exc:
        return _check("aiad_home", "fail", f"{home} is not writable and private: {exc}", "Make AIAD_HOME writable by your user and set its permissions to 0700.")
    if mode & 0o077:
        return _check("aiad_home", "fail", f"{home} has mode {mode:04o}; expected private mode 0700.", "Run `chmod 700` on AIAD_HOME.")
    return _check("aiad_home", "pass", f"{home} is writable with mode 0700.")


def _config_check() -> dict[str, Any]:
    try:
        values = config.load()
    except config.ConfigError as exc:
        return _check("config", "fail", str(exc), "Fix or remove the invalid config.toml in AIAD_HOME.")
    return _check("config", "pass", f"Configuration parsed ({len(values)} value(s)).")


def _mcp_startup_check() -> dict[str, Any]:
    env = os.environ.copy()
    env["AIAD_NO_BROWSER"] = "1"
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "aiad-doctor", "version": __version__},
        },
    }
    started = time.monotonic()
    process: subprocess.Popen[str] | None = None
    response: dict[str, Any] | None = None
    error: str | None = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "aiad_platform.cli", "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()
        remaining = max(0.0, MCP_FIRST_RESPONSE_LIMIT_S - (time.monotonic() - started))
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if selector.select(remaining):
                line = process.stdout.readline()
                try:
                    candidate = json.loads(line)
                except (ValueError, TypeError):
                    error = f"adapter returned invalid JSON: {line[:200]!r}"
                else:
                    if isinstance(candidate, dict) and candidate.get("id") == 1 and isinstance(candidate.get("result"), dict):
                        response = candidate
                    else:
                        error = f"adapter returned an invalid initialize response: {candidate!r}"
            else:
                error = "no initialize response within 1.0 s"
    except (OSError, AssertionError) as exc:
        error = f"could not run aiad mcp: {exc}"
    finally:
        elapsed = time.monotonic() - started
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    if response is not None and elapsed < MCP_FIRST_RESPONSE_LIMIT_S:
        return _check("mcp_startup", "pass", f"First initialize response in {elapsed:.3f} s (limit 1.000 s).")
    if response is not None:
        error = f"first initialize response took {elapsed:.3f} s (limit 1.000 s)"
    return _check("mcp_startup", "fail", error or "adapter did not initialize.", "Reinstall AIAD and check `aiad mcp` dependencies; MCP startup must stay under 1 s.")


def _engine_check() -> dict[str, Any]:
    result = _probe_import("aiad.engine.jobs")
    seconds = float(result.get("seconds", 0.0))
    if result.get("status") == "pass":
        return _check("engine", "pass", f"aiad.engine.jobs imported in {seconds:.2f} s.")
    detail = result.get("error", "module is not installed")
    return _check("engine", "fail", f"aiad.engine.jobs is not importable: {detail}", "Install the AIAD engine runtime with `uv tool upgrade aiad` or reinstall AIAD.")


def _kernel_check() -> dict[str, Any]:
    reports = {name: _probe_import(name) for name in ("build123d", "OCP")}
    details = [
        f"{name}: {reports[name]['status']} in {float(reports[name].get('seconds', 0.0)):.2f} s"
        + (f" ({reports[name]['error']})" if reports[name].get("error") else "")
        for name in reports
    ]
    if all(value.get("status") == "pass" for value in reports.values()):
        return _check("kernel", "pass", "Kernel imports in subprocesses: " + "; ".join(details) + ".")
    return _check("kernel", "fail", "Kernel imports in subprocesses: " + "; ".join(details) + ".", "Install the pinned build123d/OCP runtime with `uv tool upgrade aiad`.")


def _viewer_check() -> dict[str, Any]:
    try:
        html_path = viewer.viewer_path()
        size = html_path.stat().st_size
    except OSError as exc:
        return _check("viewer", "fail", f"Viewer HTML was not found: {exc}", "Reinstall AIAD or build the packaged viewer.")
    if size > 3 * 1024 * 1024:
        return _check("viewer", "fail", f"Viewer HTML is {size} bytes; the limit is 3 MiB.", "Reduce or rebuild the packaged viewer to at most 3 MiB.")
    if html_path.resolve() == viewer.STUB.resolve():
        return _check("viewer", "warn", f"Found the {size}-byte stub viewer.", "Build and package the full viewer when the S3 viewer is available.")
    return _check("viewer", "pass", f"Found {html_path} ({size} bytes).")


def _graphics_check() -> dict[str, Any]:
    if sys.platform != "linux" or os.environ.get("DISPLAY"):
        return _check("headless_graphics", "skip", "Headless Linux rendering check does not apply.")
    if ctypes.util.find_library("OSMesa"):
        return _check("headless_graphics", "pass", "OSMesa is available for headless Linux rendering.")
    return _check("headless_graphics", "fail", "No display or OSMesa library is available for agent rendering.", "On Debian or Ubuntu install libosmesa6; on other distributions install the OSMesa runtime package.")


def _daemon_check() -> dict[str, Any]:
    try:
        status = daemon_ctl.ensure_running(timeout_s=15)
    except Exception as exc:
        return _check("daemon", "fail", f"Daemon did not start or answer hello: {type(exc).__name__}: {exc}", "Check AIAD_HOME/logs/aiadd.log, then run `aiad daemon status`.")
    if status.get("compatible") and status.get("version") == __version__:
        return _check("daemon", "pass", f"aiadd pid {status.get('pid')} answered hello with AIAD {status.get('version')}.")
    return _check("daemon", "fail", f"Daemon hello did not match AIAD {__version__}: {status!r}.", "Stop the old daemon with `aiad daemon stop`, then retry.")


def _socket_check(daemon_check: dict[str, Any]) -> dict[str, Any]:
    if daemon_check["status"] != "pass":
        return _skip("socket_permissions", "daemon")
    try:
        mode = stat.S_IMODE(paths.socket_path().stat().st_mode)
    except OSError as exc:
        return _check("socket_permissions", "fail", f"Could not inspect {paths.socket_path()}: {exc}", "Restart the daemon with `aiad daemon start`.")
    if mode == 0o600:
        return _check("socket_permissions", "pass", f"Daemon socket mode is 0600 ({paths.socket_path()}).")
    return _check("socket_permissions", "fail", f"Daemon socket mode is {mode:04o}; expected 0600.", "Stop the daemon and restart it so the socket is recreated with mode 0600.")


def _reply_structured(reply: Any) -> dict[str, Any]:
    if isinstance(reply, dict):
        structured = reply.get("structured")
        if isinstance(structured, dict):
            return structured
        if "ok" in reply:
            return reply
    return {}


def _reply_message(reply: Any) -> str:
    if isinstance(reply, dict):
        structured = _reply_structured(reply)
        return str(reply.get("text") or structured.get("summary") or structured.get("message") or reply)
    return str(reply)


def _reply_ok(reply: Any) -> bool:
    structured = _reply_structured(reply)
    return structured.get("ok") is True


async def _rpc_calls(calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    from .rpc import RpcClient

    client = await asyncio.wait_for(RpcClient.connect(paths.socket_path()), timeout=2.0)
    try:
        answers = []
        for method, params in calls:
            timeout = 130.0 if method == "aiad_build" else 15.0
            answers.append(await asyncio.wait_for(client.call(method, params), timeout=timeout))
        return answers
    finally:
        await client.close()


async def _rpc_call(client: Any, method: str, params: dict[str, Any], timeout_s: float = 15.0) -> Any:
    return await asyncio.wait_for(client.call(method, params), timeout=timeout_s)


async def _read_scene(client: Any, scene_id: str) -> tuple[bool, str]:
    offset = 0
    data = bytearray()
    total: int | None = None
    while True:
        reply = await _rpc_call(
            client,
            "aiad_scene_read",
            {"scene_id": scene_id, "offset": offset, "length": SCENE_CHUNK_BYTES, "client": DOCTOR_CLIENT},
        )
        chunk = _reply_structured(reply)
        if not chunk or chunk.get("offset") != offset:
            return False, f"Scene read returned an invalid chunk at offset {offset}."
        try:
            raw = base64.b64decode(chunk["data_b64"], validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            return False, f"Scene chunk at offset {offset} is invalid: {exc}."
        if total is None:
            total = int(chunk.get("total_bytes", -1))
        if total != chunk.get("total_bytes") or len(raw) != chunk.get("byte_count"):
            return False, f"Scene chunk at offset {offset} has inconsistent length metadata."
        if not raw and chunk.get("has_more"):
            return False, f"Scene read made no progress at offset {offset}."
        data.extend(raw)
        offset += len(raw)
        if not chunk.get("has_more"):
            break
        if offset > (total or 0):
            return False, "Scene read exceeded its reported size."
    digest = hashlib.sha256(data).hexdigest()
    if digest != scene_id:
        return False, f"Scene SHA-256 mismatch: expected {scene_id}, read {digest}."
    return True, f"Read and verified {len(data)} scene bytes against SHA-256 {scene_id}."


def _not_implemented(reply: Any) -> str | None:
    structured = _reply_structured(reply)
    failure = structured.get("failure")
    messages = [
        _reply_message(reply),
        str(structured.get("summary", "")),
        str(failure.get("message", "")) if isinstance(failure, dict) else "",
    ]
    for message in messages:
        if "not implemented" in message.lower():
            return message
    return None


async def _project_checks(root: Path) -> list[dict[str, Any]]:
    from .rpc import RpcClient

    project = "doctor-smoke"
    client_info = {**DOCTOR_CLIENT, "root": str(root)}
    rev: int | None = None
    dependents = ("project_build", "project_show", "scene_integrity", "render", "export")
    try:
        client = await asyncio.wait_for(RpcClient.connect(paths.socket_path()), timeout=2.0)
    except Exception as exc:
        checks = [_check("project_create", "fail", f"Could not connect to aiadd: {type(exc).__name__}: {exc}", "Restart the daemon and retry `aiad doctor`.")]
        checks.extend(_skip(check_id, "project_create") for check_id in dependents)
        return checks
    try:
        checks: list[dict[str, Any]] = []
        try:
            new_reply = await _rpc_call(client, "aiad_new", {"name": project, "client": client_info})
        except Exception as exc:
            checks.append(_check("project_create", "fail", f"aiad_new failed: {type(exc).__name__}: {exc}", "Check AIAD_HOME permissions and daemon logs."))
            checks.extend(_skip(check_id, "project_create") for check_id in dependents)
            return checks
        if not _reply_ok(new_reply):
            checks.append(_check("project_create", "fail", _reply_message(new_reply), "Check AIAD_HOME permissions and project creation errors."))
            checks.extend(_skip(check_id, "project_create") for check_id in dependents)
            return checks
        checks.append(_check("project_create", "pass", _reply_message(new_reply)))

        source = "from build123d import Box\nresult = Box(10, 10, 10)\n"
        try:
            build_reply = await _rpc_call(
                client,
                "aiad_build",
                {"project": project, "source": source, "wait_s": 120, "client": client_info},
                timeout_s=130.0,
            )
        except Exception as exc:
            checks.append(_check("project_build", "fail", f"aiad_build failed: {type(exc).__name__}: {exc}", "Install the AIAD engine and kernel runtime, then retry `aiad doctor`."))
            checks.extend(_skip(check_id, "project_build") for check_id in ("project_show", "scene_integrity", "render", "export"))
            return checks
        structured = _reply_structured(build_reply)
        build_ok = _reply_ok(build_reply) and structured.get("status") != "running"
        rev_value = structured.get("rev") or structured.get("head_rev")
        rev = int(rev_value) if isinstance(rev_value, int) else None
        build_message = _reply_message(build_reply)
        if structured.get("status") == "running":
            build_message += " Build did not finish within wait_s=120."
            build_ok = False
        checks.append(_check("project_build", "pass" if build_ok else "fail", build_message, None if build_ok else "Install the AIAD engine and kernel runtime, then retry `aiad doctor`."))
        if not build_ok:
            for dependent in ("project_show", "scene_integrity", "render", "export"):
                checks.append(_skip(dependent, "project_build"))
            return checks

        try:
            show_reply = await _rpc_call(client, "aiad_show", {"project": project, "rev": rev, "client": client_info})
        except Exception as exc:
            checks.append(_check("project_show", "fail", f"aiad_show failed: {type(exc).__name__}: {exc}", "Check the built revision and daemon logs."))
            checks.append(_skip("scene_integrity", "project_show"))
            for method, check_id, params in (
                ("aiad_render", "render", {"project": project, "rev": rev, "client": client_info}),
                ("aiad_export", "export", {"project": project, "rev": rev, "formats": ["stl"], "allow_unverified": True, "client": client_info}),
            ):
                try:
                    result = await _rpc_call(client, method, params)
                except Exception as render_exc:
                    checks.append(_check(check_id, "fail", f"{method} failed: {type(render_exc).__name__}: {render_exc}", "Check the daemon log and retry `aiad doctor`."))
                    continue
                unimplemented = _not_implemented(result)
                if unimplemented:
                    checks.append(_check(check_id, "skip", unimplemented))
                else:
                    ok = _reply_ok(result)
                    checks.append(_check(check_id, "pass" if ok else "fail", _reply_message(result), None if ok else f"Check the {method} failure in the daemon log."))
            return checks
        show_ok = _reply_ok(show_reply)
        checks.append(_check("project_show", "pass" if show_ok else "fail", _reply_message(show_reply), None if show_ok else "Check the built revision and viewer setup, then retry `aiad doctor`."))
        if show_ok:
            scene = _reply_structured(show_reply).get("scene")
            scene_id = scene.get("scene_id") if isinstance(scene, dict) else None
            if isinstance(scene_id, str) and scene_id:
                try:
                    verified, message = await _read_scene(client, scene_id)
                except Exception as exc:
                    verified, message = False, f"Scene read failed: {type(exc).__name__}: {exc}"
                checks.append(_check("scene_integrity", "pass" if verified else "fail", message, None if verified else "Restart the daemon and rebuild the project so its scene can be read.") )
            else:
                checks.append(_check("scene_integrity", "fail", "aiad_show returned no scene_id.", "Rebuild the project and check scene generation in the daemon log."))
        else:
            checks.append(_skip("scene_integrity", "project_show"))

        for method, check_id, params in (
            ("aiad_render", "render", {"project": project, "rev": rev, "client": client_info}),
            ("aiad_export", "export", {"project": project, "rev": rev, "formats": ["stl"], "allow_unverified": True, "client": client_info}),
        ):
            try:
                result = await _rpc_call(client, method, params)
            except Exception as exc:
                checks.append(_check(check_id, "fail", f"{method} failed: {type(exc).__name__}: {exc}", "Check the daemon log and retry `aiad doctor`."))
                continue
            unimplemented = _not_implemented(result)
            if unimplemented:
                checks.append(_check(check_id, "skip", unimplemented))
            else:
                ok = _reply_ok(result)
                checks.append(_check(check_id, "pass" if ok else "fail", _reply_message(result), None if ok else f"Check the {method} failure in the daemon log."))
        return checks
    finally:
        await client.close()


def _run_project_checks(home: Path) -> list[dict[str, Any]]:
    try:
        with tempfile.TemporaryDirectory(prefix="doctor-", dir=home) as temporary:
            return asyncio.run(_project_checks(Path(temporary)))
    except Exception as exc:
        checks = [
            _check("project_create", "fail", f"Could not run the temporary project check: {type(exc).__name__}: {exc}", "Check that AIAD_HOME is writable and that the daemon is running.")
        ]
        checks.extend(_skip(check_id, "project_create") for check_id in ("project_build", "project_show", "scene_integrity", "render", "export"))
        return checks


def _create_only_check() -> dict[str, Any]:
    """Confirm project creation even when an engine failure blocks the build."""
    from .rpc import RpcClient

    try:
        with tempfile.TemporaryDirectory(prefix="doctor-", dir=paths.aiad_home()) as temporary:
            root = Path(temporary)

            async def create() -> Any:
                client = await RpcClient.connect(paths.socket_path())
                try:
                    return await client.call(
                        "aiad_new",
                        {"name": "doctor-smoke", "client": {**DOCTOR_CLIENT, "root": str(root)}},
                    )
                finally:
                    await client.close()

            reply = asyncio.run(create())
    except Exception as exc:
        return _check("project_create", "fail", f"Temporary project creation failed: {type(exc).__name__}: {exc}", "Check daemon logs and AIAD_HOME permissions.")
    ok = _reply_ok(reply)
    return _check(
        "project_create",
        "pass" if ok else "fail",
        _reply_message(reply),
        None if ok else "Check daemon logs and AIAD_HOME permissions.",
    )


def _clients_check() -> dict[str, Any]:
    try:
        clients = importlib.import_module(".clients", package="aiad_platform")
        entries = clients.statuses()
    except ModuleNotFoundError as exc:
        if exc.name not in {"aiad_platform.clients", "aiad_platform"}:
            return _check("clients", "fail", f"Client status check failed: {exc}", "Reinstall AIAD and its client integration package.")
        return _check("clients", "warn", "Client writers are not available in this build.", "Upgrade to a build that includes the client configuration writers.")
    except Exception as exc:
        return _check("clients", "fail", f"Client status check failed: {type(exc).__name__}: {exc}", "Reinstall AIAD and check client configuration permissions.")
    detected = [entry.id for entry in entries if entry.detected]
    installed = [entry.id for entry in entries if entry.installed]
    return _check("clients", "pass", f"Client status checked: {len(detected)} detected, {len(installed)} configured.")


def run_doctor() -> dict[str, Any]:
    """Run independent checks, then the daemon-backed CAD smoke test."""
    prior_no_browser = os.environ.get("AIAD_NO_BROWSER")
    os.environ["AIAD_NO_BROWSER"] = "1"
    try:
        checks: list[dict[str, Any]] = [
            _python_check(),
            _version_check(),
            _home_check(),
            _config_check(),
            _mcp_startup_check(),
            _engine_check(),
            _kernel_check(),
            _viewer_check(),
            _graphics_check(),
        ]
        home_check = next(check for check in checks if check["id"] == "aiad_home")
        if home_check["status"] == "pass":
            daemon_check = _daemon_check()
        else:
            daemon_check = _skip("daemon", "aiad_home")
        checks.append(daemon_check)
        checks.append(_socket_check(daemon_check))
        if daemon_check["status"] != "pass":
            for check_id in ("project_create", "project_build", "project_show", "scene_integrity", "render", "export"):
                checks.append(_skip(check_id, "daemon"))
        else:
            engine_check = next(check for check in checks if check["id"] == "engine")
            kernel_check = next(check for check in checks if check["id"] == "kernel")
            if engine_check["status"] != "pass" or kernel_check["status"] != "pass":
                prerequisite = "engine" if engine_check["status"] != "pass" else "kernel"
                created = _create_only_check()
                checks.append(created)
                failed_step = prerequisite if created["status"] == "pass" else "project_create"
                for check_id in ("project_build", "project_show", "scene_integrity", "render", "export"):
                    checks.append(_skip(check_id, failed_step))
            else:
                checks.extend(_run_project_checks(paths.aiad_home()))
        checks.append(_clients_check())
        return {"ok": not any(check["status"] == "fail" for check in checks), "checks": checks}
    finally:
        if prior_no_browser is None:
            os.environ.pop("AIAD_NO_BROWSER", None)
        else:
            os.environ["AIAD_NO_BROWSER"] = prior_no_browser
