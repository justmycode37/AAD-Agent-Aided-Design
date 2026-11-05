"""One trusted inspection of snapshotted native geometry, without model code."""

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

_METRICS_MARKER = b"AAD_GEOMETRY_METRICS:"


def main() -> None:
    payload = json.loads(sys.stdin.buffer.readline())
    record = payload["record"]
    digest = hashlib.sha256(_canonical_json(record)).hexdigest()
    if digest != payload["input_sha256"]:
        raise ValueError("inspection input hash mismatch")
    snapshot_hash = _file_digest(Path("part.brep"), math.inf)
    if snapshot_hash != record["snapshot_sha256"]:
        raise ValueError("inspection snapshot hash mismatch")
    wire = {"nonce": payload["nonce"], "input_sha256": digest, "snapshot_sha256": snapshot_hash,
            "ok": False, "metrics": {}, "checks": [], "failure": None}
    # Python/native diagnostics are retained; protocol records start on a new
    # line even when a native library writes directly to fd 1.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            import build123d as bd
            from OCP.BRepCheck import BRepCheck_Analyzer
            from .gate import hard_checks, validator_geometry_checks
            from .types import Check, ModelContext
            try:
                from aad_quality import spec_checks, process_checks
            except ModuleNotFoundError as exc:
                if exc.name != "aad_quality":
                    raise
                spec_checks = process_checks = None

            shape = bd.import_brep("part.brep")
            if shape is None or shape.wrapped is None or shape.wrapped.IsNull():
                raise ValueError("the imported BREP has no native shape")
            solids = list(shape.solids())
            if not solids:
                raise ValueError("the imported BREP has no positive-volume solid")
            volumes = []
            for index, solid in enumerate(solids):
                solid_volume = float(solid.volume)
                if not math.isfinite(solid_volume) or solid_volume <= 0:
                    raise ValueError(f"solid volume at native body index {index} must be finite and positive; observed {solid_volume!r}")
                volumes.append(solid_volume)
            volume = math.fsum(volumes)
            centers = [solid.center(bd.CenterOf.MASS) for solid in solids]
            center = [math.fsum(v * getattr(c, axis) for v, c in zip(volumes, centers, strict=True)) / volume for axis in ("X", "Y", "Z")]
            size = shape.bounding_box().size
            metrics = {"units": record["inputs"]["units"], "bbox": [size.X, size.Y, size.Z],
                       "volume": volume, "area": float(shape.area), "center_of_mass": center,
                       "solids": len(solids), "faces": len(shape.faces()), "edges": len(shape.edges()),
                       "vertices": len(shape.vertices()), "valid": bool(shape.is_valid and BRepCheck_Analyzer(shape.wrapped).IsValid())}
            _json_value(metrics)
            wire["metrics"] = metrics
            progress = {"nonce": payload["nonce"], "input_sha256": digest,
                        "snapshot_sha256": snapshot_hash, "metrics": metrics}
            sys.__stdout__.buffer.write(b"\n" + _METRICS_MARKER + _canonical_json(progress) + b"\n")
            sys.__stdout__.buffer.flush()
            ctx = ModelContext(kind=record["inputs"]["kind"], units=record["inputs"]["units"],
                               spec=record["inputs"]["spec"], process=record["inputs"].get("process"),
                               shape=shape, metrics=metrics, job_dir=Path.cwd())
            checks = hard_checks(ctx)
            checks += validator_geometry_checks(ctx)
            if spec_checks is not None and process_checks is not None:
                checks += spec_checks(ctx)
                checks += process_checks(ctx)
            else:
                requested_process = record["inputs"].get("process") or record["inputs"]["spec"].get("process")
                if record["inputs"]["spec"].get("requirements"):
                    checks.append(Check(id="spec_evaluator_unavailable", suite="spec", level="error", status="skip",
                                        message="The aad-quality evaluator is not installed; spec requirements were not checked."))
                if requested_process:
                    checks.append(Check(id="process_evaluator_unavailable", suite="process", level="error", status="skip",
                                        message="The aad-quality evaluator is not installed; process requirements were not checked."))
            wire["checks"] = [asdict(check) for check in checks]
            if not metrics["valid"] or (ctx.kind == "part" and len(solids) != 1) or any(c.level == "error" and c.status == "fail" for c in checks):
                raise ValueError("independently loaded geometry failed native validity, body-count or hard checks")
            wire["ok"] = True
            if payload.get("render_scene") is True:
                scene_started = time.perf_counter()
                try:
                    from aad_render import make_scene

                    ctx.project = payload["scene_project"]
                    ctx.rev = payload["scene_rev"]
                    ctx.status = "draft"
                    ctx.checks_summary = {
                        status: sum(check.status == status for check in checks)
                        for status in ("pass", "fail", "warn", "skip", "not_applicable")
                    }
                    scene_dir = Path.cwd() / "scene"
                    manifest = make_scene(ctx, scene_dir)
                    scene_bytes = (scene_dir / "scene.bin.gz").read_bytes()
                    if hashlib.sha256(scene_bytes).hexdigest() != manifest.scene_id or len(scene_bytes) != manifest.total_bytes:
                        raise ValueError("scene manifest does not match the rendered scene bytes")
                    wire["scene"] = asdict(manifest)
                except Exception as exc:
                    wire["scene_error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    wire["scene_elapsed_ms"] = (time.perf_counter() - scene_started) * 1000
        except Exception as exc:
            wire["failure"] = {"code": "INVALID_SOLID", "phase": "validate", "feature": "unknown",
                               "message": "The BREP could not pass independent geometry inspection.",
                               "kernel_message": f"{type(exc).__name__}: {exc}"}
    sys.stdout.buffer.write(b"\n" + _MARKER + _canonical_json(wire) + b"\n")
    sys.stdout.buffer.flush()
