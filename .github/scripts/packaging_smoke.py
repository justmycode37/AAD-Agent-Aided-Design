#!/usr/bin/env python3
"""Build local AIAD wheels and validate a clean uv tool installation.

This smoke test uses temporary uv, home, AIAD, and client-config directories.
Only AIAD's own wheels are taken from the workspace; all runtime components
are passed to ``uv tool install`` as local wheel files.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import selectors
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_WHEELS = {
    "aiad": "0.0.1",
    "aiad-engine": "0.1.0",
    "aiad-render": "0.0.1",
    "aiad-drafting": "0.1.0",
    "aiad-quality": "0.1.0",
}
RUNTIME_PINS = {
    "mcp": "2.3.0",
    "build123d": "0.13.0",
    "cadquery-ocp-novtk": "8.0.1.1.0",
    "ezdxf": "1.4.5",
    "vtk": "9.7.1",
}
BLOCKED_IMPORTS = (
    "build123d",
    "OCP",
    "vtk",
    "vtkmodules",
    "aiad.engine",
    "aiad_render",
    "aiad_drafting",
    "aiad_quality",
)
RUNTIME_SIZE_LIMIT_BYTES = 1_000_000_000
INSTALL_TIME_LIMIT_SECONDS = 180.0
MCP_STARTUP_BUDGET_S = 1.0
MCP_FUNCTIONAL_TIMEOUT_S = 30.0


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _format_command(command: list[str]) -> str:
    display = list(command)
    for index, value in enumerate(display[:-1]):
        if value == "-c":
            display[index + 1] = "<inline Python>"
    return shlex.join(display)


def _run_logged(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 900.0,
) -> None:
    print(f"$ {_format_command(command)}", flush=True)
    completed = subprocess.run(command, cwd=cwd, env=env, check=False, timeout=timeout)
    if completed.returncode:
        raise RuntimeError(f"Command exited {completed.returncode}: {_format_command(command)}")


def _run_captured(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 300.0,
) -> subprocess.CompletedProcess[str]:
    print(f"$ {_format_command(command)}", flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n", flush=True)
    if completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr, flush=True)
    if completed.returncode:
        raise RuntimeError(
            f"Command exited {completed.returncode}: {_format_command(command)}\n"
            f"{completed.stdout[-4000:]}\n{completed.stderr[-4000:]}"
        )
    return completed


def _wheel(wheel_dir: Path, distribution: str, version: str) -> Path:
    normalized = distribution.replace("-", "_").lower()
    prefix = f"{normalized}-{version}-"
    matches = sorted(path for path in wheel_dir.glob("*.whl") if path.name.lower().startswith(prefix))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {distribution} {version} wheel in {wheel_dir}; found {matches!r}.")
    return matches[0].resolve()


def _parse_last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"Command did not produce a JSON object: {stdout[-2000:]}")


def _isolated_environment(work_dir: Path, install_cache_dir: Path | None = None) -> tuple[dict[str, str], bool]:
    paths = {
        "home": work_dir / "home",
        "aiad_home": work_dir / "aiad-home",
        "appdata": work_dir / "appdata",
        "xdg_config": work_dir / "xdg-config",
        "xdg_data": work_dir / "xdg-data",
        "xdg_cache": work_dir / "xdg-cache",
        "uv_tools": work_dir / "uv-tools",
        "uv_bin": work_dir / "uv-bin",
        "uv_cache": install_cache_dir.resolve() if install_cache_dir else work_dir / "uv-cache-install",
    }
    for key, path in paths.items():
        if key == "uv_cache" and install_cache_dir is not None:
            if not path.is_dir():
                raise RuntimeError(f"Provisioned uv cache directory does not exist: {path}")
            continue
        path.mkdir(parents=True, exist_ok=True)
    # A separate, initially empty cache makes the registry download measurement
    # distinct from a provisioned/cache-hit installation.
    cache_was_empty = not any(paths["uv_cache"].iterdir())
    if install_cache_dir is None and not cache_was_empty:
        raise RuntimeError(f"The isolated install cache must start empty: {paths['uv_cache']}")

    env = os.environ.copy()
    # Overrides that could point the isolated run at another checkout or at real client configs.
    for key in (
        "PYTHONPATH", "PYTHONHOME", "AIAD_VIEWER_HTML", "UV_PROJECT", "UV_WORKSPACE",
        "CODEX_HOME", "CLINE_MCP_SETTINGS_PATH", "CLINE_DATA_DIR", "CLINE_DIR",
    ):  # fmt: skip
        env.pop(key, None)
    env.update(
        {
            "HOME": str(paths["home"]),
            "USERPROFILE": str(paths["home"]),
            "AIAD_HOME": str(paths["aiad_home"]),
            "APPDATA": str(paths["appdata"]),
            "XDG_CONFIG_HOME": str(paths["xdg_config"]),
            "XDG_DATA_HOME": str(paths["xdg_data"]),
            "XDG_CACHE_HOME": str(paths["xdg_cache"]),
            "UV_TOOL_DIR": str(paths["uv_tools"]),
            "UV_TOOL_BIN_DIR": str(paths["uv_bin"]),
            "UV_CACHE_DIR": str(paths["uv_cache"]),
            "UV_PYTHON": "3.12",
            "UV_NO_CONFIG": "1",
            "PYTHONNOUSERSITE": "1",
            "AIAD_NO_BROWSER": "1",
            "PATH": str(paths["uv_bin"]) + os.pathsep + env.get("PATH", ""),
        }
    )
    return env, cache_was_empty


def _find_tool_python(tool_dir: Path) -> Path:
    for config in tool_dir.rglob("pyvenv.cfg"):
        env_dir = config.parent
        for candidate in (
            env_dir / "bin" / "python",
            env_dir / "bin" / "python3",
            env_dir / "Scripts" / "python.exe",
        ):
            if candidate.is_file():
                # Preserve a venv's Python symlink path: resolving it points at
                # the base interpreter and drops the tool's site-packages.
                return candidate.absolute()
    raise RuntimeError(f"Could not locate the installed aiad tool interpreter under {tool_dir}.")


def _disk_usage_bytes(paths: list[Path]) -> int:
    """Return allocated bytes reported by du -sk for existing install paths."""
    existing = [path for path in paths if path.exists()]
    if not existing:
        return 0
    completed = subprocess.run(
        ["du", "-sk", *(str(path) for path in existing)],
        check=True,
        text=True,
        capture_output=True,
    )
    return sum(int(line.split(maxsplit=1)[0]) * 1024 for line in completed.stdout.splitlines() if line.strip())


def _install_info_probe(
    python: Path,
    *,
    cwd: Path,
    env: dict[str, str],
    wheels: dict[str, Path],
    repo: Path,
) -> dict[str, Any]:
    code = r'''
import importlib, importlib.metadata as metadata, json, pathlib, sys
from urllib.parse import unquote, urlsplit

repo = pathlib.Path(sys.argv[1]).resolve()
wheel_dir = pathlib.Path(sys.argv[2]).resolve()
expected_wheels = json.loads(sys.argv[3])
expected_runtime = json.loads(sys.argv[4])
module_names = {
    "aiad_platform",
    "aiad.engine.jobs",
    "aiad_render",
    "aiad_drafting",
    "aiad_quality",
}
site_roots = [pathlib.Path(p).resolve() for p in sys.path if "site-packages" in p or "dist-packages" in p]
modules = {}
for module_name in sorted(module_names):
    module = importlib.import_module(module_name)
    module_file = pathlib.Path(module.__file__).resolve()
    in_site = any(module_file.is_relative_to(root) for root in site_roots)
    if not in_site or module_file.is_relative_to(repo):
        raise SystemExit(f"{module_name} came from {module_file}, outside installed site-packages")
    modules[module_name] = {"file": str(module_file), "in_site_packages": in_site}

versions = {name: metadata.version(name) for name in (*expected_wheels, *expected_runtime)}
for name, expected in expected_runtime.items():
    if versions[name] != expected:
        raise SystemExit(f"{name} version {versions[name]} does not match required pin {expected}")

local_sources = {}
for name, expected_path in expected_wheels.items():
    direct_text = metadata.distribution(name).read_text("direct_url.json")
    if not direct_text:
        raise SystemExit(f"{name} has no direct_url.json; it was not proven to come from a local wheel")
    direct = json.loads(direct_text)
    parsed = urlsplit(direct.get("url", ""))
    source = pathlib.Path(unquote(parsed.path)).resolve() if parsed.scheme == "file" else None
    expected = pathlib.Path(expected_path).resolve()
    if source != expected or source.parent != wheel_dir:
        raise SystemExit(f"{name} source {direct.get('url')!r} is not the built wheel {expected}")
    local_sources[name] = direct.get("url")

from aiad_platform.viewer import viewer_path
viewer = viewer_path().resolve()
platform_root = pathlib.Path(importlib.import_module("aiad_platform").__file__).resolve().parent
if not viewer.is_file() or not viewer.is_relative_to(platform_root):
    raise SystemExit(f"Viewer HTML is missing from the installed package: {viewer}")
viewer_html_bytes = viewer.stat().st_size
if viewer_html_bytes <= 0:
    raise SystemExit("Installed viewer HTML is empty")

aiad_metadata = metadata.distribution("aiad").metadata
requires_dist = aiad_metadata.get_all("Requires-Dist") or []
required_runtime = ["aiad-engine==0.1.0", "aiad-drafting==0.1.0", "aiad-quality==0.1.0", "aiad-render[vtk]==0.0.1"]
for requirement in required_runtime:
    if not any(line.lower().replace(" ", "") == requirement.lower() for line in requires_dist):
        raise SystemExit(f"Installed aiad wheel is missing runtime requirement {requirement}: {requires_dist}")
from aiad_quality import library
for kind in ("part", "material", "playbook", "api"):
    if not library("search", kind=kind)["items"]:
        raise SystemExit(f"Installed quality catalog is empty for {kind}")

print(json.dumps({
    "versions": versions,
    "modules": modules,
    "local_wheels": local_sources,
    "viewer_html": {"path": str(viewer), "bytes": viewer_html_bytes},
    "requires_dist": requires_dist,
}, sort_keys=True))
'''
    completed = _run_captured(
        [
            str(python),
            "-c",
            code,
            str(repo),
            str(next(iter(wheels.values())).parent),
            json.dumps({name: str(path) for name, path in wheels.items()}),
            json.dumps(RUNTIME_PINS),
        ],
        cwd=cwd,
        env=env,
        timeout=240.0,
    )
    return _parse_last_json(completed.stdout)


def _stdio_initialize(
    executable: Path,
    *,
    work_dir: Path,
    env: dict[str, str],
    timeout_s: float,
    trace_path: Path | None = None,
) -> dict[str, Any]:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "aiad-packaging-smoke", "version": "0.0.1"},
        },
    }
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    response: dict[str, Any] | None = None
    error: str | None = None
    stderr = ""
    try:
        process = subprocess.Popen(
            [str(executable), "mcp"],
            cwd=work_dir,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        process.stdin.flush()
        deadline = started + timeout_s
        received = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(max(0.0, deadline - time.monotonic())):
                    break
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    error = "adapter exited before the initialize response"
                    break
                received.extend(chunk)
                if len(received) > 65536:
                    error = "initialize response exceeded 64 KiB"
                    break
                if b"\n" in received:
                    line = bytes(received).split(b"\n", 1)[0]
                    try:
                        candidate = json.loads(line)
                    except (ValueError, TypeError) as exc:
                        error = f"invalid initialize JSON: {exc}"
                    else:
                        result_body = candidate.get("result") if isinstance(candidate, dict) else None
                        if (
                            isinstance(result_body, dict)
                            and candidate.get("id") == 1
                            and result_body.get("serverInfo", {}).get("name") == "aiad"
                            and isinstance(result_body.get("protocolVersion"), str)
                            and isinstance(result_body.get("capabilities"), dict)
                        ):
                            response = candidate
                        else:
                            error = f"unexpected initialize response: {candidate!r}"
                    break
        elapsed = time.monotonic() - started
    except (OSError, AssertionError) as exc:
        elapsed = time.monotonic() - started
        error = f"could not complete initialize request: {exc}"
    finally:
        if process is not None and process.stdin is not None:
            process.stdin.close()
        if process is not None:
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
            if process.stderr is not None:
                stderr = process.stderr.read().decode(errors="replace")
                process.stderr.close()
            if process.stdout is not None:
                process.stdout.close()

    imports = trace_path.read_text(encoding="utf-8").splitlines() if trace_path and trace_path.exists() else []
    if response is None and error is None:
        error = f"no initialize response within {timeout_s:.3f} s"
    if response is not None and elapsed >= timeout_s:
        error = f"initialize response took {elapsed:.3f} s (limit {timeout_s:.3f} s)"
    if imports:
        error = f"MCP startup attempted kernel/runtime imports: {', '.join(imports)}"
    if error:
        if stderr:
            error += f"; adapter stderr: {stderr[-1200:]}"
        return {
            "status": "fail",
            "elapsed_seconds": round(elapsed, 4),
            "error": error,
            "response": response,
            "kernel_imports": imports,
        }
    return {
        "status": "pass",
        "elapsed_seconds": round(elapsed, 4),
        "response": response,
        "kernel_imports": imports,
    }


def _mcp_probe(executable: Path, *, work_dir: Path, env: dict[str, str]) -> dict[str, Any]:
    # Keep the time-critical probe uninstrumented; import tracing is run in a
    # separate process so its meta-path hook cannot distort the 1 s result.
    # A slow answer is a budget finding, recorded separately. Only no answer at all is a functional failure.
    result = _stdio_initialize(executable, work_dir=work_dir, env=env, timeout_s=MCP_FUNCTIONAL_TIMEOUT_S)
    result["initialize_seconds"] = result.pop("elapsed_seconds")
    result["within_startup_budget"] = (
        result.get("status") == "pass" and result["initialize_seconds"] < MCP_STARTUP_BUDGET_S
    )
    result["kernel_imports_absent"] = None
    return result


def _mcp_import_probe(executable: Path, *, work_dir: Path, env: dict[str, str]) -> dict[str, Any]:
    probe_dir = work_dir / "mcp-import-probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    trace_path = work_dir / "mcp-kernel-imports.txt"
    trace_path.unlink(missing_ok=True)
    sitecustomize = probe_dir / "sitecustomize.py"
    sitecustomize.write_text(
        f'''import os, sys
blocked = {BLOCKED_IMPORTS!r}
class ImportTrace:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            fd = os.open(os.environ["AIAD_IMPORT_TRACE"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.write(fd, (fullname + "\\n").encode())
            os.close(fd)
        return None
sys.meta_path.insert(0, ImportTrace())
''',
        encoding="utf-8",
    )
    probe_env = env.copy()
    probe_env["PYTHONPATH"] = str(probe_dir)
    probe_env["AIAD_IMPORT_TRACE"] = str(trace_path)
    result = _stdio_initialize(
        executable,
        work_dir=work_dir,
        env=probe_env,
        timeout_s=5.0,
        trace_path=trace_path,
    )
    result["initialize_seconds"] = result.pop("elapsed_seconds")
    result["kernel_imports_absent"] = not result.get("kernel_imports")
    return result


def _prewarm(python: Path, *, cwd: Path, env: dict[str, str], result_path: Path) -> dict[str, Any]:
    code = """import json, pathlib, sys, time
from aiad_platform.prewarm import run_prewarm
started = time.monotonic()
result = run_prewarm(lambda message: print(message, file=sys.stderr, flush=True))
result[\"elapsed_seconds\"] = round(time.monotonic() - started, 3)
pathlib.Path(sys.argv[1]).write_text(json.dumps(result, indent=2) + \"\\n\", encoding=\"utf-8\")
print(json.dumps({\"ok\": result.get(\"ok\"), \"elapsed_seconds\": result[\"elapsed_seconds\"]}))
"""
    _run_logged(
        [str(python), "-c", code, str(result_path)],
        cwd=cwd,
        env=env,
        timeout=240.0,
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def _provision_vtk(python: Path, *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    """Complete a VTK import after a timed prewarm attempt, before doctor render."""
    code = """import json, time, vtk
started = time.perf_counter()
version = vtk.vtkVersion.GetVTKVersion()
print(json.dumps({"version": version, "seconds": round(time.perf_counter() - started, 3)}))
"""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(python), "-c", code],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=240.0,
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "elapsed_seconds": round(time.monotonic() - started, 3), "error": "VTK import timed out after 240 seconds"}
    result = {
        "status": "pass" if completed.returncode == 0 else "fail",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    if completed.returncode == 0:
        try:
            detail = _parse_last_json(completed.stdout)
        except RuntimeError as exc:
            result["status"] = "fail"
            result["error"] = str(exc)
        else:
            result.update(detail)
    else:
        result["error"] = f"VTK import exited {completed.returncode}"
    return result


def _doctor(executable: Path, *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    command = [str(executable), "doctor", "--json"]
    print(f"$ {shlex.join(command)}", flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=600.0,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n", flush=True)
    if completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr, flush=True)
    result = _parse_last_json(completed.stdout)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["exit_code"] = completed.returncode
    required_checks = ("project_create", "project_build", "project_show", "scene_integrity", "render", "export")
    checks = {check.get("id"): check for check in result.get("checks", []) if isinstance(check, dict)}
    failed = [name for name in required_checks if checks.get(name, {}).get("status") != "pass"]
    result["required_cad_checks_passed"] = [name for name in required_checks if name not in failed]
    build_message = checks.get("project_build", {}).get("message", "")
    result["real_draft_part_built"] = (
        checks.get("project_build", {}).get("status") == "pass"
        and "Built doctor-smoke rev" in build_message
        and "(draft)" in build_message
        and "valid" in build_message
    )
    result["functional_smoke_passed"] = (
        completed.returncode == 0
        and not failed
        and result.get("ok") is True
        and result["real_draft_part_built"]
    )
    if completed.returncode != 0:
        result["functional_error"] = f"Doctor exited with status {completed.returncode}."
    elif failed:
        result["functional_error"] = f"Required CAD doctor checks did not pass: {', '.join(failed)}"
    elif not result["real_draft_part_built"]:
        result["functional_error"] = "Doctor did not report a valid real draft part build."
    elif result.get("ok") is not True:
        result["functional_error"] = "Doctor reported an overall failure."
    return result


def _prepare_work_dir(requested: Path | None) -> Path:
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="aiad-packaging-smoke-")).resolve()
    path = requested.expanduser().resolve()
    if path.exists() and any(path.iterdir()):
        raise RuntimeError(f"Refusing to reuse a non-empty packaging smoke directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, help="empty temporary directory for built wheels and isolated tool/home state")
    parser.add_argument("--result", type=Path, help="JSON result path (defaults to <work-dir>/packaging-result.json)")
    parser.add_argument("--download-cache", type=Path, help="reuse a provisioned uv cache; omit for an empty-cache download measurement")
    parser.add_argument("--prior-result", type=Path, help="include a previous fresh-install result in this JSON report")
    args = parser.parse_args()

    work_dir = _prepare_work_dir(args.work_dir)
    result_path = (args.result.expanduser().resolve() if args.result else work_dir / "packaging-result.json")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "validation_dir": str(work_dir),
        "machine": {
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        },
        "scope": {
            "install_kind": "fresh isolated UV cache; local AIAD wheels and registry runtime dependencies",
            "runtime_disk_scope": "allocated bytes in UV_TOOL_DIR plus UV_TOOL_BIN_DIR; excludes UV_CACHE_DIR, Python interpreter, and AIAD_HOME data",
            "browser_suppressed": True,
            "real_client_configs_accessed": False,
            "host_page_cache": "not flushed or controlled; this is not a clean-machine benchmark",
            "release_ready": False,
            "release_blockers": ["worker sandbox and independent validation issue #31"],
        },
    }
    functional_errors: list[str] = []
    if args.prior_result:
        previous = json.loads(args.prior_result.expanduser().read_text(encoding="utf-8"))
        result["fresh_empty_cache_install_measurement"] = {
            "path": str(args.prior_result.expanduser().resolve()),
            "cache_initially_empty": previous.get("install_cache_initially_empty"),
            "install_elapsed_seconds": previous.get("install_elapsed_seconds"),
            "runtime_disk_after_install_bytes": previous.get(
                "runtime_disk_after_install_bytes", previous.get("runtime_disk_bytes")
            ),
            "prior_run_status": previous.get("status"),
            "measurement_note": "Install command observation from an earlier run; this does not establish that the earlier full smoke passed.",
        }
    try:
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError(f"Packaging smoke must run under Python 3.12, got {platform.python_version()}.")
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("uv is required to build and install the local wheels.")

        wheel_dir = work_dir / "wheels"
        wheel_dir.mkdir(parents=True)
        build_env = os.environ.copy()
        build_env["UV_CACHE_DIR"] = str(work_dir / "uv-cache-build")
        build_env["UV_PYTHON"] = "3.12"
        build_env.pop("UV_PROJECT", None)  # The wheels under test must come from this checkout.
        build_env.pop("UV_WORKSPACE", None)
        build_started = time.monotonic()
        _run_logged(
            [uv, "build", "--all-packages", "--wheel", "--clear", "--out-dir", str(wheel_dir)],
            cwd=ROOT,
            env=build_env,
            timeout=900.0,
        )
        result["build_elapsed_seconds"] = round(time.monotonic() - build_started, 3)

        wheels = {
            name: _wheel(wheel_dir, name, version)
            for name, version in EXPECTED_WHEELS.items()
        }
        result["built_wheels"] = {name: str(path) for name, path in wheels.items()}
        with ZipFile(wheels["aiad"]) as package:
            viewer_files = sorted(
                member
                for member in package.namelist()
                if member.startswith("aiad_platform/viewer/") and member.lower().endswith(".html")
            )
        if not viewer_files:
            raise RuntimeError("The built aiad wheel contains no viewer HTML under aiad_platform/viewer/.")
        result["built_wheel_viewer_html"] = viewer_files

        env, install_cache_was_empty = _isolated_environment(work_dir, args.download_cache)
        tool_dir = Path(env["UV_TOOL_DIR"])
        bin_dir = Path(env["UV_TOOL_BIN_DIR"])
        result["install_cache_initially_empty"] = install_cache_was_empty
        result["install_cache_path"] = env["UV_CACHE_DIR"]
        if not install_cache_was_empty:
            result["scope"]["install_kind"] = "provisioned UV cache; local AIAD wheels and cached registry runtime dependencies"
        install_command = [uv]
        if args.download_cache:
            install_command.append("--offline")
        install_command.extend([
            "tool",
            "install",
            "--python",
            sys.executable,
            "--link-mode",
            "copy",
            "--with",
            str(wheels["aiad-engine"]),
            "--with",
            str(wheels["aiad-render"]),
            "--with",
            str(wheels["aiad-drafting"]),
            "--with",
            str(wheels["aiad-quality"]),
            str(wheels["aiad"]),
        ])
        install_started = time.monotonic()
        _run_logged(install_command, cwd=work_dir, env=env, timeout=900.0)
        install_elapsed = time.monotonic() - install_started
        result["install_elapsed_seconds"] = round(install_elapsed, 3)
        result["runtime_disk_after_install_bytes"] = _disk_usage_bytes([tool_dir, bin_dir])
        result["runtime_disk_measurement"] = "du -sk allocated blocks, multiplied by 1024"
        if install_cache_was_empty:
            result["fresh_empty_cache_install_measurement"] = {
                "cache_initially_empty": True,
                "install_elapsed_seconds": result["install_elapsed_seconds"],
                "runtime_disk_after_install_bytes": result["runtime_disk_after_install_bytes"],
                "measurement_note": "Fresh isolated uv package cache; host page cache was not flushed or controlled.",
            }

        executable = next((candidate for candidate in (bin_dir / "aiad", bin_dir / "aiad.exe") if candidate.is_file()), None)
        if executable is None:
            raise RuntimeError(f"uv did not install the aiad executable under {bin_dir}.")
        result["installed_executable"] = str(executable.resolve())
        python = _find_tool_python(tool_dir)
        result["installed_python"] = str(python)

        # The first process to import the adapter compiles its bytecode. No host can start
        # `aiad mcp` this early, because `aiad install` writes the configs after pre-warming,
        # so this number is informational. The budgeted probe runs after pre-warm below.
        try:
            result["mcp_before_prewarm"] = _mcp_probe(executable, work_dir=work_dir, env=env)
            if result["mcp_before_prewarm"].get("status") != "pass":
                functional_errors.append(
                    f"MCP initialize probe failed: {result['mcp_before_prewarm'].get('error', 'unknown error')}"
                )
        except Exception as exc:
            result["mcp_before_prewarm"] = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
            functional_errors.append(f"MCP initialize probe failed: {type(exc).__name__}: {exc}")
        result["mcp"] = {"status": "not_run"}
        try:
            import_probe = _mcp_import_probe(executable, work_dir=work_dir, env=env)
            result["mcp_import_probe"] = import_probe
            result["mcp"]["kernel_imports_absent"] = (
                import_probe.get("status") == "pass"
                and import_probe.get("kernel_imports_absent") is True
            )
            if not result["mcp"]["kernel_imports_absent"]:
                functional_errors.append(
                    "MCP initialize imported CAD kernel modules or import tracing failed: "
                    f"{import_probe.get('error', import_probe.get('kernel_imports'))}"
                )
        except Exception as exc:
            result["mcp_import_probe"] = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
            result["mcp"]["kernel_imports_absent"] = False
            functional_errors.append(f"MCP import probe failed: {type(exc).__name__}: {exc}")

        try:
            version_result = _run_captured(
                [str(executable), "version", "--json"], cwd=work_dir, env=env, timeout=30.0
            )
            version = _parse_last_json(version_result.stdout)
            result["version_command"] = version
            if version.get("version") != EXPECTED_WHEELS["aiad"]:
                functional_errors.append(f"Installed aiad version was {version.get('version')!r}, expected 0.0.1")
        except Exception as exc:
            functional_errors.append(f"Installed aiad version command failed: {type(exc).__name__}: {exc}")

        try:
            provenance = _install_info_probe(
                python,
                cwd=work_dir,
                env=env,
                wheels=wheels,
                repo=ROOT,
            )
            result["installed_artifact"] = provenance
        except Exception as exc:
            result["installed_artifact_error"] = f"{type(exc).__name__}: {exc}"
            functional_errors.append(f"Installed artifact provenance failed: {type(exc).__name__}: {exc}")

        prewarm_path = work_dir / "prewarm-result.json"
        prewarm_started = time.monotonic()
        try:
            prewarm = _prewarm(python, cwd=work_dir, env=env, result_path=prewarm_path)
        except Exception as exc:
            prewarm = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        result["prewarm_elapsed_seconds"] = round(time.monotonic() - prewarm_started, 3)
        result["prewarm"] = prewarm

        # The number the budget applies to: a fresh `aiad mcp` process, kernel never imported by it.
        try:
            probe = _mcp_probe(executable, work_dir=work_dir, env=env)
            probe["kernel_imports_absent"] = result["mcp"].get("kernel_imports_absent")
            result["mcp"] = probe
            if probe.get("status") != "pass":
                functional_errors.append(f"MCP initialize after pre-warm failed: {probe.get('error', 'unknown error')}")
        except Exception as exc:
            result["mcp"] = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
            functional_errors.append(f"MCP initialize after pre-warm failed: {type(exc).__name__}: {exc}")

        vtk_status = next(
            (item.get("status") for item in prewarm.get("modules", []) if item.get("name") == "vtk"),
            None,
        )
        if vtk_status != "pass":
            print("$ completing VTK import after the prewarm attempt before running doctor", flush=True)
            vtk_result = _provision_vtk(python, cwd=work_dir, env=env)
            result["provisioned_vtk_import"] = vtk_result
            if vtk_result.get("status") != "pass":
                functional_errors.append(f"Installed VTK import failed: {vtk_result.get('error', 'unknown error')}")

        doctor: dict[str, Any] | None = None
        try:
            doctor = _doctor(executable, cwd=work_dir, env=env)
            result["doctor"] = doctor
            if not doctor.get("functional_smoke_passed"):
                functional_errors.append(doctor.get("functional_error", "Installed aiad doctor smoke failed"))
        except Exception as exc:
            result["doctor_error"] = f"{type(exc).__name__}: {exc}"
            functional_errors.append(f"Installed aiad doctor failed: {type(exc).__name__}: {exc}")
        finally:
            stop = subprocess.run(
                [str(executable), "daemon", "stop", "--json"],
                cwd=work_dir,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30.0,
            )
            if stop.returncode != 0:
                functional_errors.append(f"aiad daemon stop exited {stop.returncode}: {stop.stderr.strip()[:300]}")
            result["daemon_cleanup"] = {
                "returncode": stop.returncode,
                "stdout": stop.stdout.strip(),
                "stderr": stop.stderr.strip(),
            }

        result["runtime_disk_bytes"] = _disk_usage_bytes([tool_dir, bin_dir])
        result["runtime_disk_after_validation_bytes"] = result["runtime_disk_bytes"]
        result["runtime_disk_decimal_gb"] = round(result["runtime_disk_bytes"] / 1_000_000_000, 3)
        result["runtime_disk_gib"] = round(result["runtime_disk_bytes"] / (1024**3), 3)
        result["budget"] = {
            "runtime_disk_target_bytes": RUNTIME_SIZE_LIMIT_BYTES,
            "install_time_target_seconds": INSTALL_TIME_LIMIT_SECONDS,
            "runtime_disk_within_target": result["runtime_disk_bytes"] <= RUNTIME_SIZE_LIMIT_BYTES,
            "install_time_within_target": install_elapsed < INSTALL_TIME_LIMIT_SECONDS,
            "install_time_measurement_kind": "fresh-empty-cache" if install_cache_was_empty else "provisioned-cache",
        }
        fresh_measurement = result.get("fresh_empty_cache_install_measurement")
        fresh_elapsed = fresh_measurement.get("install_elapsed_seconds") if fresh_measurement else None
        result["budget"]["fresh_empty_cache_install_time_within_target"] = (
            fresh_elapsed < INSTALL_TIME_LIMIT_SECONDS if isinstance(fresh_elapsed, (int, float)) else None
        )
        result["functional_errors"] = functional_errors
        if doctor is not None:
            result["doctor_warnings"] = [
                check
                for check in doctor.get("checks", [])
                if isinstance(check, dict) and check.get("status") in {"warn", "skip"}
            ]

        blockers = list(result["scope"]["release_blockers"])
        if not result.get("mcp", {}).get("within_startup_budget"):
            blockers.append(
                f"MCP initialize after pre-warm took {result.get('mcp', {}).get('initialize_seconds')} s, "
                "over the 1-second budget, which PLAN section 9 sets for the reference Mac"
            )
        if not result["budget"]["runtime_disk_within_target"]:
            blockers.append(
                f"Installed runtime is {result['runtime_disk_bytes']} bytes, over the 1,000,000,000-byte target"
            )
        if fresh_measurement and result["budget"]["fresh_empty_cache_install_time_within_target"] is False:
            blockers.append(
                f"Fresh empty-cache install took {fresh_elapsed:.3f} seconds, over the 180-second target"
            )
        if not prewarm.get("ok"):
            blockers.append("Cold runtime prewarm did not complete within the prewarm runner's 240-second budget")
        if not (ROOT / "packages" / "quality" / "pyproject.toml").is_file():
            blockers.append("aiad_quality is absent from the current workspace and is not verified by this packaging smoke")
        result["scope"]["release_blockers"] = blockers
        result["scope"]["quality_component"] = {
            "status": "available" if (ROOT / "packages" / "quality" / "pyproject.toml").is_file() else "not_present_in_workspace",
            "installed": "aiad-quality" in result.get("installed_artifact", {}).get("versions", {}),
            "verified": False,
        }
        result["status"] = "failed" if functional_errors else ("passed_with_release_blockers" if blockers else "passed")
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _write_json(result_path, result)
        print(f"Machine-readable result: {result_path}", flush=True)
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)

    # Budget misses are reported, not fatal, because PLAN section 9 sets budgets for the reference Mac.
    # They must stay visible on a green run, so each becomes a CI annotation.
    if os.environ.get("GITHUB_ACTIONS"):
        for blocker in result.get("scope", {}).get("release_blockers", []):
            print(f"::warning title=Release blocker::{blocker}", flush=True)
    return 0 if result["status"].startswith("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
