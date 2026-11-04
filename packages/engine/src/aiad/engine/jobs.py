"""Run each model script in a fresh, killable Python worker process."""

from __future__ import annotations

import json
import math
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import sandbox
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
    record on stdout. Every model process uses the OS sandbox launcher; an
    unavailable boundary fails closed. Memory limits, a warm pool, a revision
    store, and independent validation remain separate requirements.
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
        try:
            (job_dir / "part.brep").lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError("job_dir already contains part.brep; use a fresh job directory for each build")

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
    try:
        process = sandbox.popen(
            [sys.executable, "-I", "-B", "-c", _WORKER_BOOTSTRAP, str(_SOURCE_ROOT)],
            job_dir,
        )
    except (sandbox.SandboxUnavailable, OSError) as exc:
        return _failed(
            Failure(
                code="SANDBOX_DENIED",
                phase="sandbox",
                message=f"The build sandbox could not be started: {exc}",
                kernel_message=f"{type(exc).__name__}: {exc}",
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
        failure = Failure(
            code="WORKER_CRASH",
            phase="crash",
            message=f"The build worker exited with status {process.returncode} without returning a valid result; no exit cause was inferred.",
        )
        if response is None and process.returncode != 0 and any(
            line.startswith((b"bwrap:", b"sandbox-exec:")) for line in stderr_bytes.splitlines()
        ):
            failure = Failure(
                code="SANDBOX_DENIED",
                phase="sandbox",
                message=f"The sandbox launch returned no worker result (exit status {process.returncode}); inspect the retained launcher diagnostic.",
                kernel_message=_decode(stderr_bytes),
            )
        return _failed(
            failure,
            job_dir=job_dir,
            artifacts=_preserved_artifacts(job_dir),
            build_ms=elapsed_ms,
            stdout=_decode(stdout_bytes),
            stderr=_decode(stderr_bytes),
        )

    try:
        result = _result_from_wire(response, job_dir=job_dir, elapsed_ms=elapsed_ms, process_stderr=_decode(stderr_bytes))
        return _constrain_worker_evidence(result, job_dir, project_dir)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
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
            stdout=_decode(stdout_bytes),
            stderr=_decode(stderr_bytes),
        )


def _coerce_request(req: BuildRequest | Mapping[str, Any]) -> BuildRequest:
    if isinstance(req, BuildRequest):
        return req
    if isinstance(req, Mapping):
        return BuildRequest(**dict(req))
    raise TypeError("req must be a BuildRequest or a mapping of its fields")


def _canonical_artifact(path: Path, job_dir: Path) -> Path | None:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        if (resolved.is_relative_to(job_dir) and stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1 and info.st_size > 0):
            return resolved
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def _private_artifact(path: Path, job_dir: Path) -> bool:
    return _canonical_artifact(path, job_dir) is not None


def _detail_counts(checks: list[Check]) -> dict[str, int]:
    return {status: sum(c.status == status for c in checks)
            for status in ("pass", "fail", "warn", "skip")}


def _constrain_worker_evidence(result: BuildResult, job_dir: Path, project_dir: Path) -> BuildResult:
    # A model's pipe result is not a trusted validation receipt.
    original_counts = _detail_counts(result.checks)
    totals = {status: max(result.checks_summary.get(status, 0), count)
              for status, count in original_counts.items()}
    part = _canonical_artifact(job_dir / "part.brep", job_dir)
    if result.ok and part is None:
        result.ok, result.status = False, "failed"
        result.failure = Failure(
            code="WORKER_CRASH", phase="crash", feature="unknown", file="part.brep",
            message="The worker reported success without a private, non-empty part.brep artifact; the build was not accepted.",
        )
        result.summary = result.failure.message
        result.checks.append(Check(id="worker_artifact_required", suite="hard", level="error", status="fail",
                                   message="The required private BREP artifact was not available."))
    failed_check = next((c for c in result.checks if c.level == "error" and c.status == "fail"), None)
    if result.ok and failed_check is not None:
        result.ok, result.status = False, "failed"
        result.failure = Failure(code="NOT_VERIFIED", phase="validate", feature=failed_check.feature or "unknown",
                                message=f"The worker reported a failed error-level check ({failed_check.id}); success was not accepted.")
        result.summary = result.failure.message
    artifacts: dict[str, str] = {}
    rejected = 0
    for name, value in result.artifacts.items():
        if not isinstance(value, str):
            rejected += 1
            continue
        path = Path(value)
        if not path.is_absolute():
            path = job_dir / path
        canonical = _canonical_artifact(path, job_dir)
        if canonical is not None:
            artifacts[name] = str(canonical)
        else:
            rejected += 1
    if part is not None:
        artifacts["part.brep"] = str(part)
    result.artifacts = artifacts
    result.requirements_proven = False
    if result.context is not None:
        result.context.job_dir = job_dir
        result.context.project_dir = project_dir
    if rejected:
        result.checks.append(Check(
            id="worker_artifacts_rejected", suite="hard", level="warn", status="warn",
            message="Worker artifact references did not pass the private job-file checks and were omitted.",
            actual=str(rejected),
        ))
    if result.status == "verified":
        result.status = "draft"
        result.summary = "A BREP artifact was returned; independent verification of the worker evidence is pending."
        result.checks.append(Check(
            id="worker_verification_unattested", suite="hard", level="error", status="skip",
            message="The model process cannot attest its own verified status. A separate trusted validator is required.",
        ))
    changed_counts = _detail_counts(result.checks)
    result.checks_summary = {status: totals[status] + changed_counts[status] - original_counts[status]
                             for status in totals}
    if result.scene is not None:
        result.scene.status = "draft"
        result.scene.compare = None
        result.scene.checks_summary = dict(result.checks_summary)
    return result


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
    # remain bounded even when the leader has already exited.
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
    if not isinstance(wire.get("ok"), bool) or wire.get("status") not in ("failed", "draft", "verified"):
        raise ValueError("worker result has invalid success/status fields")
    if wire["ok"] != (wire["status"] != "failed"):
        raise ValueError("worker result has inconsistent success/status fields")
    checks = [_check_from_wire(item) for item in _object_list(wire.get("checks", []), "checks")]
    summary_data = wire.get("checks_summary", {})
    if not isinstance(summary_data, dict):
        raise ValueError("checks_summary must be an object")
    summary = {}
    for status in ("pass", "fail", "warn", "skip"):
        count = summary_data.get(status, 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("checks_summary counts must be non-negative integers")
        summary[status] = count
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
        checks_summary=summary,
        failure=failure,
        artifacts=dict(wire.get("artifacts", {})),
        scene=scene,
        job_dir=job_dir,
        build_ms=float(wire.get("build_ms", elapsed_ms)),
        stdout=str(wire.get("stdout", "")),
        stderr=str(stderr),
        requirements_proven=bool(wire.get("requirements_proven", False)),
    )


def _object_list(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must be a list of objects")
    return value


def _check_from_wire(item: dict[str, Any]) -> Check:
    if not isinstance(item, dict):
        raise ValueError("check must be an object")
    if item.get("suite", "hard") not in ("hard", "spec", "process", "interference", "2d"):
        raise ValueError("check has an unsupported suite")
    if item.get("level", "warn") not in ("error", "warn") or item.get("status", "skip") not in ("pass", "fail", "warn", "skip"):
        raise ValueError("check has an unsupported level or status")
    if not isinstance(item.get("id"), str) or not item["id"]:
        raise ValueError("check requires a nonempty string ID")
    for field in ("message", "expected", "actual", "feature", "selector", "assumptions", "fix_hint"):
        if item.get(field) is not None and not isinstance(item[field], str):
            raise ValueError(f"check {field} must be text or null")
    if item.get("heuristic") is not None and not isinstance(item["heuristic"], bool):
        raise ValueError("check heuristic must be boolean or null")
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
        features=[_feature_from_wire(feature) for feature in _object_list(item.get("features", []), "features")],
        params=dict(item.get("params", {})),
        metrics=dict(item.get("metrics", {})),
        spec=dict(item.get("spec", {})),
        material=item.get("material"),
        process=item.get("process"),
        job_dir=Path(item.get("job_dir", ".")),
    )


def _feature_from_wire(item: dict[str, Any]) -> FeatureInfo:
    return FeatureInfo(
        name=item.get("name", "main"),
        faces=[_entity_from_wire(entity) for entity in _object_list(item.get("faces", []), "feature faces")],
        edges=[_entity_from_wire(entity) for entity in _object_list(item.get("edges", []), "feature edges")],
        line_start=item.get("line_start"),
        line_end=item.get("line_end"),
        deps=list(item.get("deps", [])),
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
        candidates=[Candidate(**candidate) for candidate in _object_list(item.get("candidates", []), "failure candidates")],
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
    brep_path = _canonical_artifact(job_dir / "part.brep", job_dir)
    return {"part.brep": str(brep_path)} if brep_path is not None else {}


def _decode(value: bytes | None) -> str:
    return (value or b"").decode("utf-8", errors="replace")


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
