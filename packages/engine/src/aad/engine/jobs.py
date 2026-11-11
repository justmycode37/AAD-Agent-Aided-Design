"""Run each model script in a fresh, killable Python worker process."""

from __future__ import annotations

import json
import hashlib
import importlib.metadata
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
from .cache import CacheError, GeometryCache
from .identity import cache_identity
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
    record on stdout. Every model process uses the OS sandbox launcher and
    installs its address-space growth budget before importing worker/kernel
    code; an unavailable boundary fails closed.
    """
    exchange_dir: Path | None = None
    job_dir: Path | None = None
    try:
        request = _coerce_request(req)
        _validate_request(request)
        project_dir = Path(request.project_dir).expanduser().resolve()
        if request.job_dir is None:
            job_dir = Path(tempfile.mkdtemp(prefix="aad-engine-job-")).resolve()
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
            # Optional native rendering must not abort or hang this process
            # after it has already produced a usable BREP.
            "preview": False,
        }
        if re.search(r"\b(?:asset|vendor_step)\b", request.source):
            _stage_assets(project_dir, job_dir)
        cache_enabled = request.part_cache and not _part_cache_disabled()
        pipeline = _part_cache_pipeline() if cache_enabled and "cached_part" in request.source else None
        cache_enabled = cache_enabled and pipeline is not None and "cached_part" in request.source
        exchange_dir = Path(tempfile.mkdtemp(prefix=".aad-part-cache-", dir=job_dir)) if cache_enabled else job_dir
        kernel_versions, pipeline_identity = pipeline if pipeline is not None else ({}, "")
        asset_sha256 = _staged_asset_digests(job_dir)
        cache_seeds = _stage_part_cache(
            project_dir, exchange_dir, source=request.source,
            pipeline_identity=pipeline_identity, kernel_versions=kernel_versions,
            asset_sha256=asset_sha256, spec=request.spec, kind=request.kind, units=request.units,
        ) if cache_enabled else {}
        payload.update({
            "part_cache_enabled": cache_enabled,
            "part_cache_exchange_dir": str(exchange_dir),
            "part_cache_seeds": cache_seeds,
            "part_cache_kernel_versions": kernel_versions,
            "part_cache_pipeline_identity": pipeline_identity,
            "part_cache_asset_sha256": asset_sha256,
        })
        input_bytes = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        if exchange_dir is not None and job_dir is not None and exchange_dir != job_dir:
            _cleanup_part_cache_exchange(exchange_dir, job_dir)
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
        if exchange_dir is not None and job_dir is not None and exchange_dir != job_dir:
            _cleanup_part_cache_exchange(exchange_dir, job_dir)
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
        if exchange_dir is not None and job_dir is not None and exchange_dir != job_dir:
            _cleanup_part_cache_exchange(exchange_dir, job_dir)
        return _failed(
            Failure(code="BAD_INPUT", phase="input", message=f"Build memory policy is invalid: {exc}"),
            job_dir=job_dir,
            build_ms=_elapsed_ms(started),
        )

    try:
        stdout_bytes, stderr_bytes = process.communicate(input=input_bytes, timeout=request.timeout_s)
    except subprocess.TimeoutExpired:
        _kill_worker(process)
        stdout_bytes, stderr_bytes = _drain_killed_worker(process)
        if exchange_dir is not None and job_dir is not None and exchange_dir != job_dir:
            _cleanup_part_cache_exchange(exchange_dir, job_dir)
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
        if exchange_dir is not None and job_dir is not None and exchange_dir != job_dir:
            _cleanup_part_cache_exchange(exchange_dir, job_dir)
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
        elif not worker_started and response is None and process.returncode != 0 and any(
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
        result = _constrain_worker_evidence(result, job_dir, project_dir, kind=request.kind)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if exchange_dir is not None and job_dir is not None and exchange_dir != job_dir:
            _cleanup_part_cache_exchange(exchange_dir, job_dir)
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

    if cache_seeds:
        _mark_unreproduced_geometry(result)

    if exchange_dir != job_dir:
        stored, write_errors = _publish_part_cache(
            project_dir, exchange_dir, result,
            source=request.source, pipeline_identity=pipeline_identity,
            kernel_versions=kernel_versions, asset_sha256=asset_sha256,
            spec=request.spec, kind=request.kind, units=request.units,
        )
        cache_metrics = result.metrics.setdefault("geometry_cache", {})
        if isinstance(cache_metrics, dict):
            cache_metrics["stored"] = stored
            cache_metrics["write_errors"] = write_errors
            if result.context is not None:
                result.context.metrics["geometry_cache"] = dict(cache_metrics)
        _cleanup_part_cache_exchange(exchange_dir, job_dir)

    preview_started = time.perf_counter()
    if request.preview and "part.brep" in result.artifacts:
        from ._preview import add_preview

        remaining_s = max(0.0, request.timeout_s - (time.perf_counter() - started))
        add_preview(result, timeout_s=remaining_s, memory_limit_bytes=memory_limit_bytes)
    elapsed_ms = _elapsed_ms(started)
    result.build_ms = elapsed_ms
    _finish_phase_timings(result, elapsed_ms, preview_ms=_elapsed_ms(preview_started) if request.preview else 0.0)
    return result


def _finish_phase_timings(result: BuildResult, elapsed_ms: float, *, preview_ms: float = 0.0) -> None:
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
    normalized = {name: round(milliseconds, 3) for name, milliseconds in normalized.items()}
    result.metrics["build_ms"] = elapsed_ms
    result.metrics["phase_timings_ms"] = normalized
    if result.context is not None:
        result.context.metrics["build_ms"] = elapsed_ms
        result.context.metrics["phase_timings_ms"] = normalized
    if elapsed_ms > 20_000 and normalized:
        slowest, milliseconds = max(normalized.items(), key=lambda item: item[1])
        result.summary = f"{result.summary} Slowest phase: {slowest} ({milliseconds / 1000:.1f} s)."


# Candidates come from model code, so these bound what it can make the parent read or keep.
_MAX_CACHE_RECORD_BYTES = 8 * 1024 * 1024
_MAX_CACHE_ENTRY_BYTES = 256 * 1024 * 1024
_MAX_CACHE_PROJECT_BYTES = 2 * 1024 * 1024 * 1024


def _part_cache_disabled() -> bool:
    return os.environ.get("AAD_DISABLE_GEOMETRY_CACHE", "").strip().lower() in {"1", "true", "yes", "on"}


def _part_cache_pipeline() -> tuple[dict[str, str], str] | None:
    """Identify the versions and source code that can affect cached geometry/checks."""
    distributions = (
        "python", "aad-engine", "aad-quality", "aad-render", "aad-drafting", "aad-design",
        "build123d", "cadquery-ocp-novtk",
    )
    versions: dict[str, str] = {}
    for distribution in distributions:
        try:
            value = sys.version.split()[0] if distribution == "python" else importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return None
        versions[distribution] = value

    try:
        import aad
        import aad_quality

        roots = {
            "engine": Path(aad.__file__).resolve().parent,
            "quality": Path(aad_quality.__file__).resolve().parent,
        }
        engine_project = Path(__file__).resolve().parents[3]
        quality_project = roots["quality"].parents[1]
        files = [engine_project / "pyproject.toml", quality_project / "pyproject.toml"]
        for root in roots.values():
            files.extend(path for path in root.rglob("*")
                         if path.is_file() and path.suffix in {".py", ".json"})
        digest = hashlib.sha256()
        digest.update(sys.platform.encode("utf-8"))
        for name, version in sorted(versions.items()):
            digest.update(name.encode("utf-8") + b"\0" + version.encode("utf-8") + b"\0")
        for path in sorted(set(files), key=lambda item: str(item)):
            if not path.is_file():
                return None
            try:
                relative = path.relative_to(engine_project.parent.parent)
            except ValueError:
                relative = path
            digest.update(str(relative).encode("utf-8") + b"\0")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        return versions, digest.hexdigest()
    except (ImportError, OSError, ValueError):
        return None


def _staged_asset_digests(job_dir: Path) -> dict[str, str]:
    assets = job_dir / "assets"
    if assets.is_symlink() or not assets.is_dir():
        return {}
    digests: dict[str, str] = {}
    for path in sorted(assets.iterdir(), key=lambda item: item.name):
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            digests[path.name] = digest.hexdigest()
        except OSError:
            continue
    return digests


def _wire_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _stage_part_cache(
    project_dir: Path,
    exchange_dir: Path,
    *,
    source: str,
    pipeline_identity: str,
    kernel_versions: dict[str, str],
    asset_sha256: dict[str, str],
    spec: dict[str, Any],
    kind: str,
    units: str,
) -> dict[str, dict[str, Any]]:
    """Copy valid same-project candidates into the already-writable job tree."""
    aad_dir = project_dir / ".aad"
    cache_dir = aad_dir / "cache"
    try:
        aad_info = aad_dir.lstat()
        cache_info = cache_dir.lstat()
        if not stat.S_ISDIR(aad_info.st_mode) or not stat.S_ISDIR(cache_info.st_mode):
            return {}
        cache = GeometryCache(cache_dir)
        entries = cache.candidates(source=source, pipeline_identity=pipeline_identity)
        trusted = {
            "source": source,
            "spec": _wire_json(spec),
            "kind": kind,
            "units": units,
            "kernel_versions": kernel_versions,
            "asset_sha256": asset_sha256,
            "pipeline_identity": pipeline_identity,
        }
        seed_root = exchange_dir / "seed"
        seed_root.mkdir(mode=0o700)
        seeds: dict[str, dict[str, Any]] = {}
        for item in entries:
            record = item.input_record
            if any(_canonical_json(record.get(name)) != _canonical_json(value)
                   for name, value in trusted.items()):
                continue
            try:
                destination = seed_root / item.geometry.key
                cache.restore(item.geometry.key, record, destination)
            except (CacheError, OSError, ValueError):
                continue
            seeds[item.geometry.key] = {
                "input_record": record,
                "byte_length": item.geometry.byte_length,
                "sha256": item.geometry.sha256,
            }
        return seeds
    except (CacheError, OSError, ValueError, RuntimeError):
        return {}


def _ensure_project_cache(project_dir: Path) -> GeometryCache:
    aad_dir = project_dir / ".aad"
    try:
        info = aad_dir.lstat()
    except FileNotFoundError:
        aad_dir.mkdir(mode=0o700)
    else:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("project .aad path must be a real directory")
    return GeometryCache(aad_dir / "cache")


def _publish_part_cache(
    project_dir: Path,
    exchange_dir: Path,
    result: BuildResult,
    *,
    source: str,
    pipeline_identity: str,
    kernel_versions: dict[str, str],
    asset_sha256: dict[str, str],
    spec: dict[str, Any],
    kind: str,
    units: str,
) -> tuple[int, int]:
    """Publish candidate B-reps only after the worker process has exited."""
    if not result.ok or result.context is None or kind == "2d":
        return 0, 0
    pending = exchange_dir / "pending"
    try:
        if not stat.S_ISDIR(pending.lstat().st_mode):
            return 0, 0
        cache = _ensure_project_cache(project_dir)
    except (OSError, ValueError, CacheError):
        return 0, 0

    trusted = {
        "source": source,
        "spec": _wire_json(spec),
        "kind": kind,
        "units": units,
        "kernel_versions": kernel_versions,
        "asset_sha256": asset_sha256,
        "pipeline_identity": pipeline_identity,
        "material": result.context.material,
        "process": result.context.process,
    }
    stored = errors = 0
    for entry in pending.iterdir():
        key = entry.name
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            continue
        try:
            if not stat.S_ISDIR(entry.lstat().st_mode):
                continue
            record_path = entry / "input_record.json"
            record_info = record_path.lstat()
            if (not stat.S_ISREG(record_info.st_mode) or record_info.st_nlink != 1
                    or record_info.st_size > _MAX_CACHE_RECORD_BYTES):
                continue
            brep_info = (entry / "part.brep").lstat()
            if brep_info.st_size > _MAX_CACHE_ENTRY_BYTES:
                continue
            with record_path.open("rb") as stream:
                record = json.loads(stream.read().decode("utf-8"))
            kwargs = {name: record[name] for name in (
                "source", "applied_params", "spec", "kind", "units", "material", "process",
                "kernel_versions", "tolerances", "asset_sha256", "pipeline_identity",
            )}
            actual_key, canonical_record = cache_identity(**kwargs)
            if actual_key != key or _canonical_json(record) != _canonical_json(canonical_record):
                continue
            if any(_canonical_json(record.get(name)) != _canonical_json(value)
                   for name, value in trusted.items()):
                continue
            params_record = record.get("applied_params")
            if type(params_record) is not dict or _canonical_json(params_record.get("model")) != _canonical_json(_wire_json(result.context.params)):
                continue
            try:
                cache.put(key, record, entry / "part.brep", candidate_dir=exchange_dir)
                cache.prune(_MAX_CACHE_PROJECT_BYTES)
                stored += 1
            except (CacheError, OSError, ValueError):
                errors += 1
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            errors += 1
    return stored, errors


def _mark_unreproduced_geometry(result: BuildResult) -> None:
    """Keep a build that was offered cached geometry from reaching ``verified``.

    Entries are written by model code, so their bytes cannot be proven to match
    the builder. The offer decides this, because the worker's hit count is also
    untrusted. The returned geometry still gets every check; a clean rebuild
    with ``part_cache=False`` shows the source reproduces it.
    """
    result.checks.append(Check(
        id="part_cache_unreproduced", suite="hard", level="error", status="skip",
        message="This build was offered cached part geometry that AAD cannot prove matches its builder.",
        fix_hint="Rebuild with part_cache=false to confirm the source reproduces this geometry.",
    ))
    result.checks_summary["skip"] = result.checks_summary.get("skip", 0) + 1
    if result.status == "verified":
        result.status = "draft"
    if result.scene is not None:
        result.scene.checks_summary = dict(result.checks_summary)


def _cleanup_part_cache_exchange(exchange_dir: Path, job_dir: Path) -> None:
    try:
        if (exchange_dir.parent.resolve(strict=True) == job_dir.resolve(strict=True)
                and exchange_dir.name.startswith(".aad-part-cache-")
                and stat.S_ISDIR(exchange_dir.lstat().st_mode)):
            shutil.rmtree(exchange_dir)
    except (OSError, RuntimeError, ValueError):
        pass


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


def _reference_read_paths(project_dir: Path, spec: Mapping[str, Any]) -> tuple[Path, ...]:
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


def _constrain_worker_evidence(result: BuildResult, job_dir: Path, project_dir: Path, *, kind: str = "part") -> BuildResult:
    # A model's pipe result is not a trusted validation receipt.
    original_counts = _detail_counts(result.checks)
    totals = {status: max(result.checks_summary.get(status, 0), count)
              for status, count in original_counts.items()}
    # A sheet has no B-rep body: its source record is the artifact that must exist.
    required = "sheet.json" if kind == "2d" else "part.brep"
    part = _canonical_artifact(job_dir / required, job_dir)
    if result.ok and part is None:
        result.ok, result.status = False, "failed"
        result.failure = Failure(
            code="WORKER_CRASH", phase="crash", feature="unknown", file=required,
            message=f"The worker reported success without a private, non-empty {required} artifact; the build was not accepted.",
        )
        result.summary = result.failure.message
        result.checks.append(Check(id="worker_artifact_required", suite="hard", level="error", status="fail",
                                   message=f"The required private {required} artifact was not available."))
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
        artifacts[required] = str(part)
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
        meta=dict(item.get("meta", {})),
        param_definitions=dict(item.get("param_definitions", {})),
        part_name=item.get("part_name"),
    )


def _feature_from_wire(item: dict[str, Any]) -> FeatureInfo:
    return FeatureInfo(
        name=item.get("name", "main"),
        faces=[_entity_from_wire(entity) for entity in _object_list(item.get("faces", []), "feature faces")],
        edges=[_entity_from_wire(entity) for entity in _object_list(item.get("edges", []), "feature edges")],
        line_start=item.get("line_start"),
        line_end=item.get("line_end"),
        deps=list(item.get("deps", [])),
        intent=item.get("intent"),
        vertices=[_entity_from_wire(entity) for entity in item.get("vertices", [])],
        metadata=dict(item.get("metadata", {})),
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
