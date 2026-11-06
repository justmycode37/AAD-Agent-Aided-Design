"""A project created by a multi-solid STEP import is an assembly over MCP stdio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from build123d import Box, export_step
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


@pytest.mark.parametrize("suffix", [".dxf", ".svg"])
def test_import_2d_file_creates_2d_project_and_builds_as_sheet(
    mcp_process: Any, tmp_path: Path, suffix: str
) -> None:
    initialize(mcp_process)
    source_path = tmp_path / f"outline{suffix}"
    if suffix == ".dxf":
        import ezdxf

        document = ezdxf.new("R2013")
        document.header["$INSUNITS"] = 4
        document.modelspace().add_lwpolyline(
            [(0, 0), (20, 0), (20, 10), (0, 10)], close=True
        )
        document.saveas(source_path)
    else:
        source_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="10mm" '
            'viewBox="0 0 20 10"><rect id="outline" width="20" height="10"/></svg>',
            encoding="utf-8",
        )

    imported = call(mcp_process, "aad_import", name="outline-import", path=str(source_path))
    envelope = imported["structuredContent"]
    assert envelope["ok"], imported
    assert envelope["imported"]["project_kind"] == "2d", imported
    assert ".to_sheet()" in envelope["summary"], imported
    assert ".to_sheet()" in envelope["imported"]["history_notice"], imported

    source = f"from aad import *\nresult = asset({source_path.name!r}).to_sheet()\n"
    built = call(
        mcp_process,
        "aad_build",
        project="outline-import",
        source=source,
        wait_s=WAIT_S,
    )
    result = built["structuredContent"]
    assert result["ok"], built
    assert result["metrics"]["bbox"][:2] == pytest.approx([20, 10])


def test_single_solid_import_result_reports_part_project_kind(
    mcp_process: Any, tmp_path: Path
) -> None:
    initialize(mcp_process)
    step_path = tmp_path / "single-solid.step"
    export_step(Box(10, 10, 10), step_path)

    imported = call(mcp_process, "aad_import", name="single-solid-import", path=str(step_path))
    envelope = imported["structuredContent"]
    assert envelope["ok"], imported
    assert envelope["imported"]["solids"] == 1, imported
    assert envelope["imported"]["project_kind"] == "part", imported
