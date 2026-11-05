"""Create canonical viewer scenes from saved BREP data, without model code."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

from . import sandbox
from .types import SceneManifest

_MARKER = b"AAD_BREP_SCENE:"
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from aad.engine._scene_worker import main; main()"
)


def render_brep_scene(
    brep_path: str | Path, *, candidate_dir: str | Path, project: str, rev: int,
    kind: str, units: str, status: str, metrics: dict[str, Any],
    checks_summary: dict[str, int], expected_brep_sha256: str | None,
    timeout_s: float = 120.0,
) -> SceneManifest:
    """Tessellate a private BREP snapshot in a clean sandboxed process."""
    from .jobs import _decode, _drain_killed_worker, _kill_worker

    candidate = Path(candidate_dir).resolve(strict=True)
    source = Path(brep_path).absolute()
    try:
        source_info = source.lstat()
    except OSError as exc:
        raise ValueError("scene input must be a private BREP inside the candidate directory") from exc
    resolved_source = source.resolve(strict=True)
    if (not resolved_source.is_relative_to(candidate) or not stat.S_ISREG(source_info.st_mode)
            or source_info.st_nlink != 1 or source_info.st_size <= 0):
        raise ValueError("scene input must be a private BREP inside the candidate directory")
    render_dir = Path(tempfile.mkdtemp(prefix="brep-scene-", dir=candidate)).resolve()
    snapshot = render_dir / "part.brep"
    digest = hashlib.sha256()
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as reader, snapshot.open("xb") as writer:
        opened = os.fstat(reader.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_nlink) != (
                source_info.st_dev, source_info.st_ino, 1) or not stat.S_ISREG(opened.st_mode):
            raise ValueError("scene BREP changed before snapshotting")
        while block := reader.read(1024 * 1024):
            digest.update(block)
            writer.write(block)
    brep_hash = digest.hexdigest()
    if expected_brep_sha256 is not None and brep_hash != expected_brep_sha256:
        shutil.rmtree(render_dir, ignore_errors=True)
        raise ValueError("saved BREP hash changed after validation; the verified scene was withheld")
    nonce = secrets.token_hex(24)
    payload = {
        "nonce": nonce, "brep": str(snapshot), "brep_sha256": brep_hash,
        "project": project, "rev": rev, "kind": kind, "units": units,
        "status": status, "metrics": metrics, "checks_summary": checks_summary,
    }
    process = None
    started = time.monotonic()
    try:
        process = sandbox.popen(
            [sys.executable, "-I", "-B", "-c", _BOOTSTRAP, str(_SOURCE_ROOT)],
            render_dir, read_paths=(snapshot,),
        )
        try:
            stdout, stderr = process.communicate(
                input=(json.dumps(payload, allow_nan=False, separators=(",", ":")) + "\n").encode(),
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            _kill_worker(process)
            _drain_killed_worker(process)
            raise TimeoutError(f"clean BREP scene rendering exceeded its {timeout_s:g} second budget")
        if process.returncode != 0:
            raise RuntimeError(f"clean BREP scene renderer exited with status {process.returncode}: {_decode(stderr)[-2000:]}")
        wire = _response(stdout)
        if wire.get("nonce") != nonce or wire.get("brep_sha256") != brep_hash:
            raise ValueError("clean BREP scene response is not bound to the validated artifact")
        if wire.get("error"):
            raise RuntimeError(f"clean BREP scene rendering failed: {wire['error']}")
        data = wire.get("scene")
        if not isinstance(data, dict):
            raise ValueError("clean BREP scene renderer returned no manifest")
        manifest = SceneManifest(**data)
        if (
            len(manifest.scene_id) != 64
            or any(char not in "0123456789abcdef" for char in manifest.scene_id)
            or manifest.project != project or manifest.rev != rev
            or manifest.kind != "3d" or manifest.units != units
            or manifest.encoding != "gzip" or manifest.format != "tcv-5"
            or manifest.total_bytes <= 0 or not manifest.parts
        ):
            raise ValueError("clean BREP scene manifest is incomplete or inconsistent")
        scene_source = render_dir / "scene"
        blob = scene_source / "scene.bin.gz"
        if not blob.is_file() or hashlib.sha256(blob.read_bytes()).hexdigest() != manifest.scene_id:
            raise ValueError("clean BREP scene bytes do not match the manifest digest")
        target = candidate / "scene"
        try:
            target_info = target.lstat()
        except FileNotFoundError:
            target_info = None
        if target_info is not None:
            if target.is_symlink() or not target.is_dir():
                target.unlink()
            else:
                shutil.rmtree(target)
        os.replace(scene_source, target)
        manifest.status = "verified" if status == "verified" else "draft"
        return manifest
    finally:
        if process is not None and process.poll() is None:
            _kill_worker(process)
            _drain_killed_worker(process)
        shutil.rmtree(render_dir, ignore_errors=True)


def _response(stdout: bytes) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_MARKER):
            value = json.loads(line[len(_MARKER):])
            if isinstance(value, dict):
                return value
    raise ValueError("clean BREP scene renderer returned no protocol result")

