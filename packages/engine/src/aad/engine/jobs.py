"""Run each model script in a fresh, killable Python worker process."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import memory, sandbox
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

_MAX_ASSET_BYTES = 256 * 1024 * 1024
_RESULT_MARKER = b"AAD_ENGINE_RESULT:"
_STARTED_MARKER = b"AAD_ENGINE_STARTED\n"
_VALIDATION_TIMEOUT_S = 600.0
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_WORKER_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from aad.engine import memory; _limit=sys.argv.pop(1); "
    "memory.install_worker_memory_budget(int(_limit)) if _limit != '-' else None; "
    "from aad.engine._worker import main; "
    f"sys.stdout.buffer.write({_STARTED_MARKER!r}); sys.stdout.buffer.flush(); main()"
)


def run_build(req: BuildRequest, *, memory_limit_bytes: int | None = None) -> BuildResult:
    """Build a model in a new subprocess and return its checks and artifacts.

    The worker receives one JSON request on stdin and returns one marked JSON
    record on stdout. The model process installs its address-space growth budget
    before importing worker/kernel code. After it exits, a separate trusted
    process reloads the saved BREP, validates it, and creates the viewer scene
    from that same loaded snapshot under the same configured memory budget.
    Unsupported sandbox or memory boundaries fail closed.
    """
    try:
        request = _coerce_request(req)
        _validate_request(request)
        project_dir = Path(request.project_dir).expanduser().resolve()
        if request.job_dir is None:
            job_dir = Path(tempfile.mkdtemp(prefix="aad-engine-job-")).resolve()
        else:
            raw_job_dir = Path(request.job_dir).expanduser()
            job_dir = (
                project_dir / raw_job_dir
                if not raw_job_dir.is_absolute()
                else raw_job_dir
            ).resolve()
            job_dir.mkdir(parents=True, exist_ok=True)
        if not job_dir.is_dir():
            raise ValueError("job_dir is not a directory")
        try:
            (job_dir / "part.brep").lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError(
                "job_dir already contains part.brep; use a fresh job directory for each build"
            )

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
            "defer_validation": True,
            # Optional native rendering must not abort or hang this process
            # after it has already produced a usable BREP.
            "preview": False,
        }
        input_bytes = (
            json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")
        if re.search(r"\basset\b", request.source):
            _stage_assets(project_dir, job_dir)
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
        if memory_limit_bytes is None:
            if sys.platform == "darwin" or sys.platform.startswith("linux"):
                memory_limit_bytes = memory.default_memory_budget_bytes()
        else:
            memory_limit_bytes = memory.memory_budget_bytes(memory_limit_bytes)
        startup_limit = (
            memory.bootstrap_address_space_budget(memory_limit_bytes)
            if memory_limit_bytes is not None else None
        )
        process = sandbox.popen(
            [sys.executable, "-I", "-B", "-c", _WORKER_BOOTSTRAP, str(_SOURCE_ROOT),
             str(memory_limit_bytes) if memory_limit_bytes is not None else "-"],
            job_dir,
            read_paths=_reference_read_paths(project_dir, request.spec),
            address_space=startup_limit,
        )
    except (sandbox.SandboxUnavailable, memory.AddressSpaceUnavailable, OSError) as exc:
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
    except (TypeError, ValueError) as exc:
        return _failed(
            Failure(code="BAD_INPUT", phase="input", message=f"Build memory policy is invalid: {exc}"),
            job_dir=job_dir,
            build_ms=_elapsed_ms(started),
        )

    try:
        stdout_bytes, stderr_bytes = process.communicate(
            input=input_bytes, timeout=request.timeout_s
        )
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
    except BaseException:
        # A caller can interrupt a build before its own deadline (for example,
        # a cancelled request or a test-runner timeout). Do not leave the
        # sandboxed worker running in that case.
        try:
            _kill_worker(process)
        except OSError:
            pass
        try:
            _drain_killed_worker(process)
        except Exception:
            pass
        raise

    elapsed_ms = _elapsed_ms(started)
    response = _find_response(stdout_bytes)
    if process.returncode != 0 or response is None:
        worker_started = _STARTED_MARKER in stdout_bytes.splitlines(keepends=True)
        failure = Failure(
            code="WORKER_CRASH",
            phase="crash",
            message=f"The build worker exited with status {process.returncode} without returning a valid result; no exit cause was inferred.",
        )
        if not worker_started and memory.has_setup_failure_marker(stdout_bytes):
            failure = Failure(
                code="SANDBOX_DENIED",
                phase="sandbox",
                message="The configured engine.memory address-space limit could not be installed; model code was not run.",
                kernel_message=_decode(stderr_bytes),
            )
        elif not worker_started and response is None and memory.is_memory_exhaustion_output(stderr_bytes):
            failure = Failure(
                code="OUT_OF_MEMORY",
                phase="oom",
                message="The build worker could not allocate memory during startup under the configured engine.memory limit.",
                kernel_message=_decode(stderr_bytes),
            )
        elif (
            not worker_started
            and response is None
            and process.returncode != 0
            and any(
                line.startswith((b"bwrap:", b"sandbox-exec:"))
                for line in stderr_bytes.splitlines()
            )
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
        result = _result_from_wire(
            response,
            job_dir=job_dir,
            elapsed_ms=elapsed_ms,
            process_stderr=_decode(stderr_bytes),
        )
        # This is a parent-clock measurement, not the model worker's JSON field.
        result.build_ms = elapsed_ms
        result = _constrain_worker_evidence(result, job_dir, project_dir, kind=request.kind,
                                            spec=request.spec, process=request.process, units=request.units,
                                            rev=request.rev, timeout_s=_VALIDATION_TIMEOUT_S,
                                            memory_limit_bytes=memory_limit_bytes)
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

    preview_started = time.perf_counter()
    if request.preview and "part.brep" in result.artifacts:
        from ._preview import add_preview

        remaining_s = max(0.0, request.timeout_s - (time.perf_counter() - started))
        add_preview(result, timeout_s=remaining_s, memory_limit_bytes=memory_limit_bytes)
    elapsed_ms = _elapsed_ms(started)
    result.build_ms = elapsed_ms
    _finish_phase_timings(
        result,
        elapsed_ms,
        preview_ms=_elapsed_ms(preview_started) if request.preview else 0.0,
    )
    return result


def _finish_phase_timings(
    result: BuildResult, elapsed_ms: float, *, preview_ms: float = 0.0
) -> None:
    if not isinstance(result.metrics, dict):
        result.metrics = {}
    result.metrics["build_ms"] = elapsed_ms
    if result.context is not None:
        result.context.metrics["build_ms"] = elapsed_ms
    timings = result.metrics.get("phase_timings_ms")
    if not isinstance(timings, dict) or any(
        not isinstance(name, str)
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
        for name, value in timings.items()
    ):
        return
    normalized = {name: float(value) for name, value in timings.items()}
    if preview_ms > 0:
        normalized["render"] = normalized.get("render", 0.0) + preview_ms
    accounted_ms = sum(normalized.values())
    normalized["other"] = max(0.0, elapsed_ms - accounted_ms)
    normalized = {
        name: round(milliseconds, 3) for name, milliseconds in normalized.items()
    }
    result.metrics["build_ms"] = elapsed_ms
    result.metrics["phase_timings_ms"] = normalized
    if result.context is not None:
        result.context.metrics["build_ms"] = elapsed_ms
        result.context.metrics["phase_timings_ms"] = normalized
    if elapsed_ms > 20_000 and normalized:
        slowest, milliseconds = max(normalized.items(), key=lambda item: item[1])
        result.summary = (
            f"{result.summary} Slowest phase: {slowest} ({milliseconds / 1000:.1f} s)."
        )


def _coerce_request(req: BuildRequest | Mapping[str, Any]) -> BuildRequest:
    if isinstance(req, BuildRequest):
        return req
    if isinstance(req, Mapping):
        return BuildRequest(**dict(req))
    raise TypeError("req must be a BuildRequest or a mapping of its fields")


def _stage_assets(project_dir: Path, job_dir: Path) -> None:
    """Copy regular files from the project's imports into the job, the sandbox's only readable project data."""
    imports = project_dir / "imports"
    if imports.is_symlink() or not imports.is_dir():
        return
    target = job_dir / "assets"
    target.mkdir(exist_ok=True)
    for entry in imports.iterdir():
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
            continue
        if entry.stat().st_size <= _MAX_ASSET_BYTES:
            shutil.copyfile(entry, target / entry.name, follow_symlinks=False)


def _reference_read_paths(
    project_dir: Path, spec: Mapping[str, Any]
) -> tuple[Path, ...]:
    """Grant the checker read access to only safe project-contained reference files."""
    requirements = spec.get("requirements") if isinstance(spec, Mapping) else None
    if not isinstance(requirements, list):
        return ()
    try:
        root = Path(project_dir).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return ()
    allowed_extensions = {".step", ".stp", ".stl", ".ply", ".obj"}
    max_bytes = 256 * 1024 * 1024
    read_paths: set[Path] = set()
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            continue
        body = requirement.get("matches_reference")
        if not isinstance(body, Mapping):
            continue
        raw_path = body.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        candidate = root
        unsafe = False
        for component in relative.parts:
            if component in {"", "."}:
                continue
            candidate = candidate / component
            try:
                if candidate.is_symlink():
                    unsafe = True
                    break
            except OSError:
                unsafe = True
                break
        if unsafe:
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            info = resolved.stat()
        except (OSError, RuntimeError, ValueError):
            continue
        if (
            resolved.is_file()
            and resolved.suffix.lower() in allowed_extensions
            and 0 < info.st_size <= max_bytes
        ):
            read_paths.add(resolved)
    return tuple(sorted(read_paths, key=str))


def _canonical_artifact(path: Path, job_dir: Path) -> Path | None:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        if (
            resolved.is_relative_to(job_dir)
            and stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_size > 0
        ):
            return resolved
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def _private_artifact(path: Path, job_dir: Path) -> bool:
    return _canonical_artifact(path, job_dir) is not None


def _detail_counts(checks: list[Check]) -> dict[str, int]:
    return {
        status: sum(c.status == status for c in checks)
        for status in ("pass", "fail", "warn", "skip", "not_applicable")
    }


def _constrain_worker_evidence(result: BuildResult, job_dir: Path, project_dir: Path, *, kind: str = "part",
                               spec: Mapping[str, Any] | None = None, process: str | None = None,
                               units: str = "mm", rev: int = 0, timeout_s: float = _VALIDATION_TIMEOUT_S,
                               memory_limit_bytes: int | None = None) -> BuildResult:
    # A model's pipe result is not a trusted validation receipt.
    worker_claimed_verified = result.status == "verified"
    worker_phase_timings = (
        dict(result.metrics["phase_timings_ms"])
        if isinstance(result.metrics, dict)
        and isinstance(result.metrics.get("phase_timings_ms"), dict)
        else None
    )
    result.checks = []
    result.checks_summary = {}
    result.metrics = {}
    result.scene = None
    _remove_candidate_scene(job_dir / "scene")
    # A sheet has no B-rep body: its source record is the artifact that must exist.
    required = "sheet.json" if kind == "2d" else "part.brep"
    part = _canonical_artifact(job_dir / required, job_dir)
    if result.ok and part is None:
        result.ok, result.status = False, "failed"
        result.failure = Failure(
            code="WORKER_CRASH",
            phase="crash",
            feature="unknown",
            file=required,
            message=f"The worker reported success without a private, non-empty {required} artifact; the build was not accepted.",
        )
        result.summary = result.failure.message
        result.checks.append(
            Check(
                id="worker_artifact_required",
                suite="hard",
                level="error",
                status="fail",
                message=f"The required private {required} artifact was not available.",
            )
        )
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
        artifacts[required] = str(part)
    result.artifacts = artifacts
    result.requirements_proven = False
    if result.context is not None:
        result.context.job_dir = job_dir
        result.context.project_dir = project_dir
    if rejected:
        result.checks.append(
            Check(
                id="worker_artifacts_rejected",
                suite="hard",
                level="warn",
                status="warn",
                message="Worker artifact references did not pass the private job-file checks and were omitted.",
                actual=str(rejected),
            )
        )
    if result.status == "verified":
        result.status = "draft"
        result.summary = "A BREP artifact was returned; independent verification of the worker evidence is pending."
        result.checks.append(
            Check(
                id="worker_verification_unattested",
                suite="hard",
                level="error",
                status="skip",
                message="The model process cannot attest its own verified status. A separate trusted validator is required.",
            )
        )
    # Never use the worker's geometry metrics, feature records, checks or scenes
    # as evidence. Re-load the private BREP or sheet in a clean validator.
    evidence = None
    if result.ok and kind != "2d" and part is not None:
        try:
            import json
            from .validation import _canonical_json, inspect_brep

            project_path = project_dir / "project.json"
            project = (
                json.loads(project_path.read_text()) if project_path.is_file() else {}
            )
            trusted_spec = dict(spec) if isinstance(spec, Mapping) else {}
            if not isinstance(trusted_spec, dict):
                trusted_spec = {}
            trusted_process = process if process is not None else project.get("process")
            evidence = inspect_brep(
                part,
                candidate_dir=job_dir,
                kind=kind,
                units=project.get("units", units),
                spec=trusted_spec,
                process=trusted_process,
                timeout_s=timeout_s,
                memory_limit_bytes=memory_limit_bytes,
                render_scene=True,
                project=project.get("name") or project_dir.name,
                rev=rev,
            )
            parent_checks = [
                check
                for check in result.checks
                if check.id == "worker_artifacts_rejected"
            ]
            result.checks = [*evidence.checks, *parent_checks]
            requirements = trusted_spec.get("requirements") or []
            requirements = requirements if isinstance(requirements, list) else []
            requirement_results = _requirement_results(requirements, evidence.checks)
            error_requirement_ids = [
                item["id"] for item in requirement_results if item["level"] == "error"
            ]
            independently_passed_error_ids = [
                item["id"]
                for item in requirement_results
                if item["level"] == "error" and item["status"] == "pass"
            ]
            missing_or_unrun = [
                item for item in requirement_results if item["status"] == "skip"
            ]
            incorrect_error = [
                item
                for item in requirement_results
                if item["level"] == "error"
                and item["status"] not in {"pass", "not_applicable"}
            ]
            incorrect_advisory = [
                item
                for item in requirement_results
                if item["level"] == "warn"
                and item["status"] not in {"pass", "fail", "warn", "not_applicable"}
            ]
            independent_failure = next(
                (
                    c
                    for c in evidence.checks
                    if c.level == "error" and c.status == "fail"
                ),
                None,
            )
            result.metrics = dict(evidence.metrics)
            if evidence.failure is not None and evidence.failure.code == "TIMEOUT":
                result.status = "draft"
                result.metrics = dict(evidence.metrics)
                result.checks.append(
                    Check(
                        id="trusted_validation_timeout",
                        suite="hard",
                        level="error",
                        status="skip",
                        message=f"The model built and its BREP was retained, but independent validation exceeded its {timeout_s:g} second budget. Rerun with `aad_build` to validate it again.",
                    )
                )
                result.summary = f"The model built successfully, but independent validation ran out of time after {evidence.elapsed_ms / 1000:.1f} seconds. The revision was retained as draft; rerun `aad_build` to try validation again."
            elif independent_failure is not None:
                result.ok, result.status = False, "failed"
                if independent_failure.suite == "hard":
                    failure_code = "HARD_GATE_FAILED"
                elif independent_failure.suite in {"spec", "process"}:
                    failure_code = "REQUIREMENT_FAILED"
                else:
                    failure_code = "NOT_VERIFIED"
                result.failure = Failure(
                    code=failure_code,
                    phase="validate",
                    feature=independent_failure.feature or "unknown",
                    message=f"Independent validation failed error-level check {independent_failure.id!r}.",
                )
                result.summary = result.failure.message
            elif (
                evidence.failure is not None
                and evidence.failure.code == "INVALID_SOLID"
            ):
                result.ok, result.status = False, "failed"
                result.failure = Failure(
                    code="INVALID_SOLID",
                    phase="validate",
                    message="The saved BREP did not pass independent geometry validation.",
                    kernel_message=evidence.failure.kernel_message
                    or evidence.failure.message,
                )
                result.checks.append(
                    Check(
                        id="independent_brep_invalid",
                        suite="hard",
                        level="error",
                        status="fail",
                        message=result.failure.message,
                        actual=result.failure.kernel_message,
                    )
                )
                result.summary = result.failure.message
            elif evidence.failure is not None:
                result.status = "draft"
                result.metrics = dict(evidence.metrics)
                result.checks.append(
                    Check(
                        id="trusted_validation_unavailable",
                        suite="hard",
                        level="error",
                        status="skip",
                        message="Independent validation could not be completed.",
                        actual=evidence.failure.message,
                    )
                )
                result.summary = "The model built and its BREP was retained, but independent validation did not complete; the revision remains draft."
            elif (
                evidence.ok
                and requirements
                and independently_passed_error_ids
                and not missing_or_unrun
                and not incorrect_error
                and not incorrect_advisory
                and independent_failure is None
                and not any(
                    c.level == "error" and c.status not in {"pass", "not_applicable"}
                    for c in evidence.checks
                )
            ):
                result.status = "verified"
                result.requirements_proven = True
                error_summary = ", ".join(
                    item["id"]
                    for item in requirement_results
                    if item["level"] == "error"
                    and item["status"] in {"pass", "not_applicable"}
                )
                failing_advisory = [
                    f"{item['id']} ({item['status']})"
                    for item in requirement_results
                    if item["level"] == "warn" and item["status"] in {"fail", "warn"}
                ]
                advisory_summary = (
                    f"Advisory requirements not met: {', '.join(failing_advisory)}."
                    if failing_advisory
                    else "No advisory requirements failed."
                )
                result.summary = (
                    "The saved BREP passed the hard gate. Error-level requirements proven: "
                    f"{error_summary}. {advisory_summary}"
                )
                interference_warnings = [
                    check.message
                    for check in result.checks
                    if check.id == "interference_pairs"
                    and check.suite == "interference"
                    and check.level == "warn"
                    and check.status in {"warn", "skip"}
                ]
                if interference_warnings:
                    result.summary += (
                        " Build-time automatic interference check: "
                        + " ".join(interference_warnings)
                    )
                settings = {"units": project.get("units", units)}
                if trusted_process is not None:
                    settings["process"] = trusted_process
                settings_hash = hashlib.sha256(_canonical_json(settings)).hexdigest()
                claims = {
                    "requirement_results": requirement_results,
                    "proven_error_requirement_ids": [
                        item["id"]
                        for item in requirement_results
                        if item["level"] == "error"
                        and item["status"] in {"pass", "not_applicable"}
                    ],
                    "proven_requirement_ids": [
                        item["id"]
                        for item in requirement_results
                        if item["status"] in {"pass", "not_applicable"}
                    ],
                }
                result.verification_receipt = {
                    "rev": rev,
                    "kind": kind,
                    "brep_sha256": evidence.snapshot_sha256,
                    "spec_sha256": evidence.spec_sha256,
                    "kernel_versions": dict(evidence.kernel_versions),
                    "kernel_sha256": hashlib.sha256(
                        _canonical_json(evidence.kernel_versions)
                    ).hexdigest(),
                    "validation_input_sha256": evidence.input_sha256,
                    "project_settings": settings,
                    "project_settings_sha256": settings_hash,
                    **claims,
                    "claims_sha256": hashlib.sha256(
                        _canonical_json(claims)
                    ).hexdigest(),
                }
            else:
                result.status = "draft"
                if worker_claimed_verified:
                    result.checks.append(
                        Check(
                            id="worker_verification_unattested",
                            suite="hard",
                            level="error",
                            status="skip",
                            message="The model worker's verified claim was not independently proven.",
                        )
                    )
                if evidence.failure is None:
                    result.metrics = dict(evidence.metrics)
                    if not requirements:
                        result.summary = "The model built and passed the hard gate, but no requirements were declared; it remains draft."
                    elif not error_requirement_ids:
                        result.summary = (
                            "The spec contains only advisory requirements; nothing required was proven. "
                            "The revision remains draft."
                        )
                    elif missing_or_unrun or incorrect_error:
                        missing_ids = ", ".join(
                            item["id"] for item in missing_or_unrun + incorrect_error
                        )
                        result.summary = f"The model built, but requirements were not fully proven ({missing_ids}); it remains draft."
                    elif not independently_passed_error_ids:
                        result.summary = (
                            "The error-level requirements had no independent pass; nothing required was proven. "
                            "The revision remains draft."
                        )
                    else:
                        result.summary = "The model built, but independent evidence did not prove the required checks; it remains draft."
            if evidence.ok:
                try:
                    if evidence.scene is not None:
                        result.scene = _publish_inspection_scene(evidence, job_dir)
                    if result.scene is None:
                        raise RuntimeError(
                            evidence.scene_error
                            or "the clean validator returned no scene"
                        )
                    if result.verification_receipt is not None:
                        result.verification_receipt["scene_id"] = result.scene.scene_id
                except Exception as exc:
                    reason = evidence.scene_error or f"{type(exc).__name__}: {exc}"
                    _mark_scene_failure(result, reason, job_dir)
            result.metrics.setdefault("validation_ms", evidence.elapsed_ms)
            result.metrics["scene_ms"] = evidence.scene_elapsed_ms
            result.metrics["validation_only_ms"] = max(
                0.0, evidence.elapsed_ms - evidence.scene_elapsed_ms
            )
        except Exception as exc:
            result.status = "draft"
            result.requirements_proven = False
            result.verification_receipt = None
            result.scene = None
            _remove_candidate_scene(job_dir / "scene")
            result.checks.append(
                Check(
                    id="trusted_validation_unavailable",
                    suite="hard",
                    level="error",
                    status="skip",
                    message="Independent validation could not be completed.",
                    actual=f"{type(exc).__name__}: {exc}",
                )
            )
    elif kind == "2d" and part is not None:
        try:
            from .validation import inspect_sheet

            trusted_spec = dict(spec) if isinstance(spec, Mapping) else {}
            evidence = inspect_sheet(
                part,
                candidate_dir=job_dir,
                units=units,
                spec=trusted_spec,
                process=process,
                memory_limit_bytes=memory_limit_bytes,
                timeout_s=timeout_s,
                project=project_dir.name,
                rev=rev,
            )
            parent_checks = [
                check
                for check in result.checks
                if check.id
                in {"worker_artifacts_rejected", "worker_verification_unattested"}
            ]
            result.checks = [*evidence.checks, *parent_checks]
            result.metrics = dict(evidence.metrics)
            if evidence.scene is not None:
                result.scene = _publish_inspection_scene(evidence, job_dir)
            elif evidence.scene_error:
                result.checks.append(
                    Check(
                        id="scene_generation_failed",
                        suite="2d",
                        level="warn",
                        status="skip",
                        message="Independent 2D checks completed, but the clean sheet scene could not be generated.",
                        actual=evidence.scene_error,
                    )
                )
            if evidence.failure is not None:
                result.checks.append(
                    Check(
                        id="trusted_sheet_validation_unavailable",
                        suite="2d",
                        level="error",
                        status="skip",
                        message="Independent 2D validation could not be completed; the saved sheet remains a draft.",
                        actual=evidence.failure.message,
                    )
                )
                if result.ok:
                    result.status = "draft"
                    result.summary = (
                        "The sheet was retained, but independent 2D validation did not complete; "
                        "the revision remains draft."
                    )
            else:
                failed_check = next(
                    (
                        check
                        for check in evidence.checks
                        if check.level == "error" and check.status == "fail"
                    ),
                    None,
                )
                if failed_check is not None:
                    result.ok, result.status = False, "failed"
                    failure_code = (
                        "REQUIREMENT_FAILED"
                        if failed_check.suite == "spec"
                        else "HARD_GATE_FAILED"
                    )
                    result.failure = Failure(
                        code=failure_code,
                        phase="validate",
                        feature=failed_check.feature or "unknown",
                        message=f"Independent 2D validation failed error-level check {failed_check.id!r}.",
                    )
                    result.summary = result.failure.message
                elif result.ok:
                    result.status = "draft"
                    requirements = trusted_spec.get("requirements")
                    requirements = (
                        requirements if isinstance(requirements, list) else []
                    )
                    error_requirements = [
                        item
                        for item in requirements
                        if isinstance(item, Mapping)
                        and item.get("level", "error") == "error"
                    ]
                    skipped = [
                        check.id
                        for check in evidence.checks
                        if check.level == "error" and check.status == "skip"
                    ]
                    if not requirements:
                        result.summary = (
                            "The sheet passed its available 2D checks, but no requirements were declared; "
                            "the revision remains draft."
                        )
                    elif not error_requirements:
                        result.summary = (
                            "The spec contains only advisory requirements; nothing required was proven. "
                            "The revision remains draft."
                        )
                    elif skipped:
                        result.summary = (
                            f"The sheet passed available 2D checks, but skipped checks remain "
                            f"({', '.join(skipped[:5])}); the revision remains draft."
                        )
                    else:
                        result.summary = (
                            "The saved sheet passed its independent 2D checks. "
                            "2D verification is not available, so the revision remains draft."
                        )
                    if evidence.scene_error:
                        result.summary += f" The clean 2D scene could not be generated ({evidence.scene_error})."
        except Exception as exc:
            result.status = "draft"
            result.requirements_proven = False
            result.checks.append(
                Check(
                    id="trusted_sheet_validation_unavailable",
                    suite="2d",
                    level="error",
                    status="skip",
                    message="Independent 2D validation could not be completed; the saved sheet remains a draft.",
                    actual=f"{type(exc).__name__}: {exc}",
                )
            )
            if result.ok:
                result.summary = (
                    "The sheet was retained, but independent 2D validation did not complete; "
                    "the revision remains draft."
                )
    if evidence is not None and evidence.inspection_dir is not None:
        shutil.rmtree(evidence.inspection_dir, ignore_errors=True)
    detail_counts = _detail_counts(result.checks)
    result.checks_summary = {status: detail_counts[status] for status in detail_counts}
    if result.scene is not None:
        result.scene.status = "verified" if result.status == "verified" else "draft"
        result.scene.compare = None
        result.scene.checks_summary = dict(result.checks_summary)
        try:
            _write_scene_status(result.scene, job_dir)
        except Exception as exc:
            _mark_scene_failure(result, f"{type(exc).__name__}: {exc}", job_dir)
    if worker_phase_timings is not None:
        result.metrics["phase_timings_ms"] = worker_phase_timings
    return result


def _remove_candidate_scene(path: Path) -> None:
    """Discard any scene created by the untrusted model before validator output is published."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _publish_inspection_scene(evidence: Any, job_dir: Path) -> SceneManifest:
    if evidence.scene is None or evidence.scene_dir is None:
        raise ValueError("the clean validator returned no complete scene")
    source = evidence.scene_dir
    target = job_dir / "scene"
    _remove_candidate_scene(target)
    os.replace(source, target)
    return evidence.scene


def _write_scene_status(scene: SceneManifest, job_dir: Path) -> None:
    path = job_dir / "scene" / "scene.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("scene_id") != scene.scene_id:
        raise ValueError("the saved clean scene manifest changed before publication")
    value["status"] = scene.status
    value["checks_summary"] = scene.checks_summary
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _mark_scene_failure(result: BuildResult, reason: str, job_dir: Path) -> None:
    _remove_candidate_scene(job_dir / "scene")
    result.scene = None
    result.status = "draft"
    result.requirements_proven = False
    result.verification_receipt = None
    result.checks = [
        check for check in result.checks if check.id != "scene_generation_failed"
    ]
    result.checks.append(
        Check(
            id="scene_generation_failed",
            suite="hard",
            level="error",
            status="skip",
            message="The saved BREP passed geometry validation, but the clean viewer scene could not be generated; the revision remains draft.",
            actual=str(reason)[:1000],
        )
    )
    result.checks_summary = _detail_counts(result.checks)
    result.summary = f"The BREP was retained, but clean scene generation failed ({reason}); the revision remains draft."


def _requirement_results(
    requirements: list[Any], checks: list[Check]
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for index, requirement in enumerate(requirements):
        req_id = requirement.get("id") if isinstance(requirement, Mapping) else None
        if not isinstance(req_id, str) or not req_id.strip():
            req_id = f"requirement_{index + 1}"
        level = (
            requirement.get("level", "error")
            if isinstance(requirement, Mapping)
            else "error"
        )
        if level not in {"error", "warn"}:
            level = "error"
        matches = [
            check for check in checks if check.suite == "spec" and check.id == req_id
        ]
        status = (
            matches[0].status
            if len(matches) == 1 and matches[0].level == level
            else "skip"
        )
        results.append({"id": req_id, "level": level, "status": status})
    return results


def _validate_request(req: BuildRequest) -> None:
    if not isinstance(req.source, str):
        raise TypeError("source must be text")
    if req.kind not in {"part", "assembly", "2d"}:
        raise ValueError("kind must be 'part', 'assembly', or '2d'")
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
        if (
            isinstance(value, dict)
            and value.get("protocol") == 1
            and isinstance(value.get("result"), dict)
        ):
            return value["result"]
    return None


def _result_from_wire(
    wire: dict[str, Any], *, job_dir: Path, elapsed_ms: float, process_stderr: str
) -> BuildResult:
    if not isinstance(wire.get("ok"), bool) or wire.get("status") not in (
        "failed",
        "draft",
        "verified",
    ):
        raise ValueError("worker result has invalid success/status fields")
    if wire["ok"] != (wire["status"] != "failed"):
        raise ValueError("worker result has inconsistent success/status fields")
    checks = [
        _check_from_wire(item)
        for item in _object_list(wire.get("checks", []), "checks")
    ]
    summary_data = wire.get("checks_summary", {})
    if not isinstance(summary_data, dict):
        raise ValueError("checks_summary must be an object")
    summary = {}
    for status in ("pass", "fail", "warn", "skip", "not_applicable"):
        count = summary_data.get(status, 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("checks_summary counts must be non-negative integers")
        summary[status] = count
    context_data = wire.get("context")
    context = (
        _context_from_wire(context_data) if isinstance(context_data, dict) else None
    )
    scene_data = wire.get("scene")
    scene = SceneManifest(**scene_data) if isinstance(scene_data, dict) else None
    failure_data = wire.get("failure")
    failure = (
        _failure_from_wire(failure_data) if isinstance(failure_data, dict) else None
    )
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
    if item.get("suite", "hard") not in (
        "hard",
        "spec",
        "process",
        "interference",
        "2d",
    ):
        raise ValueError("check has an unsupported suite")
    if item.get("level", "warn") not in ("error", "warn") or item.get(
        "status", "skip"
    ) not in ("pass", "fail", "warn", "skip", "not_applicable"):
        raise ValueError("check has an unsupported level or status")
    if not isinstance(item.get("id"), str) or not item["id"]:
        raise ValueError("check requires a nonempty string ID")
    for field in (
        "message",
        "expected",
        "actual",
        "feature",
        "selector",
        "assumptions",
        "fix_hint",
    ):
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
        features=[
            _feature_from_wire(feature)
            for feature in _object_list(item.get("features", []), "features")
        ],
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
        faces=[
            _entity_from_wire(entity)
            for entity in _object_list(item.get("faces", []), "feature faces")
        ],
        edges=[
            _entity_from_wire(entity)
            for entity in _object_list(item.get("edges", []), "feature edges")
        ],
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
        candidates=[
            Candidate(**candidate)
            for candidate in _object_list(
                item.get("candidates", []), "failure candidates"
            )
        ],
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
