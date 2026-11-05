"""Kernel-free parent API for inspecting a completed model's BREP independently."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
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
from .jobs import _check_from_wire, _decode, _drain_killed_worker, _failure_from_wire, _kill_worker
from .types import Check, Failure

_MARKER = b"AAD_GEOMETRY_EVIDENCE:"
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from aad.engine._validation_worker import main; main()"
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
    failure: Failure | None = None
    snapshot_sha256: str | None = None
    input_sha256: str | None = None
    inspection_dir: Path | None = None
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: float = 0.0


def inspect_brep(
    brep_path: str | Path, *, candidate_dir: str | Path, kind: str,
    units: str, timeout_s: float = 60.0, spec: dict[str, Any] | None = None,
    memory_limit_bytes: int | None = None,
    process: str | None = None,
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
            raise ValueError("inspection requires kind part/assembly and explicit units mm/in")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
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
        _json_value(declared)
        # Detach caller mutations before hashing or giving inputs to the child.
        declared = json.loads(_canonical_json(declared))
        candidate = Path(candidate_dir).resolve(strict=True)
        path = Path(brep_path).absolute()
        resolved = path.resolve(strict=True)
        if not candidate.is_dir() or candidate == Path("/") or not resolved.is_relative_to(candidate):
            raise ValueError("BREP must be inside a private candidate directory")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size <= 0:
            raise ValueError("BREP must be a nonempty private regular file without links")
        # Snapshot only after the producing process is gone. A final-component
        # no-follow open and descriptor checks avoid following a replaced link.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_nlink) != (info.st_dev, info.st_ino, 1) or not stat.S_ISREG(opened.st_mode):
                raise ValueError("BREP changed before snapshotting")
            result.inspection_dir = Path(tempfile.mkdtemp(prefix="inspection-", dir=candidate)).resolve()
            snapshot = result.inspection_dir / "part.brep"
            digest = hashlib.sha256()
            with snapshot.open("xb") as target:
                while block := source.read(1024 * 1024):
                    _remaining(deadline)
                    digest.update(block)
                    target.write(block)
            after = os.fstat(source.fileno())
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                raise ValueError("BREP changed while snapshotting")
        result.snapshot_sha256 = digest.hexdigest()
        record = {"protocol": 1, "snapshot_sha256": result.snapshot_sha256, "inputs": declared}
        result.input_sha256 = hashlib.sha256(_canonical_json(record)).hexdigest()
        nonce = secrets.token_hex(24)
        payload = {"record": record, "input_sha256": result.input_sha256, "nonce": nonce}
        phase = "sandbox"
        _remaining(deadline)
        startup_limit = (
            memory.bootstrap_address_space_budget(memory_limit_bytes)
            if memory_limit_bytes is not None
            else None
        )
        child = sandbox.popen(
            [sys.executable, "-I", "-B", "-c", _BOOTSTRAP, str(_SOURCE_ROOT)],
            result.inspection_dir,
            address_space=startup_limit,
        )
        phase = "inspect"
        stdout, stderr = child.communicate(input=_canonical_json(payload) + b"\n", timeout=_remaining(deadline))
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
                result.failure = Failure(code="WORKER_CRASH", phase="crash", feature="unknown",
                                         message=f"The BREP inspector exited with status {child.returncode}; no exit cause was inferred.")
        else:
            phase = "decode"
            wire = _response(stdout)
            if wire.get("nonce") != nonce or wire.get("input_sha256") != result.input_sha256 or wire.get("snapshot_sha256") != result.snapshot_sha256:
                raise ValueError("BREP inspection response is not bound to these inputs and snapshot")
            if _file_digest(snapshot, deadline) != result.snapshot_sha256:
                raise ValueError("BREP snapshot changed during inspection")
            _accept_response(result, wire, units, kind)
            _remaining(deadline)
    except (TimeoutError, subprocess.TimeoutExpired):
        result.failure = Failure(code="TIMEOUT", phase="timeout", feature="unknown",
                                 message=f"BREP inspection exceeded its {timeout_s:g} second budget.")
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
        result.failure = Failure(code=code, phase=failure_phase, feature="unknown",
                                 message="Independent BREP inspection could not be completed.",
                                 kernel_message=f"{type(exc).__name__}: {exc}")
    finally:
        if child is not None and (not communication_complete or child.poll() is None):
            try:
                _kill_worker(child)
                stdout, stderr = _drain_killed_worker(child)
            except OSError as exc:
                result.failure = Failure(code="WORKER_CRASH", phase="crash", feature="unknown",
                                         message="BREP inspector cleanup failed.", kernel_message=f"{type(exc).__name__}: {exc}")
        result.stdout, result.stderr = _decode(stdout), _decode(stderr)
        result.elapsed_ms = (time.monotonic() - started) * 1000
        if result.failure is not None:
            result.ok = False
    return result


def _accept_response(result: GeometryEvidence, wire: dict, units: str, kind: str) -> None:
    if not isinstance(wire.get("ok"), bool):
        raise ValueError("inspection ok must be boolean")
    metrics = wire.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("inspection metrics must be an object")
    _json_value(metrics)
    if metrics:
        if metrics.get("units") != units or not isinstance(metrics.get("valid"), bool):
            raise ValueError("inspection metrics have invalid units or validity")
        for key in ("solids", "faces", "edges", "vertices"):
            if type(metrics.get(key)) is not int or metrics[key] < 0:
                raise ValueError(f"inspection {key} must be a non-negative integer")
        for key in ("volume", "area"):
            if type(metrics.get(key)) not in (int, float) or metrics[key] < 0:
                raise ValueError(f"inspection {key} must be a non-negative number")
        for key in ("bbox", "center_of_mass"):
            vector = metrics.get(key)
            if not isinstance(vector, list) or len(vector) != 3 or any(type(v) not in (int, float) for v in vector):
                raise ValueError(f"inspection {key} must contain three numbers")
        if any(value < 0 for value in metrics["bbox"]):
            raise ValueError("inspection bbox dimensions must be non-negative")
    checks = wire.get("checks")
    if not isinstance(checks, list) or any(not isinstance(c, dict) or not {"id", "suite", "level", "status", "message"} <= c.keys() for c in checks):
        raise ValueError("inspection checks must contain complete check objects")
    decoded = [_check_from_wire(c) for c in checks]
    if any(c.suite not in {"hard", "spec", "process"} for c in decoded) or len({(c.suite, c.id) for c in decoded}) != len(decoded):
        raise ValueError("inspection checks must be unique recognized checks")
    failure = wire.get("failure")
    if failure is not None and (not isinstance(failure, dict) or failure.get("code") != "INVALID_SOLID" or failure.get("phase") != "validate" or not isinstance(failure.get("message"), str)):
        raise ValueError("inspection failure is malformed")
    if wire["ok"] and (failure is not None or not metrics or not metrics["valid"] or metrics["solids"] < 1 or (kind == "part" and metrics["solids"] != 1) or metrics["volume"] <= 0 or not decoded or any(c.suite == "hard" and c.level == "error" and c.status == "fail" for c in decoded)):
        raise ValueError("inspection reports contradictory success")
    if not wire["ok"] and failure is None:
        raise ValueError("failed inspection requires a failure")
    result.ok, result.metrics, result.checks = wire["ok"], metrics, decoded
    result.failure = _failure_from_wire(failure) if failure is not None else None


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


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
            wire = json.loads(line[len(_MARKER):])
            if isinstance(wire, dict):
                return wire
    raise ValueError("BREP inspector returned no valid protocol result")
