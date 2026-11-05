"""Killable, isolated process boundary for selected-task artifact audits."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

from .corpus.loader import load_corpus
from .oracles import CheckObservation, StageAudit, TaskAudit
from .harness.aad_benchmark_harness.security import path_is_within


class AuditWorkerError(RuntimeError):
    """The independent audit process failed, timed out or returned invalid data."""


def _validated_evidence(
    evidence: Mapping[str, Mapping[str, Any]], allowed_roots: Sequence[Path]
) -> dict[str, dict[str, Any]]:
    roots = tuple(root.resolve(strict=True) for root in allowed_roots)
    if not roots:
        raise ValueError("at least one host-owned artifact root is required")
    result: dict[str, dict[str, Any]] = {}
    for stage_id, row in evidence.items():
        if not isinstance(stage_id, str) or not isinstance(row, Mapping):
            raise ValueError("stage evidence must be mappings keyed by stage ID")
        normalized: dict[str, Any] = {}
        for key in ("brep_path", "sheet_json_path", "drawing_svg_path"):
            raw_path = row.get(key)
            if raw_path is None:
                normalized[key] = None
                continue
            raw = Path(raw_path)
            if not raw.is_absolute():
                raise ValueError(f"audit input {key} must be an absolute host artifact path")
            # Reject symlinks in every path component before resolving; checking
            # lstat() only after resolve() would inspect the target, not the link.
            current = Path(raw.anchor)
            for component in raw.parts[1:]:
                current = current / component
                if stat.S_ISLNK(current.lstat().st_mode):
                    raise ValueError(f"audit input {key} contains a symlink path component")
            path = raw.resolve(strict=True)
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"audit input {key} must be a regular, non-symlink file")
            if metadata.st_size > 256 * 1024 * 1024:
                raise ValueError(f"audit input {key} exceeds the 256 MiB audit file limit")
            if not any(path_is_within(path, root) for root in roots):
                raise ValueError(f"audit input {key} escapes every host-owned artifact root")
            normalized[key] = str(path)
        response = row.get("response_text", "")
        if not isinstance(response, str):
            raise ValueError("response_text must be text")
        normalized["response_text"] = response
        result[stage_id] = normalized
    return result


def audit_task_in_process(
    task_id: str,
    stage_evidence: Mapping[str, Mapping[str, Any]],
    *,
    allowed_roots: Sequence[Path],
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Run one task's oracle in a fresh Python process with a hard timeout."""
    corpus = load_corpus()
    expected = next((task for task in corpus["tasks"] if task["id"] == task_id), None)
    if expected is None:
        raise ValueError(f"unknown pilot task {task_id!r}")
    evidence = _validated_evidence(stage_evidence, allowed_roots)
    expected_stage_ids = {stage["id"] for stage in expected["stages"]}
    if set(evidence) - expected_stage_ids:
        raise ValueError("audit evidence includes an unknown stage")
    payload = json.dumps(
        {"task_id": task_id, "stage_evidence": evidence},
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("audit request exceeds the 4 MiB host-side limit")

    repo_root = Path(__file__).resolve().parents[2]
    child_code = (
        "import sys; sys.path.insert(0, sys.argv.pop(1)); "
        "from evals.benchmark.audit_worker import main; raise SystemExit(main())"
    )
    with tempfile.TemporaryDirectory(prefix="aad-pilot-audit-") as temp_root:
        temp = Path(temp_root)
        env = {
            "HOME": str(temp),
            "TMPDIR": str(temp),
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", child_code, str(repo_root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=temp,
            env=env,
            close_fds=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(payload, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                process.kill()
            stdout, stderr = process.communicate()
            raise AuditWorkerError(f"independent audit exceeded {timeout_seconds:g} seconds and was killed") from error
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace")[-2000:]
            raise AuditWorkerError(f"independent audit exited {process.returncode}: {message}")
        if len(stdout) > 8 * 1024 * 1024:
            raise AuditWorkerError("independent audit output exceeded the 8 MiB result limit")
        try:
            result = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuditWorkerError("independent audit returned invalid JSON") from error
        if not isinstance(result, dict) or result.get("task_id") != task_id:
            raise AuditWorkerError("independent audit returned the wrong task record")
        return result


def unevaluated_task_audit(task: Mapping[str, Any], reason: str) -> TaskAudit:
    """Represent an unavailable oracle run without turning it into a pass."""
    stages = []
    for stage in task["stages"]:
        coverage = str(stage["oracle"]["coverage"])
        checks = tuple(
            CheckObservation(
                check_id=str(check["id"]),
                check_type=str(check["type"]),
                status="unsupported",
                coverage=coverage,
                reason=reason,
                expected={key: value for key, value in check.items() if key not in {"id", "type"}},
                actual=None,
            )
            for check in stage["oracle"]["checks"]
        )
        stages.append(StageAudit(
            task_id=str(task["id"]),
            stage_id=str(stage["id"]),
            expected_outcome=str(stage["expected_outcome"]),
            declared_coverage=coverage,
            effective_coverage="bounded",
            verdict="indeterminate",
            checks=checks,
        ))
    return TaskAudit(str(task["id"]), "indeterminate", "bounded", tuple(stages))


__all__ = ["AuditWorkerError", "audit_task_in_process", "unevaluated_task_audit"]
