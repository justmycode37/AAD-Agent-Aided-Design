from __future__ import annotations

import shutil
import tempfile
import os
import subprocess
import sys
import time
import asyncio
from pathlib import Path

import pytest

from aad_platform import daemon_ctl
from aad_platform import doctor


@pytest.fixture
def temp_home(monkeypatch):
    home = Path(tempfile.mkdtemp(prefix="aad-", dir="/tmp"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AAD_HOME", str(home))
    monkeypatch.setenv("AAD_NO_BROWSER", "1")
    try:
        yield home
    finally:
        # This only probes the daemon associated with this test's temp AAD_HOME.
        try:
            daemon_ctl.stop(timeout_s=0.2)
        except Exception:
            pass
        shutil.rmtree(home, ignore_errors=True)


def test_engine_failure_names_the_step_and_skips_dependents(monkeypatch, temp_home):
    monkeypatch.delenv("AAD_NO_BROWSER", raising=False)

    def check_daemon_without_browser_override():
        assert "AAD_NO_BROWSER" not in os.environ
        return doctor._check("daemon", "pass", "Daemon check ran.")

    monkeypatch.setattr(doctor, "_python_check", lambda: doctor._check("python", "pass", "Python check ran."))
    monkeypatch.setattr(doctor, "_version_check", lambda: doctor._check("version", "pass", "Version check ran."))
    monkeypatch.setattr(doctor, "_home_check", lambda: doctor._check("aad_home", "pass", "Home check ran."))
    monkeypatch.setattr(doctor, "_config_check", lambda: doctor._check("config", "pass", "Config check ran."))
    monkeypatch.setattr(doctor, "_mcp_startup_check", lambda: doctor._check("mcp_startup", "pass", "MCP check ran."))
    monkeypatch.setattr(doctor, "_engine_check", lambda: doctor._check("engine", "fail", "aad.engine.jobs is not importable: injected failure.", "Install the engine."))
    monkeypatch.setattr(doctor, "_kernel_check", lambda: doctor._check("kernel", "pass", "Kernel check ran."))
    monkeypatch.setattr(doctor, "_viewer_check", lambda: doctor._check("viewer", "warn", "Stub viewer."))
    monkeypatch.setattr(doctor, "_daemon_check", check_daemon_without_browser_override)
    monkeypatch.setattr(doctor, "_socket_check", lambda _check: doctor._check("socket_permissions", "pass", "Socket check ran."))
    monkeypatch.setattr(doctor, "_create_only_check", lambda: doctor._check("project_create", "pass", "Project creation ran."))
    monkeypatch.setattr(doctor, "_clients_check", lambda: doctor._check("clients", "pass", "Client check ran."))

    result = doctor.run_doctor()
    assert result["ok"] is False
    checks = {item["id"]: item for item in result["checks"]}
    assert checks["engine"]["status"] == "fail"
    assert "not importable" in checks["engine"]["message"]
    assert checks["engine"]["fix"]
    assert checks["project_build"]["status"] == "skip"
    assert "engine failed" in checks["project_build"]["message"]
    assert checks["project_show"]["status"] == "skip"
    assert "engine failed" in checks["project_show"]["message"]
    assert checks["render"]["status"] == "skip"
    assert checks["export"]["status"] == "skip"
    assert "AAD_NO_BROWSER" not in __import__("os").environ or __import__("os").environ["AAD_NO_BROWSER"] == "1"


def test_kernel_check_accepts_either_real_environment_outcome(temp_home):
    result = doctor._kernel_check()
    assert result["id"] == "kernel"
    assert result["status"] in {"pass", "fail"}
    assert "subprocess" in result["message"]
    if result["status"] == "fail":
        assert result.get("fix")


def test_viewer_check_reports_stub_as_warning(temp_home):
    result = doctor._viewer_check()
    assert result["status"] in {"pass", "warn", "fail"}
    if result["status"] == "warn":
        assert "stub" in result["message"].lower()


@pytest.mark.parametrize("library, expected", [(None, "fail"), ("libOSMesa.so.8", "pass")])
def test_headless_linux_graphics_reports_missing_osmesa(monkeypatch, library, expected):
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(doctor.ctypes.util, "find_library", lambda _: library)
    result = doctor._graphics_check()
    assert result["status"] == expected
    if library is None:
        assert "libosmesa6" in result["fix"]


def test_partial_initialize_output_respects_startup_deadline(monkeypatch, temp_home):
    popen = subprocess.Popen

    def partial_response(*args, **kwargs):
        return popen([sys.executable, "-c", "import sys,time; sys.stdout.write('{'); sys.stdout.flush(); time.sleep(10)"], **kwargs)

    monkeypatch.setattr(doctor.subprocess, "Popen", partial_response)
    monkeypatch.setattr(doctor, "MCP_INITIALIZE_TIMEOUT_S", 0.2)
    started = time.monotonic()
    assert doctor._mcp_startup_check()["status"] == "fail"
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("response,expected", [('{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{},"serverInfo":{"name":"test","version":"1"}}}', "warn"), ('not JSON', "fail")])
def test_slow_initialize_preserves_performance_warning_and_rejects_invalid_output(monkeypatch, temp_home, response, expected):
    popen = subprocess.Popen

    def delayed_response(*args, **kwargs):
        code = f"import time; time.sleep(.05); print({response!r}, flush=True); time.sleep(10)"
        return popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(doctor.subprocess, "Popen", delayed_response)
    monkeypatch.setattr(doctor, "MCP_FIRST_RESPONSE_LIMIT_S", 0.01)
    monkeypatch.setattr(doctor, "MCP_INITIALIZE_TIMEOUT_S", 2.0)
    result = doctor._mcp_startup_check()
    assert result["status"] == expected
    if expected == "warn":
        assert result["within_budget"] is False
        assert result["elapsed_seconds"] > result["budget_seconds"]
        assert "startup budget" in result["fix"]


@pytest.mark.parametrize("method", ["aad_render", "aad_export", "aad_show"])
def test_rpc_waits_for_native_operations_but_keeps_control_calls_bounded(monkeypatch, method):
    class SlowClient:
        async def call(self, _method, _params):
            await asyncio.sleep(0.05)
            return {"ok": True}

    monkeypatch.setattr(doctor, "CONTROL_RPC_TIMEOUT_S", 0.01)
    monkeypatch.setattr(doctor, "CAD_RPC_TIMEOUT_S", 0.5)
    if method == "aad_show":
        with pytest.raises(TimeoutError):
            asyncio.run(doctor._rpc_call(SlowClient(), method, {}))
    else:
        assert asyncio.run(doctor._rpc_call(SlowClient(), method, {})) == {"ok": True}


def test_native_operation_still_has_a_finite_deadline(monkeypatch):
    class StalledClient:
        async def call(self, _method, _params):
            await asyncio.sleep(10)

    monkeypatch.setattr(doctor, "CAD_RPC_TIMEOUT_S", 0.01)
    with pytest.raises(TimeoutError):
        asyncio.run(doctor._rpc_call(StalledClient(), "aad_render", {}))


def test_doctor_request_does_not_open_window(monkeypatch):
    from aad_platform.daemon import handlers

    class Window:
        def open_once(self, _project):
            pytest.fail("doctor must not launch a browser")

        def url(self, project):
            return "http://127.0.0.1/" + project

    async def window(_self):
        return Window()

    monkeypatch.setattr(handlers.Handlers, "_ensure_window", window)
    monkeypatch.setattr(handlers, "viewer_mode", lambda _client: "window")
    result = asyncio.run(handlers.Handlers()._viewer_for("p", doctor.DOCTOR_CLIENT))
    assert result["mode"] == "window"


def test_prewarm_bounds_total_time_and_skips_retry_after_timeout(monkeypatch):
    from aad_platform import prewarm

    clock = [0.0]
    calls = []
    monkeypatch.setattr(prewarm.time, "monotonic", lambda: clock[0])

    def slow_import(module, *, timeout_s):
        calls.append((module, timeout_s))
        clock[0] += timeout_s
        return {"status": "fail", "seconds": timeout_s, "timed_out": True, "error": "timeout"}

    monkeypatch.setattr(prewarm, "_import_once", slow_import)
    result = prewarm.run_prewarm()
    assert result["ok"] is False
    assert 1 <= len(calls) <= len(prewarm.MODULES)
    assert sum(timeout for _, timeout in calls) <= prewarm.TOTAL_TIMEOUT_S
    assert calls[0][1] <= prewarm.TOTAL_TIMEOUT_S
    assert result["modules"][0]["second"]["status"] == "skip"
