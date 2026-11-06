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
    return importlib.util.find_spec("aad.engine.jobs") is not None


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
    from .. import config
    from aad.engine import memory
    from aad.engine.jobs import run_build as engine_run_build  # kernel-free import
    from aad.engine.types import BuildRequest, BuildResult, Failure

    try:
        memory_limit_bytes = config.engine_memory_limit_bytes()
    except memory.AddressSpaceUnavailable as exc:
        return dataclasses.asdict(
            BuildResult(
                ok=False,
                status="failed",
                summary=f"The configured engine.memory limit cannot run on this platform: {exc}",
                failure=Failure(
                    code="SANDBOX_DENIED",
                    phase="sandbox",
                    message=f"The configured engine.memory limit cannot run on this platform: {exc}",
                    kernel_message=f"{type(exc).__name__}: {exc}",
                ),
                job_dir=job_dir,
            )
        )

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
        ),
        memory_limit_bytes=memory_limit_bytes,
    )
    return dataclasses.asdict(result)


def stub_scene(brep: Path, out_dir: Path, project: str, rev: int, units: str) -> dict[str, Any]:
    """Tessellate with the M0 stub, in a separate process so that a crash stays contained."""
    from .. import config
    from aad.engine import memory

    memory_limit_bytes = config.engine_memory_limit_bytes()
    if memory_limit_bytes is None:
        raise memory.AddressSpaceUnavailable("stub scene generation requires a supported engine.memory policy")
    out_dir.mkdir(parents=True, exist_ok=True)
    engine_root = Path(memory.__file__).resolve().parents[2]
    platform_root = Path(__file__).resolve().parents[2]
    bootstrap = (
        "import runpy, sys; sys.path[:0]=[sys.argv.pop(1), sys.argv.pop(1)]; "
        "from aad.engine.memory import install_worker_memory_budget; "
        "install_worker_memory_budget(int(sys.argv.pop(1))); "
        "runpy.run_module('aad_platform.stubs.scene', run_name='__main__')"
    )
    argv = [
        sys.executable, "-I", "-B", "-c", bootstrap,
        str(platform_root), str(engine_root), str(memory_limit_bytes),
        str(brep), str(out_dir), project, str(rev), units,
    ]
    argv = memory._bootstrap_argv(
        argv, memory.bootstrap_address_space_budget(memory_limit_bytes), out_dir
    )
    subprocess.run(
        argv,
        check=True,
        capture_output=True,
        timeout=120,
    )
    return json.loads((out_dir / "scene.json").read_text())
