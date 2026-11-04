"""Run each model script in a fresh, killable Python worker process."""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .types import (
    BuildRequest,
    BuildResult,
    Candidate,
    Check,
    Entity,
    Failure,
    FeatureInfo,
    ModelContext,
    SceneManifest,
)

_RESULT_MARKER = b"AIAD_ENGINE_RESULT:"
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_WORKER_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from aiad.engine._worker import main; main()"
)


def run_build(req: BuildRequest) -> BuildResult:
    """Build a model in a new subprocess and return its checks and artifacts.

    The worker receives one JSON request on stdin and returns one marked JSON
    record on stdout. Model code never executes in this process. This bootstrap
    does not provide an OS sandbox, memory limit, warm pool, or revision store.
    """
    try:
        request = _coerce_request(req)
        _validate_request(request)
        project_dir = Path(request.project_dir).expanduser().resolve()
        if request.job_dir is None:
            job_dir = Path(tempfile.mkdtemp(prefix="aiad-engine-job-")).resolve()
        else:
            raw_job_dir = Path(request.job_dir).expanduser()
            job_dir = (project_dir / raw_job_dir if not raw_job_dir.is_absolute() else raw_job_dir).resolve()
            job_dir.mkdir(parents=True, exist_ok=True)
        if not job_dir.is_dir():
            raise ValueError("job_dir is not a directory")

        payload = {
            "source": request.source,
            "project_dir": str(project_dir),
            "job_dir": str(job_dir),
            "rev": request.rev,
            "kind": request.kind,
            "units": request.units,
            "params": request.params,
            "spec": request.spec,
            "material": request.material,
            "process": request.process,
            "preview": request.preview,
        }
        input_bytes = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        return _failed(
            Failure(
                code="BAD_INPUT",
                phase="input",
                message=f"Build request is invalid: {exc}",
            )
        )

    started = time.perf_counter()
    popen_options: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": str(job_dir),
        "close_fds": True,
    }
    if os.name == "posix":
        popen_options["start_new_session"] = True

    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", _WORKER_BOOTSTRAP, str(_SOURCE_ROOT)],
            **popen_options,
        )
    except OSError as exc:
        return _failed(
            Failure(
                code="WORKER_CRASH",
                phase="crash",
                message="The build worker could not be started.",
                kernel_message=str(exc),
            ),
            job_dir=job_dir,
            build_ms=_elapsed_ms(started),
        )

    try:
        stdout_bytes, stderr_bytes = process.communicate(input=input_bytes, timeout=request.timeout_s)
    except subprocess.TimeoutExpired:
        _kill_worker(process)
        stdout_bytes, stderr_bytes = _drain_killed_worker(process)
        return _failed(
            Failure(
                code="TIMEOUT",
                phase="timeout",
                message=f"The build worker exceeded the {request.timeout_s:g} second timeout and was killed.",
            ),
            job_dir=job_dir,
            artifacts=_preserved_artifacts(job_dir),
            build_ms=_elapsed_ms(started),
            stdout=_decode(stdout_bytes),
            stderr=_decode(stderr_bytes),
        )

    elapsed_ms = _elapsed_ms(started)
    response = _find_response(stdout_bytes)
    if process.returncode != 0 or response is None:
        return _failed(
            Failure(
                code="WORKER_CRASH",
                phase="crash",
                message="The build worker exited without returning a valid result; no exit cause was inferred.",
            ),
            job_dir=job_dir,
            artifacts=_preserved_artifacts(job_dir),
            build_ms=elapsed_ms,
            stdout="",
            stderr=_decode(stderr_bytes),
        )

    try:
        return _result_from_wire(response, job_dir=job_dir, elapsed_ms=elapsed_ms, process_stderr=_decode(stderr_bytes))
    except (KeyError, TypeError, ValueError) as exc:
        return _failed(
            Failure(
                code="WORKER_CRASH",
                phase="crash",
                message="The build worker returned a result that could not be decoded.",
                kernel_message=str(exc),
            ),
            job_dir=job_dir,
            artifacts=_preserved_artifacts(job_dir),
            build_ms=elapsed_ms,
            stderr=_decode(stderr_bytes),
        )


def _coerce_request(req: BuildRequest | Mapping[str, Any]) -> BuildRequest:
    if isinstance(req, BuildRequest):
        return req
    if isinstance(req, Mapping):
        return BuildRequest(**dict(req))
    raise TypeError("req must be a BuildRequest or a mapping of its fields")


def _validate_request(req: BuildRequest) -> None:
    if not isinstance(req.source, str):
        raise TypeError("source must be text")
    if req.kind not in {"part", "assembly", "2d"}:
        raise ValueError("kind must be 'part', 'assembly', or '2d'")
    if req.kind == "2d":
        raise ValueError("the M0 worker currently builds 3D B-rep models only")
    if req.units not in {"mm", "in"}:
        raise ValueError("units must be 'mm' or 'in'")
    if isinstance(req.rev, bool) or not isinstance(req.rev, int) or req.rev < 0:
        raise ValueError("rev must be a non-negative integer")
    if isinstance(req.timeout_s, bool) or not isinstance(req.timeout_s, (int, float)):
        raise TypeError("timeout_s must be a positive number")
    if not math.isfinite(float(req.timeout_s)) or req.timeout_s <= 0:
        raise ValueError("timeout_s must be a finite positive number")
    if not isinstance(req.params, dict):
        raise TypeError("params must be a dictionary")
    if not isinstance(req.spec, dict):
        raise TypeError("spec must be a dictionary")
    if not isinstance(req.preview, bool):
        raise TypeError("preview must be a boolean")
    for name in ("material", "process"):
        value = getattr(req, name)
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{name} must be text or None")
    if not isinstance(req.project_dir, (str, os.PathLike)):
        raise TypeError("project_dir must be a path")
    if req.job_dir is not None and not isinstance(req.job_dir, (str, os.PathLike)):
        raise TypeError("job_dir must be a path or None")
    # Validate JSON compatibility here so bad request values are reported as
    # input errors instead of looking like a child-process crash.
    json.dumps({"params": req.params, "spec": req.spec}, allow_nan=False)


def _kill_worker(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            # The leader can exit while descendants still hold its pipes.
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass


def _drain_killed_worker(process: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    # A descendant can leave the worker's process group. Pipe cleanup must
    # remain bounded even before the OS sandbox is introduced.
    try:
        return process.communicate(timeout=1.0)
    except subprocess.TimeoutExpired as exc:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        return exc.output or b"", exc.stderr or b""


def _find_response(stdout: bytes) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if not line.startswith(_RESULT_MARKER):
            continue
        try:
            value = json.loads(line[len(_RESULT_MARKER) :].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("protocol") == 1 and isinstance(value.get("result"), dict):
            return value["result"]
    return None


def _result_from_wire(
    wire: dict[str, Any], *, job_dir: Path, elapsed_ms: float, process_stderr: str
) -> BuildResult:
    checks = [_check_from_wire(item) for item in wire.get("checks", [])]
    context_data = wire.get("context")
    context = _context_from_wire(context_data) if isinstance(context_data, dict) else None
    scene_data = wire.get("scene")
    scene = SceneManifest(**scene_data) if isinstance(scene_data, dict) else None
    failure_data = wire.get("failure")
    failure = _failure_from_wire(failure_data) if isinstance(failure_data, dict) else None
    stderr = wire.get("stderr", "")
    if process_stderr:
        stderr = f"{stderr}\n{process_stderr}" if stderr else process_stderr
    return BuildResult(
        ok=bool(wire.get("ok", False)),
        status=wire.get("status", "failed"),
        summary=str(wire.get("summary", "")),
        context=context,
        metrics=dict(wire.get("metrics", {})),
        checks=checks,
        checks_summary=dict(wire.get("checks_summary", {})),
        failure=failure,
        artifacts=dict(wire.get("artifacts", {})),
        scene=scene,
        job_dir=job_dir,
        build_ms=float(wire.get("build_ms", elapsed_ms)),
        stdout=str(wire.get("stdout", "")),
        stderr=str(stderr),
        requirements_proven=bool(wire.get("requirements_proven", False)),
    )


def _check_from_wire(item: dict[str, Any]) -> Check:
    return Check(
        id=item.get("id", ""),
        suite=item.get("suite", "hard"),
        level=item.get("level", "warn"),
        status=item.get("status", "skip"),
        message=item.get("message", ""),
        expected=item.get("expected"),
        actual=item.get("actual"),
        feature=item.get("feature"),
        selector=item.get("selector"),
        heuristic=item.get("heuristic"),
        assumptions=item.get("assumptions"),
        fix_hint=item.get("fix_hint"),
    )


def _context_from_wire(item: dict[str, Any]) -> ModelContext:
    return ModelContext(
        project_dir=Path(item.get("project_dir", ".")),
        rev=int(item.get("rev", 0)),
        kind=item.get("kind", "part"),
        units=item.get("units", "mm"),
        # The parent intentionally does not deserialize a kernel shape.
        shape=None,
        sheet=None,
        features=[_feature_from_wire(feature) for feature in item.get("features", [])],
        params=dict(item.get("params", {})),
        metrics=dict(item.get("metrics", {})),
        spec=dict(item.get("spec", {})),
        material=item.get("material"),
        process=item.get("process"),
        job_dir=Path(item.get("job_dir", ".")),
        meta=dict(item.get("meta", {})),
        param_definitions=dict(item.get("param_definitions", {})),
        part_name=item.get("part_name"),
    )


def _feature_from_wire(item: dict[str, Any]) -> FeatureInfo:
    return FeatureInfo(
        name=item.get("name", "main"),
        faces=[_entity_from_wire(entity) for entity in item.get("faces", [])],
        edges=[_entity_from_wire(entity) for entity in item.get("edges", [])],
        line_start=item.get("line_start"),
        line_end=item.get("line_end"),
        deps=list(item.get("deps", [])),
        intent=item.get("intent"),
        vertices=[_entity_from_wire(entity) for entity in item.get("vertices", [])],
    )


def _entity_from_wire(item: dict[str, Any]) -> Entity:
    return Entity(
        kind=item.get("kind", "part"),
        index=item.get("index"),
        feature=item.get("feature"),
        selector=item.get("selector"),
        geometry=dict(item.get("geometry", {})),
    )


def _failure_from_wire(item: dict[str, Any]) -> Failure:
    return Failure(
        code=item.get("code", "WORKER_CRASH"),
        phase=item.get("phase", "crash"),
        message=item.get("message", ""),
        kernel_message=item.get("kernel_message"),
        feature=item.get("feature"),
        file=item.get("file"),
        line=item.get("line"),
        selector=item.get("selector"),
        candidates=[Candidate(**candidate) for candidate in item.get("candidates", [])],
        probes=list(item.get("probes", [])),
        repair=list(item.get("repair", [])),
        last_good_rev=item.get("last_good_rev"),
    )


def _failed(
    failure: Failure,
    *,
    job_dir: Path | None = None,
    artifacts: dict[str, str] | None = None,
    build_ms: float = 0.0,
    stdout: str = "",
    stderr: str = "",
) -> BuildResult:
    return BuildResult(
        ok=False,
        status="failed",
        summary=failure.message,
        failure=failure,
        artifacts=dict(artifacts or {}),
        job_dir=job_dir,
        build_ms=build_ms,
        stdout=stdout,
        stderr=stderr,
    )


def _preserved_artifacts(job_dir: Path) -> dict[str, str]:
    brep_path = job_dir / "part.brep"
    return {"part.brep": str(brep_path)} if brep_path.is_file() else {}


def _decode(value: bytes | None) -> str:
    return (value or b"").decode("utf-8", errors="replace")


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
