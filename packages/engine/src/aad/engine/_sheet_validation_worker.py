"""Check one snapshotted Sheet2D without running model code."""

from __future__ import annotations

import contextlib
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from .validation import _MARKER, _canonical_json, _file_digest, _json_value
from .types import Check, ModelContext
from .memory import is_memory_exhaustion


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value {value} is not allowed")


def _sheet_metrics(evidence: dict, units: str) -> dict:
    bbox = evidence.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 3:
        return {"units": units, "solids": 0, "faces": 0, "edges": 0, "valid": False}
    contour_areas = []
    for contour in evidence.get("closed_contours") or []:
        area = contour.get("area") if isinstance(contour, dict) else None
        depth = contour.get("nesting_depth") if isinstance(contour, dict) else None
        if (
            type(area) not in (int, float)
            or not math.isfinite(float(area))
            or type(depth) is not int
        ):
            continue
        contour_areas.append(float(area) * (-1.0 if depth % 2 else 1.0))
    edges = sum(
        len(entity.get("edges") or [])
        for entity in evidence.get("cut_entities") or []
        if isinstance(entity, dict)
    )
    state = evidence.get("status") or {}
    metrics = {
        "units": units,
        "bbox": [float(value) for value in bbox],
        "solids": 0,
        "faces": len(evidence.get("closed_contours") or []),
        "edges": edges,
        "valid": bool(state.get("complete")),
    }
    if contour_areas:
        metrics["area"] = math.fsum(contour_areas)
    return metrics


def main() -> None:
    payload = json.loads(sys.stdin.buffer.readline(), parse_constant=_reject_constant)
    record = payload["record"]
    digest = hashlib.sha256(_canonical_json(record)).hexdigest()
    if digest != payload["input_sha256"]:
        raise ValueError("sheet inspection input hash mismatch")
    snapshot_hash = _file_digest(Path("sheet.json"), math.inf)
    if snapshot_hash != record["snapshot_sha256"]:
        raise ValueError("sheet inspection snapshot hash mismatch")
    wire = {
        "nonce": payload["nonce"],
        "input_sha256": digest,
        "snapshot_sha256": snapshot_hash,
        "ok": False,
        "metrics": {},
        "checks": [],
        "scene": None,
        "scene_error": None,
        "scene_elapsed_ms": 0.0,
    }
    with contextlib.redirect_stdout(sys.stderr):
        try:
            from aad_drafting import (
                Sheet2D,
                checks as sheet_checks,
                measurement_evidence,
            )

            inputs = record["inputs"]
            raw_sheet = json.loads(
                Path("sheet.json").read_text(encoding="utf-8"),
                parse_constant=_reject_constant,
            )
            _json_value(raw_sheet)
            sheet = Sheet2D.from_dict(raw_sheet)
            if sheet.to_dict() != raw_sheet:
                raise ValueError("saved sheet JSON is not in canonical Sheet2D form")
            ctx = ModelContext(
                kind="2d",
                units=inputs["units"],
                sheet=sheet,
                spec=inputs["spec"],
                process=inputs.get("process"),
                job_dir=Path.cwd(),
            )
            ctx.project = inputs["project"]
            ctx.rev = inputs["rev"]
            ctx.status = "draft"
            measured = measurement_evidence(ctx)
            metrics = _sheet_metrics(measured, ctx.units)
            ctx.metrics = metrics
            checks = [Check(**item) for item in sheet_checks(ctx)]
            if inputs["spec"].get("requirements"):
                try:
                    from aad_quality import spec_checks
                except ModuleNotFoundError as exc:
                    if exc.name != "aad_quality":
                        raise
                    checks.append(
                        Check(
                            id="spec_evaluator_unavailable",
                            suite="spec",
                            level="error",
                            status="skip",
                            message="The aad-quality evaluator is not installed; 2D spec requirements were not checked.",
                        )
                    )
                else:
                    try:
                        checks.extend(spec_checks(ctx))
                    except Exception as exc:
                        if is_memory_exhaustion(exc):
                            raise
                        checks.append(
                            Check(
                                id="spec_suite_failed",
                                suite="spec",
                                level="error",
                                status="skip",
                                message=f"The 2D spec evaluator could not complete ({type(exc).__name__}).",
                            )
                        )
            wire["metrics"] = metrics
            wire["checks"] = [asdict(check) for check in checks]
            wire["ok"] = not any(
                check.level == "error" and check.status == "fail" for check in checks
            )
            ctx.checks_summary = {
                status: sum(check.status == status for check in checks)
                for status in ("pass", "fail", "warn", "skip", "not_applicable")
            }
            scene_started = time.perf_counter()
            try:
                from aad_drafting import make_scene

                wire["scene"] = make_scene(ctx, Path.cwd() / "scene")
            except Exception as exc:
                if is_memory_exhaustion(exc):
                    raise
                wire["scene_error"] = f"{type(exc).__name__}: {exc}"
            finally:
                wire["scene_elapsed_ms"] = (time.perf_counter() - scene_started) * 1000
        except Exception as exc:
            if is_memory_exhaustion(exc):
                raise
            wire["metrics"] = {}
            wire["checks"] = [
                asdict(
                    Check(
                        id="sheet_validation_failed",
                        suite="2d",
                        level="error",
                        status="fail",
                        message=f"Independent Sheet2D validation failed ({type(exc).__name__}: {exc}).",
                    )
                )
            ]
    sys.stdout.buffer.write(b"\n" + _MARKER + _canonical_json(wire) + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
