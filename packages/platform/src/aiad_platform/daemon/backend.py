"""How the daemon reaches the kernel. Nothing here imports it in the daemon process."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def engine_available() -> bool:
    return importlib.util.find_spec("aiad.engine.jobs") is not None


def run_build(
    *,
    source: str,
    project_dir: Path,
    job_dir: Path,
    rev: int,
    kind: str,
    units: str,
    params: dict[str, Any],
    spec: dict[str, Any],
    material: str | None,
    process: str | None,
    preview: bool,
    timeout_s: float,
) -> dict[str, Any]:
    """Run one build in an S1 worker. Blocking; call it from a thread."""
    from aiad.engine.jobs import run_build as engine_run_build  # kernel-free import
    from aiad.engine.types import BuildRequest

    result = engine_run_build(
        BuildRequest(
            source=source,
            project_dir=project_dir,
            job_dir=job_dir,
            rev=rev,
            kind=kind,
            units=units,
            params=params,
            spec=spec,
            material=material,
            process=process,
            timeout_s=timeout_s,
            preview=preview,
        )
    )
    return dataclasses.asdict(result)


def stub_scene(brep: Path, out_dir: Path, project: str, rev: int, units: str) -> dict[str, Any]:
    """Tessellate with the M0 stub, in a separate process so that a crash stays contained."""
    subprocess.run(
        [sys.executable, "-m", "aiad_platform.stubs.scene", str(brep), str(out_dir), project, str(rev), units],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return json.loads((out_dir / "scene.json").read_text())
