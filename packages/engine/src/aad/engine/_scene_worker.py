"""Render a viewer scene from a BREP in a clean sandboxed process."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

_MARKER = b"AAD_BREP_SCENE:"


def main() -> None:
    payload = json.loads(sys.stdin.buffer.readline())
    brep = Path(payload["brep"])
    digest = hashlib.sha256(brep.read_bytes()).hexdigest()
    if digest != payload["brep_sha256"]:
        raise ValueError("scene input does not match the validated BREP hash")
    wire: dict = {"nonce": payload["nonce"], "brep_sha256": digest, "scene": None}
    try:
        import build123d as bd
        from aad_render import make_scene
        from .types import ModelContext

        shape = bd.import_brep(str(brep))
        if shape is None or shape.wrapped is None or shape.wrapped.IsNull():
            raise ValueError("the saved BREP did not load as a shape")
        context = ModelContext(
            project_dir=Path.cwd(), rev=payload["rev"], kind=payload["kind"],
            units=payload["units"], shape=shape, metrics=payload["metrics"],
            job_dir=Path.cwd(),
        )
        context.project = payload["project"]
        context.status = payload["status"]
        context.checks_summary = payload["checks_summary"]
        output = Path.cwd() / "scene"
        manifest = make_scene(context, output)
        scene_bytes = (output / "scene.bin.gz").read_bytes()
        scene_hash = hashlib.sha256(scene_bytes).hexdigest()
        if scene_hash != manifest.scene_id or len(scene_bytes) != manifest.total_bytes:
            raise ValueError("rendered scene manifest does not match its bytes")
        wire["scene"] = asdict(manifest)
    except Exception as exc:
        wire["error"] = f"{type(exc).__name__}: {exc}"
    sys.stdout.buffer.write(b"\n" + _MARKER + json.dumps(wire, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    sys.stdout.buffer.flush()

