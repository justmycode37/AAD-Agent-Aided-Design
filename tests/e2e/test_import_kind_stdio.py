"""A project created by a multi-solid STEP import is an assembly over MCP stdio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .test_smoke import WAIT_S, call, initialize


@pytest.mark.usefixtures("engine")
def test_import_without_project_infers_assembly_from_multisolid_step(mcp_process: Any) -> None:
    initialize(mcp_process)
    assembly_source = """from aad import *
result = assembly('source')
result.add('left', Box(10, 10, 10))
result.add('right', Box(10, 10, 10), at=Pos(20, 0, 0))
"""
    created = call(mcp_process, "aad_new", name="source-assembly", kind="assembly")
    assert created["structuredContent"]["ok"], created
    built = call(
        mcp_process,
        "aad_build",
        project="source-assembly",
        source=assembly_source,
        wait_s=WAIT_S,
    )
    assert built["structuredContent"]["ok"], built
    exported = call(
        mcp_process,
        "aad_export",
        project="source-assembly",
        formats=["step"],
        allow_unverified=True,
    )
    assert exported["structuredContent"]["ok"], exported
    step_path = exported["structuredContent"]["artifacts"]["step"]

    imported = call(mcp_process, "aad_import", name="two-solid-import", path=step_path)
    imported_envelope = imported["structuredContent"]
    assert imported_envelope["ok"], imported
    assert imported_envelope["imported"]["solids"] == 2, imported
    assert imported_envelope["imported"]["project_kind"] == "assembly", imported
    assert "assembly project" in imported_envelope["summary"], imported

    imported_source = f"from aad import *\nresult = asset({Path(step_path).name!r})\n"
    rebuilt = call(
        mcp_process,
        "aad_build",
        project="two-solid-import",
        source=imported_source,
        wait_s=WAIT_S,
    )
    rebuilt_envelope = rebuilt["structuredContent"]
    assert rebuilt_envelope["ok"], rebuilt
    assert rebuilt_envelope["metrics"]["solids"] == 2, rebuilt
