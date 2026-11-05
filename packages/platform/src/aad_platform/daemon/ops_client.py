"""Runs one post-build operation in a fresh, killable kernel process."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..ops import RESULT_MARKER, _render_result

DEFAULT_TIMEOUT_S = 120.0


def _package_roots() -> list[Path]:
    """Editable installs import from each package's src dir, which Python must be able to list."""
    roots = []
    for name in ("aad_platform", "aad_render", "aad_drafting", "aad_quality"):
        spec = importlib.util.find_spec(name)
        if spec is not None and spec.origin is not None:
            roots.append(Path(spec.origin).parents[1])
            if name == "aad_quality":
                from aad_quality.catalog import _guidance_root

                # Editable installs keep playbooks outside src; workers need the
                # same read-only guidance that is bundled into release wheels.
                roots.append(Path(str(_guidance_root())))
                # The catalog checks this marker to locate editable guidance.
                # Linux mount namespaces also need the file for is_file(), while
                # macOS already grants file-read-metadata outside read subpaths.
                marker = Path(spec.origin).parents[2] / "pyproject.toml"
                if marker.is_file():
                    roots.append(marker)
    return roots


def _run_sandboxed(request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    """Blocking post-build operation; only the final macOS VTK renderer runs outside Seatbelt."""
    try:
        from aad.engine import sandbox
    except ImportError as exc:
        return _crash("WORKER_CRASH", "crash", f"The AAD engine is not installed: {exc}. Run aad doctor.")

    if request.get("op") == "render":
        if os.environ.get("AAD_REMOTE"):
            return _crash("SANDBOX_DENIED", "sandbox", "rendering needs a local graphics worker that remote mode does not grant")
        if sys.platform == "darwin" and request.get("kind", "part") != "2d":
            return _run_macos_mesh_render(request, timeout_s, sandbox)

    return _run_worker(request, timeout_s, sandbox)


def _run_worker(request: dict[str, Any], timeout_s: float, sandbox: Any) -> dict[str, Any]:
    """Run an operation under the unchanged OS policy, with the usual timeout and kill behavior."""
    out = Path(request["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    reads = [*(Path(path) for path in request.get("read_paths", [])), *_package_roots()]
    argv = [sys.executable, "-m", "aad_platform.ops"]
    try:
        process = sandbox.popen(argv, out, read_paths=reads)
    except (sandbox.SandboxUnavailable, OSError) as exc:
        # Never fall back to an unsandboxed run.
        return _crash("SANDBOX_DENIED", "sandbox", f"The OS sandbox could not start this operation: {exc}")
    return _communicate_result(process, json.dumps(request, default=str).encode(), timeout_s)


def _run_macos_mesh_render(request: dict[str, Any], timeout_s: float, sandbox: Any) -> dict[str, Any]:
    """Parse the B-rep under Seatbelt, then give a clean VTK worker render data only."""
    deadline = time.monotonic() + timeout_s
    prepare_request = {**request, "op": "render_prepare"}
    prepared_result = _run_worker(prepare_request, timeout_s, sandbox)
    if not prepared_result.get("ok"):
        return prepared_result
    prepared = prepared_result.get("prepared_render")
    if not isinstance(prepared, dict) or not isinstance(prepared.get("render"), dict):
        return _crash("WORKER_CRASH", "render", "The sandboxed worker returned no prepared mesh payload.")

    out = Path(request["out_dir"])
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _crash("TIMEOUT", "timeout", f"The operation did not finish within {timeout_s:.0f} s.")
    argv = [sys.executable, "-m", "aad_platform.render_mesh", str(out)]
    try:
        process = subprocess.Popen(
            argv,
            cwd=out,
            env=sandbox.environment(out),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        return _crash("WORKER_CRASH", "render", f"The mesh renderer could not start: {exc}")
    rendered_result = _communicate_result(
        process,
        json.dumps({"render": prepared["render"]}, separators=(",", ":")).encode(),
        remaining,
    )
    if not rendered_result.get("ok"):
        return rendered_result
    rendered = rendered_result.get("rendered")
    if not isinstance(rendered, dict):
        return _crash("WORKER_CRASH", "render", "The mesh renderer returned no image metadata.")
    try:
        from aad_render import finish_render

        paths = finish_render(prepared, rendered, out)
        return {"ok": True, **_render_result(paths)}
    except Exception as exc:
        return _crash("WORKER_CRASH", "render", f"The render report could not be completed: {exc}")


def _communicate_result(
    process: subprocess.Popen[bytes], input_data: bytes, timeout_s: float
) -> dict[str, Any]:
    try:
        stdout, stderr = process.communicate(input_data, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill(process)
        process.communicate()
        return _crash("TIMEOUT", "timeout", f"The operation did not finish within {timeout_s:.0f} s.")
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER) :])
    detail = stderr.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
    return _crash("WORKER_CRASH", "crash", f"The worker exited with code {process.returncode}: {detail[0]}")


async def run_op(request: dict[str, Any], *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    return await asyncio.to_thread(_run_sandboxed, request, timeout_s)


def _kill(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _crash(code: str, phase: str, message: str) -> dict[str, Any]:
    return {"ok": False, "failure": {"code": code, "phase": phase, "message": message}}


def job_request(project: Any, rev: int, op: str, args: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    meta = project.meta()
    read_paths = [str(project.path / ".aad" / "revs"), *_extra_reads(args)]
    if op == "check":
        read_paths.extend(_spec_reference_reads(project, rev, args))
    return {
        "op": op,
        "project_dir": str(project.path),
        "project": project.name,
        "rev": rev,
        "kind": meta.get("kind", "part"),
        "units": meta.get("units", "mm"),
        "material": meta.get("material"),
        "process": meta.get("process"),
        "args": args,
        "out_dir": str(out_dir),
        "read_paths": list(dict.fromkeys(read_paths)),
    }


def _extra_reads(args: dict[str, Any]) -> list[str]:
    """Operations read one explicitly selected file, and nothing else outside revisions."""
    path = args.get("path")
    reference = args.get("reference_path")
    reads = [str(path)] if path else []
    if reference:
        try:
            resolved = Path(reference).expanduser().resolve(strict=True)
            if resolved.is_file():
                reads.append(str(resolved))
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    return reads


def _spec_reference_reads(project: Any, rev: int, args: dict[str, Any]) -> list[str]:
    # An empty list means the default suites, which include spec.
    suites = args.get("suites")
    if suites and "spec" not in suites:
        return []
    spec_path = project.path / ".aad" / "revs" / f"{rev:06d}" / "spec.yaml"
    try:
        if spec_path.stat().st_size > 1_048_576:
            return []
        from aad_quality.compare import resolve_project_reference
        from aad_quality.schema import load_spec

        document = load_spec(spec_path.read_bytes())
    except Exception:
        return []
    requirements = document.get("requirements")
    if not isinstance(requirements, list):
        return []
    result: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        body = requirement.get("matches_reference")
        if not isinstance(body, dict):
            continue
        try:
            result.add(str(resolve_project_reference(project.path, body.get("path"))))
        except Exception:
            # The evaluator emits a visible skip; unsafe paths never enter the sandbox allowlist.
            continue
    return sorted(result)
