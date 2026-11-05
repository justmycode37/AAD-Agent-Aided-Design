"""Runs one post-build operation in a fresh, killable kernel process."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..ops import RESULT_MARKER

DEFAULT_TIMEOUT_S = 120.0
# VTK aborts inside macOS Seatbelt, which denies its graphics access (issue #56). These ops read a
# BREP the sandboxed worker wrote and run no model code, with a clean environment and no secrets.
# That leaves OCCT's BREP parser outside the sandbox, so remote mode refuses them.
NEEDS_GRAPHICS = frozenset({"render"})


def _package_roots() -> list[Path]:
    """Editable installs import from each package's src dir, which Python must be able to list."""
    roots = []
    for name in ("aiad_platform", "aiad_render", "aiad_drafting", "aiad_quality"):
        spec = importlib.util.find_spec(name)
        if spec is not None and spec.origin is not None:
            roots.append(Path(spec.origin).parents[1])
    return roots


def _run_sandboxed(request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    """Blocking. One kernel process inside the OS sandbox, killed with its descendants on timeout."""
    try:
        from aiad.engine import sandbox
    except ImportError as exc:
        return _crash("WORKER_CRASH", "crash", f"The AIAD engine is not installed: {exc}. Run aiad doctor.")
    out = Path(request["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    reads = [*(Path(path) for path in request.get("read_paths", [])), *_package_roots()]
    argv = [sys.executable, "-m", "aiad_platform.ops"]
    try:
        if request.get("op") in NEEDS_GRAPHICS:
            if os.environ.get("AIAD_REMOTE"):
                raise sandbox.SandboxUnavailable("rendering needs graphics access that remote mode does not grant")
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
        else:
            process = sandbox.popen(argv, out, read_paths=reads)
    except (sandbox.SandboxUnavailable, OSError) as exc:
        # Never fall back to an unsandboxed run.
        return _crash("SANDBOX_DENIED", "sandbox", f"The OS sandbox could not start this operation: {exc}")
    try:
        stdout, stderr = process.communicate(json.dumps(request, default=str).encode(), timeout=timeout_s)
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
        "read_paths": [str(project.path / ".aiad" / "revs"), *_extra_reads(args)],
    }


def _extra_reads(args: dict[str, Any]) -> list[str]:
    """Import reads one staged file, and nothing else outside the revisions."""
    path = args.get("path")
    return [str(path)] if path else []
