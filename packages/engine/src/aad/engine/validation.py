"""Kernel-free parent API for inspecting a completed model's BREP independently."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from importlib import metadata as importlib_metadata
import json
import math
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

from . import memory, sandbox
from .jobs import (
    _check_from_wire,
    _decode,
    _drain_killed_worker,
    _failure_from_wire,
    _kill_worker,
)
from .types import Check, Failure, SceneManifest

_MARKER = b"AAD_GEOMETRY_EVIDENCE:"
_METRICS_MARKER = b"AAD_GEOMETRY_METRICS:"
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); _limit=sys.argv.pop(1); "
    "from aad.engine.memory import install_worker_memory_budget; "
    "install_worker_memory_budget(int(_limit)) if _limit != '-' else None; "
    "from aad.engine._validation_worker import main; main()"
)
_SHEET_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); _limit=sys.argv.pop(1); "
    "from aad.engine.memory import install_worker_memory_budget; "
    "install_worker_memory_budget(int(_limit)) if _limit != '-' else None; "
    "from aad.engine._sheet_validation_worker import main; main()"
)


@dataclass
class GeometryEvidence:
    """Inspection outcome, never a verified revision or proof of requirements.

    ``ok`` means the BREP was read and passed native validity/body checks with
    no failed error-level hard checks. Error-level skips still appear in
    ``checks``. It does not mean the full hard gate or design intent is proven.
    """

    ok: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    scene: SceneManifest | None = None
    scene_dir: Path | None = None
    scene_error: str | None = None
    scene_elapsed_ms: float = 0.0
    failure: Failure | None = None
    snapshot_sha256: str | None = None
    input_sha256: str | None = None
    spec_sha256: str | None = None
    kernel_versions: dict[str, str] = field(default_factory=dict)
    inspection_dir: Path | None = None
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: float = 0.0


@dataclass
class SheetEvidence:
    """Independent findings and measurements derived from a saved Sheet2D JSON snapshot."""

    ok: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    scene: SceneManifest | None = None
    scene_dir: Path | None = None
    scene_error: str | None = None
    scene_elapsed_ms: float = 0.0
    failure: Failure | None = None
    snapshot_sha256: str | None = None
    input_sha256: str | None = None
    inspection_dir: Path | None = None
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: float = 0.0


def inspect_brep(
    brep_path: str | Path,
    *,
    candidate_dir: str | Path,
    kind: str,
    units: str,
    timeout_s: float = 60.0,
    spec: dict[str, Any] | None = None,
    memory_limit_bytes: int | None = None,
    process: str | None = None,
    render_scene: bool = False,
    project: str = "dev",
    rev: int = 0,
) -> GeometryEvidence:
    """Snapshot then inspect a BREP after its model process has fully exited.

    The trusted caller owns kind/units/spec and must prevent concurrent writes
    to the candidate. It should pass the same configured memory budget used by
    the model worker. When omitted on macOS or Linux, the engine default applies.
    The child reads only a new snapshot and never model.py, model modules, model
    metrics, features, params or claimed check results.
    """
    started = time.monotonic()
    result = GeometryEvidence()
    child = None
    communication_complete = False
    stdout, stderr = b"", b""
    phase = "input"
    try:
        if kind not in {"part", "assembly"} or units not in {"mm", "in"}:
            raise ValueError(
                "inspection requires kind part/assembly and explicit units mm/in"
            )
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            raise ValueError("timeout_s must be a positive finite number")
        if memory_limit_bytes is None:
            if sys.platform == "darwin" or sys.platform.startswith("linux"):
                memory_limit_bytes = memory.default_memory_budget_bytes()
        else:
            memory_limit_bytes = memory.memory_budget_bytes(memory_limit_bytes)
        deadline = started + timeout_s
        declared = {"kind": kind, "units": units, "spec": {} if spec is None else spec}
        if process is not None:
            declared["process"] = process
        if not isinstance(declared["spec"], dict):
            raise ValueError("spec must be a plain JSON object")
        if not isinstance(render_scene, bool):
            raise ValueError("render_scene must be boolean")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project must be non-empty text")
        if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
            raise ValueError("rev must be a non-negative integer")
        _json_value(declared)
        # Detach caller mutations before hashing or giving inputs to the child.
        declared = json.loads(_canonical_json(declared))
        result.spec_sha256 = hashlib.sha256(
            _canonical_json(declared["spec"])
        ).hexdigest()
        versions = {}
        for distribution in ("build123d", "cadquery-ocp-novtk"):
            try:
                versions[distribution] = importlib_metadata.version(distribution)
            except importlib_metadata.PackageNotFoundError:
                versions[distribution] = "unavailable"
        result.kernel_versions = versions
        candidate = Path(candidate_dir).resolve(strict=True)
        path = Path(brep_path).absolute()
        resolved = path.resolve(strict=True)
        if (
            not candidate.is_dir()
            or candidate == Path("/")
            or not resolved.is_relative_to(candidate)
        ):
            raise ValueError("BREP must be inside a private candidate directory")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size <= 0:
            raise ValueError(
                "BREP must be a nonempty private regular file without links"
            )
        # Snapshot only after the producing process is gone. A final-component
        # no-follow open and descriptor checks avoid following a replaced link.
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_nlink) != (
                info.st_dev,
                info.st_ino,
                1,
            ) or not stat.S_ISREG(opened.st_mode):
                raise ValueError("BREP changed before snapshotting")
            result.inspection_dir = Path(
                tempfile.mkdtemp(prefix="inspection-", dir=candidate)
            ).resolve()
            snapshot = result.inspection_dir / "part.brep"
            digest = hashlib.sha256()
            with snapshot.open("xb") as target:
                while block := source.read(1024 * 1024):
                    _remaining(deadline)
                    digest.update(block)
                    target.write(block)
            after = os.fstat(source.fileno())
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            ):
                raise ValueError("BREP changed while snapshotting")
        result.snapshot_sha256 = digest.hexdigest()
        record = {
            "protocol": 1,
            "snapshot_sha256": result.snapshot_sha256,
            "spec_sha256": result.spec_sha256,
            "kernel_versions": versions,
            "inputs": declared,
        }
        result.input_sha256 = hashlib.sha256(_canonical_json(record)).hexdigest()
        nonce = secrets.token_hex(24)
        payload = {
            "record": record,
            "input_sha256": result.input_sha256,
            "nonce": nonce,
            "render_scene": render_scene,
            "scene_project": project,
            "scene_rev": rev,
        }
        phase = "sandbox"
        _remaining(deadline)
        startup_limit = (
            memory.bootstrap_address_space_budget(memory_limit_bytes)
            if memory_limit_bytes is not None
            else None
        )
        child = sandbox.popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                _BOOTSTRAP,
                str(_SOURCE_ROOT),
                str(memory_limit_bytes) if memory_limit_bytes is not None else "-",
            ],
            result.inspection_dir,
            address_space=startup_limit,
        )
        phase = "inspect"
        stdout, stderr = child.communicate(
            input=_canonical_json(payload) + b"\n", timeout=_remaining(deadline)
        )
        communication_complete = True
        if child.returncode != 0:
            if memory.has_setup_failure_marker(stdout) or memory.has_setup_failure_marker(stderr):
                result.failure = Failure(
                    code="SANDBOX_DENIED",
                    phase="sandbox",
                    feature="unknown",
                    message="The configured engine.memory limit could not be installed; validation did not run.",
                    kernel_message=_decode(stderr),
                )
            elif memory.is_memory_exhaustion_output(stderr):
                result.failure = Failure(
                    code="OUT_OF_MEMORY",
                    phase="oom",
                    feature="unknown",
                    message="The independent BREP validator could not allocate memory under engine.memory.",
                    kernel_message=_decode(stderr),
                )
            else:
                result.failure = Failure(
                    code="WORKER_CRASH",
                    phase="crash",
                    feature="unknown",
                    message=f"The BREP inspector exited with status {child.returncode}; no exit cause was inferred.",
                )
        else:
            phase = "decode"
            wire = _response(stdout)
            if (
                wire.get("nonce") != nonce
                or wire.get("input_sha256") != result.input_sha256
                or wire.get("snapshot_sha256") != result.snapshot_sha256
            ):
                raise ValueError(
                    "BREP inspection response is not bound to these inputs and snapshot"
                )
            if _file_digest(snapshot, deadline) != result.snapshot_sha256:
                raise ValueError("BREP snapshot changed during inspection")
            _accept_response(result, wire, units, kind)
            if render_scene:
                _accept_scene(result, wire, project=project, rev=rev, units=units)
            _remaining(deadline)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.output or b""
        stderr = exc.stderr or b""
        result.failure = Failure(
            code="TIMEOUT",
            phase="timeout",
            feature="unknown",
            message=f"BREP inspection exceeded its {timeout_s:g} second budget.",
        )
    except TimeoutError:
        result.failure = Failure(
            code="TIMEOUT",
            phase="timeout",
            feature="unknown",
            message=f"BREP inspection exceeded its {timeout_s:g} second budget.",
        )
    except memory.AddressSpaceUnavailable as exc:
        result.failure = Failure(
            code="SANDBOX_DENIED",
            phase="sandbox",
            feature="unknown",
            message="The configured engine.memory policy is unavailable; independent BREP validation did not run.",
            kernel_message=f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:
        if phase == "input":
            code, failure_phase = "BAD_INPUT", "input"
        elif phase == "sandbox":
            code, failure_phase = "SANDBOX_DENIED", "sandbox"
        else:
            code, failure_phase = "WORKER_CRASH", "crash"
        result.failure = Failure(
            code=code,
            phase=failure_phase,
            feature="unknown",
            message="Independent BREP inspection could not be completed.",
            kernel_message=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if child is not None and (not communication_complete or child.poll() is None):
            try:
                _kill_worker(child)
                drained_stdout, drained_stderr = _drain_killed_worker(child)
                stdout = drained_stdout or stdout
                stderr = drained_stderr or stderr
            except OSError as exc:
                result.failure = Failure(
                    code="WORKER_CRASH",
                    phase="crash",
                    feature="unknown",
                    message="BREP inspector cleanup failed.",
                    kernel_message=f"{type(exc).__name__}: {exc}",
                )
        result.stdout, result.stderr = _decode(stdout), _decode(stderr)
        result.elapsed_ms = (time.monotonic() - started) * 1000
        if (
            result.failure is not None
            and result.failure.code == "TIMEOUT"
            and result.snapshot_sha256
        ):
            try:
                progress = _progress_response(stdout)
                if (
                    progress.get("nonce") != nonce
                    or progress.get("input_sha256") != result.input_sha256
                    or progress.get("snapshot_sha256") != result.snapshot_sha256
                ):
                    raise ValueError(
                        "validator metrics progress is not bound to this inspection"
                    )
                if _file_digest(snapshot, math.inf) != result.snapshot_sha256:
                    raise ValueError("validation snapshot changed before timeout")
                result.metrics = _validated_metrics(progress.get("metrics"), units)
            except Exception:
                result.metrics = {}
        if result.failure is not None:
            result.ok = False
    return result


def inspect_sheet(
    sheet_path: str | Path,
    *,
    candidate_dir: str | Path,
    units: str,
    spec: dict[str, Any] | None = None,
    process: str | None = None,
    memory_limit_bytes: int | None = None,
    timeout_s: float = 60.0,
    project: str = "dev",
    rev: int = 0,
) -> SheetEvidence:
    """Snapshot and independently check a serialized Sheet2D after its model process exits."""
    started = time.monotonic()
    result = SheetEvidence()
    child_process = None
    communication_complete = False
    stdout, stderr = b"", b""
    phase = "input"
    try:
        if units not in {"mm", "in"}:
            raise ValueError("sheet inspection requires explicit units mm/in")
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            raise ValueError("timeout_s must be a positive finite number")
        if memory_limit_bytes is None:
            if sys.platform == "darwin" or sys.platform.startswith("linux"):
                memory_limit_bytes = memory.default_memory_budget_bytes()
        else:
            memory_limit_bytes = memory.memory_budget_bytes(memory_limit_bytes)
        deadline = started + timeout_s
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project must be non-empty text")
        if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
            raise ValueError("rev must be a non-negative integer")
        declared = {
            "kind": "2d",
            "units": units,
            "spec": {} if spec is None else spec,
            "process": process,
            "project": project,
            "rev": rev,
        }
        if not isinstance(declared["spec"], dict):
            raise ValueError("spec must be a plain JSON object")
        _json_value(declared)
        declared = json.loads(_canonical_json(declared))
        candidate = Path(candidate_dir).resolve(strict=True)
        path = Path(sheet_path).absolute()
        resolved = path.resolve(strict=True)
        if (
            not candidate.is_dir()
            or candidate == Path("/")
            or not resolved.is_relative_to(candidate)
        ):
            raise ValueError("sheet JSON must be inside a private candidate directory")
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size <= 0
            or info.st_size > 256 * 1024 * 1024
        ):
            raise ValueError(
                "sheet JSON must be a bounded, nonempty private regular file without links"
            )
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_nlink) != (
                info.st_dev,
                info.st_ino,
                1,
            ) or not stat.S_ISREG(opened.st_mode):
                raise ValueError("sheet JSON changed before snapshotting")
            result.inspection_dir = Path(
                tempfile.mkdtemp(prefix="sheet-inspection-", dir=candidate)
            ).resolve()
            snapshot = result.inspection_dir / "sheet.json"
            digest = hashlib.sha256()
            with snapshot.open("xb") as target:
                while block := source.read(1024 * 1024):
                    _remaining(deadline)
                    digest.update(block)
                    target.write(block)
            after = os.fstat(source.fileno())
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            ):
                raise ValueError("sheet JSON changed while snapshotting")
        result.snapshot_sha256 = digest.hexdigest()
        record = {
            "protocol": 1,
            "snapshot_sha256": result.snapshot_sha256,
            "inputs": declared,
        }
        result.input_sha256 = hashlib.sha256(_canonical_json(record)).hexdigest()
        nonce = secrets.token_hex(24)
        payload = {
            "record": record,
            "input_sha256": result.input_sha256,
            "nonce": nonce,
        }
        phase = "sandbox"
        _remaining(deadline)
        startup_limit = (
            memory.bootstrap_address_space_budget(memory_limit_bytes)
            if memory_limit_bytes is not None
            else None
        )
        child_process = sandbox.popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                _SHEET_BOOTSTRAP,
                str(_SOURCE_ROOT),
                str(memory_limit_bytes) if memory_limit_bytes is not None else "-",
            ],
            result.inspection_dir,
            address_space=startup_limit,
        )
        phase = "inspect"
        stdout, stderr = child_process.communicate(
            input=_canonical_json(payload) + b"\n", timeout=_remaining(deadline)
        )
        communication_complete = True
        if child_process.returncode != 0:
            if memory.has_setup_failure_marker(stdout) or memory.has_setup_failure_marker(stderr):
                result.failure = Failure(
                    code="SANDBOX_DENIED",
                    phase="sandbox",
                    feature="unknown",
                    message="The configured engine.memory limit could not be installed; sheet validation did not run.",
                    kernel_message=_decode(stderr),
                )
            elif memory.is_memory_exhaustion_output(stderr):
                result.failure = Failure(
                    code="OUT_OF_MEMORY",
                    phase="oom",
                    feature="unknown",
                    message="The independent sheet validator could not allocate memory under engine.memory.",
                    kernel_message=_decode(stderr),
                )
            else:
                result.failure = Failure(
                    code="WORKER_CRASH",
                    phase="crash",
                    feature="unknown",
                    message=f"The sheet inspector exited with status {child_process.returncode}; no exit cause was inferred.",
                )
        else:
            wire = _response(stdout)
            if (
                wire.get("nonce") != nonce
                or wire.get("input_sha256") != result.input_sha256
                or wire.get("snapshot_sha256") != result.snapshot_sha256
            ):
                raise ValueError(
                    "sheet inspection response is not bound to these inputs and snapshot"
                )
            if _file_digest(snapshot, deadline) != result.snapshot_sha256:
                raise ValueError("sheet snapshot changed during inspection")
            _accept_sheet_response(result, wire)
            _accept_sheet_scene(result, wire, project=project, rev=rev, units=units)
            _remaining(deadline)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.output or b""
        stderr = exc.stderr or b""
        result.failure = Failure(
            code="TIMEOUT",
            phase="timeout",
            feature="unknown",
            message=f"Sheet inspection exceeded its {timeout_s:g} second budget.",
        )
    except TimeoutError:
        result.failure = Failure(
            code="TIMEOUT",
            phase="timeout",
            feature="unknown",
            message=f"Sheet inspection exceeded its {timeout_s:g} second budget.",
        )
    except memory.AddressSpaceUnavailable as exc:
        result.failure = Failure(
            code="SANDBOX_DENIED",
            phase="sandbox",
            feature="unknown",
            message="The configured engine.memory policy is unavailable; independent sheet validation did not run.",
            kernel_message=f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:
        if phase == "input":
            code, failure_phase = "BAD_INPUT", "input"
        elif phase == "sandbox":
            code, failure_phase = "SANDBOX_DENIED", "sandbox"
        else:
            code, failure_phase = "WORKER_CRASH", "crash"
        result.failure = Failure(
            code=code,
            phase=failure_phase,
            feature="unknown",
            message="Independent sheet inspection could not be completed.",
            kernel_message=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if child_process is not None and (
            not communication_complete or child_process.poll() is None
        ):
            try:
                _kill_worker(child_process)
                drained_stdout, drained_stderr = _drain_killed_worker(child_process)
                stdout = drained_stdout or stdout
                stderr = drained_stderr or stderr
            except OSError as exc:
                result.failure = Failure(
                    code="WORKER_CRASH",
                    phase="crash",
                    feature="unknown",
                    message="Sheet inspector cleanup failed.",
                    kernel_message=f"{type(exc).__name__}: {exc}",
                )
        result.stdout, result.stderr = _decode(stdout), _decode(stderr)
        result.elapsed_ms = (time.monotonic() - started) * 1000
        if result.failure is not None:
            result.ok = False
    return result


def _accept_sheet_response(result: SheetEvidence, wire: dict[str, Any]) -> None:
    if not isinstance(wire.get("ok"), bool):
        raise ValueError("sheet inspection ok must be boolean")
    metrics = wire.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("sheet inspection metrics must be an object")
    _json_value(metrics)
    checks = wire.get("checks")
    if not isinstance(checks, list) or any(
        not isinstance(check, dict)
        or not {"id", "suite", "level", "status", "message"} <= check.keys()
        for check in checks
    ):
        raise ValueError("sheet inspection checks must contain complete check objects")
    decoded = [_check_from_wire(check) for check in checks]
    if any(check.suite not in {"spec", "2d"} for check in decoded) or len(
        {(check.suite, check.id) for check in decoded}
    ) != len(decoded):
        raise ValueError("sheet inspection checks must be unique recognized checks")
    expected_ok = not any(
        check.level == "error" and check.status == "fail" for check in decoded
    )
    if wire["ok"] != expected_ok:
        raise ValueError("sheet inspection reports contradictory check results")
    result.ok, result.metrics, result.checks = wire["ok"], metrics, decoded


def _accept_sheet_scene(
    result: SheetEvidence, wire: dict[str, Any], *, project: str, rev: int, units: str
) -> None:
    raw_elapsed = wire.get("scene_elapsed_ms", 0.0)
    if (
        isinstance(raw_elapsed, bool)
        or not isinstance(raw_elapsed, (int, float))
        or not math.isfinite(raw_elapsed)
        or raw_elapsed < 0
    ):
        result.scene_error = (
            "The clean 2D scene renderer returned an invalid phase duration."
        )
        return
    result.scene_elapsed_ms = float(raw_elapsed)
    scene_error = wire.get("scene_error")
    raw_manifest = wire.get("scene")
    if scene_error is not None:
        result.scene_error = (
            str(scene_error)[:1000]
            if isinstance(scene_error, str)
            else "The clean 2D scene renderer returned a malformed error."
        )
        return
    if not isinstance(raw_manifest, dict):
        result.scene_error = "The clean 2D scene renderer returned no manifest."
        return
    try:
        manifest = SceneManifest(**raw_manifest)
        if (
            len(manifest.scene_id) != 64
            or any(char not in "0123456789abcdef" for char in manifest.scene_id)
            or manifest.project != project
            or manifest.rev != rev
            or manifest.kind != "2d"
            or manifest.units != units
            or manifest.format != "svg-1"
            or manifest.encoding != "gzip"
            or manifest.total_bytes <= 0
            or len(manifest.bbox) != 6
        ):
            raise ValueError("the 2D scene manifest is incomplete or inconsistent")
        if result.inspection_dir is None:
            raise ValueError("the 2D validation snapshot directory is unavailable")
        scene_dir = result.inspection_dir / "scene"
        if scene_dir.is_symlink() or not scene_dir.is_dir():
            raise ValueError("the clean 2D scene directory is missing or unsafe")
        payload = scene_dir / "scene.bin.gz"
        if payload.is_symlink() or not payload.is_file():
            raise ValueError("the clean 2D scene bytes are missing or unsafe")
        data = payload.read_bytes()
        if (
            len(data) != manifest.total_bytes
            or hashlib.sha256(data).hexdigest() != manifest.scene_id
        ):
            raise ValueError(
                "the clean 2D scene bytes do not match the manifest digest"
            )
        manifest_path = scene_dir / "scene.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("the clean 2D scene manifest file is missing or unsafe")
        saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _json_value(saved_manifest)
        for key in (
            "scene_id",
            "project",
            "rev",
            "kind",
            "format",
            "encoding",
            "units",
            "bbox",
            "total_bytes",
            "parts",
        ):
            if saved_manifest.get(key) != getattr(manifest, key):
                raise ValueError(
                    f"the clean 2D scene manifest field {key!r} does not match the inspector response"
                )
        result.scene = manifest
        result.scene_dir = scene_dir
    except Exception as exc:
        result.scene_error = f"{type(exc).__name__}: {exc}"[:1000]


def _accept_response(
    result: GeometryEvidence, wire: dict, units: str, kind: str
) -> None:
    if not isinstance(wire.get("ok"), bool):
        raise ValueError("inspection ok must be boolean")
    metrics = _validated_metrics(wire.get("metrics"), units)
    checks = wire.get("checks")
    if not isinstance(checks, list) or any(
        not isinstance(c, dict)
        or not {"id", "suite", "level", "status", "message"} <= c.keys()
        for c in checks
    ):
        raise ValueError("inspection checks must contain complete check objects")
    decoded = [_check_from_wire(c) for c in checks]
    if any(
        c.suite not in {"hard", "spec", "process", "interference"} for c in decoded
    ) or len({(c.suite, c.id) for c in decoded}) != len(decoded):
        raise ValueError("inspection checks must be unique recognized checks")
    failure = wire.get("failure")
    if failure is not None and (
        not isinstance(failure, dict)
        or failure.get("code") != "INVALID_SOLID"
        or failure.get("phase") != "validate"
        or not isinstance(failure.get("message"), str)
    ):
        raise ValueError("inspection failure is malformed")
    if wire["ok"] and (
        failure is not None
        or not metrics
        or not metrics["valid"]
        or metrics["solids"] < 1
        or (kind == "part" and metrics["solids"] != 1)
        or metrics["volume"] <= 0
        or not decoded
        or any(
            c.suite == "hard" and c.level == "error" and c.status == "fail"
            for c in decoded
        )
    ):
        raise ValueError("inspection reports contradictory success")
    if not wire["ok"] and failure is None:
        raise ValueError("failed inspection requires a failure")
    result.ok, result.metrics, result.checks = wire["ok"], metrics, decoded
    result.failure = _failure_from_wire(failure) if failure is not None else None


def _accept_scene(
    result: GeometryEvidence,
    wire: dict[str, Any],
    *,
    project: str,
    rev: int,
    units: str,
) -> None:
    """Accept a clean scene only when its manifest and bytes match the validated snapshot."""
    raw_elapsed = wire.get("scene_elapsed_ms", 0.0)
    if (
        isinstance(raw_elapsed, bool)
        or not isinstance(raw_elapsed, (int, float))
        or not math.isfinite(raw_elapsed)
        or raw_elapsed < 0
    ):
        result.scene_error = (
            "The clean scene renderer returned an invalid phase duration."
        )
        return
    result.scene_elapsed_ms = float(raw_elapsed)
    scene_error = wire.get("scene_error")
    raw_manifest = wire.get("scene")
    if scene_error is not None:
        result.scene_error = (
            str(scene_error)[:1000]
            if isinstance(scene_error, str)
            else "The clean scene renderer returned a malformed error."
        )
        return
    if not isinstance(raw_manifest, dict):
        result.scene_error = "The clean scene renderer returned no manifest."
        return
    try:
        manifest = SceneManifest(**raw_manifest)
        if (
            len(manifest.scene_id) != 64
            or any(char not in "0123456789abcdef" for char in manifest.scene_id)
            or manifest.project != project
            or manifest.rev != rev
            or manifest.kind != "3d"
            or manifest.units != units
            or manifest.format != "tcv-5"
            or manifest.encoding != "gzip"
            or manifest.total_bytes <= 0
            or not manifest.parts
        ):
            raise ValueError("the scene manifest is incomplete or inconsistent")
        if result.inspection_dir is None:
            raise ValueError("the validation snapshot directory is unavailable")
        scene_dir = result.inspection_dir / "scene"
        if scene_dir.is_symlink() or not scene_dir.is_dir():
            raise ValueError("the clean scene directory is missing or unsafe")
        payload = scene_dir / "scene.bin.gz"
        if payload.is_symlink() or not payload.is_file():
            raise ValueError("the clean scene bytes are missing or unsafe")
        data = payload.read_bytes()
        if (
            len(data) != manifest.total_bytes
            or hashlib.sha256(data).hexdigest() != manifest.scene_id
        ):
            raise ValueError("the clean scene bytes do not match the manifest digest")
        manifest_path = scene_dir / "scene.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("the clean scene manifest file is missing or unsafe")
        saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _json_value(saved_manifest)
        for key in (
            "scene_id",
            "project",
            "rev",
            "kind",
            "format",
            "encoding",
            "units",
            "bbox",
            "total_bytes",
            "triangles",
            "parts",
        ):
            if saved_manifest.get(key) != getattr(manifest, key):
                raise ValueError(
                    f"the clean scene manifest field {key!r} does not match the validator response"
                )
        result.scene = manifest
        result.scene_dir = scene_dir
    except Exception as exc:
        result.scene_error = f"{type(exc).__name__}: {exc}"[:1000]


def _validated_metrics(value: Any, units: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("inspection metrics must be an object")
    _json_value(value)
    if value:
        if value.get("units") != units or not isinstance(value.get("valid"), bool):
            raise ValueError("inspection metrics have invalid units or validity")
        for key in ("solids", "faces", "edges", "vertices"):
            if type(value.get(key)) is not int or value[key] < 0:
                raise ValueError(f"inspection {key} must be a non-negative integer")
        for key in ("volume", "area"):
            if type(value.get(key)) not in (int, float) or value[key] < 0:
                raise ValueError(f"inspection {key} must be a non-negative number")
        for key in ("bbox", "center_of_mass"):
            vector = value.get(key)
            if (
                not isinstance(vector, list)
                or len(vector) != 3
                or any(type(v) not in (int, float) for v in vector)
            ):
                raise ValueError(f"inspection {key} must contain three numbers")
        if any(value < 0 for value in value["bbox"]):
            raise ValueError("inspection bbox dimensions must be non-negative")
    return value


def _progress_response(stdout: bytes) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_METRICS_MARKER):
            value = json.loads(line[len(_METRICS_MARKER) :])
            if not isinstance(value, dict):
                raise ValueError("validator metrics progress must be an object")
            return value
    raise ValueError("validator did not emit trusted metrics before timeout")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _json_value(value: Any) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValueError("inspection inputs and metrics must be finite plain JSON")


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("inspection budget exhausted")
    return seconds


def _file_digest(path: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            _remaining(deadline)
            digest.update(block)
    _remaining(deadline)
    return digest.hexdigest()


def _response(stdout: bytes) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_MARKER):
            wire = json.loads(line[len(_MARKER) :])
            if isinstance(wire, dict):
                return wire
    raise ValueError("BREP inspector returned no valid protocol result")
