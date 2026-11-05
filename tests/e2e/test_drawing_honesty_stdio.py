"""Drawing refusal and assembly defaults through the real MCP stdio adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .test_smoke import call, initialize


WAIT_S = 120
pytestmark = pytest.mark.usefixtures("engine")


def test_failed_2d_drawing_reports_hints_and_writes_no_files(mcp_process: Any, aad_env: dict[str, str]) -> None:
    initialize(mcp_process)
    created = call(mcp_process, "aad_new", name="triangle-sheet", kind="2d")
    assert created["structuredContent"]["ok"], created

    source = """
from aad_drafting import sheet2d
sheet = sheet2d("outline")
sheet.polyline("triangle", [(0, 0), (30, 0), (10, 20)], closed=True)
"""
    built = call(mcp_process, "aad_build", project="triangle-sheet", source=source, wait_s=WAIT_S)
    assert built["structuredContent"]["ok"], built

    response = call(mcp_process, "aad_drawing", project="triangle-sheet", formats=["svg"])
    result = response["structuredContent"]
    assert response["isError"] is True
    assert result["ok"] is False
    assert result.get("artifacts", {}) == {}
    assert "preview" not in result
    assert result["summary"].startswith("Drawing not written")
    failed = [check for check in result["checks"] if check["status"] == "fail"]
    undimensioned = next(check for check in failed if check["id"] == "2d.undimensioned_features")
    assert "dimensions='none'" in undimensioned["fix_hint"]

    output_dir = (
        Path(aad_env["HOME"])
        / "AAD"
        / "triangle-sheet"
        / "exports"
        / "triangle-sheet-r1-drawing"
    )
    assert output_dir.is_dir()
    assert list(output_dir.glob("drawing.*")) == []


def test_assembly_drawing_defaults_to_overall_dimensions_and_bom(mcp_process: Any) -> None:
    initialize(mcp_process)
    created = call(mcp_process, "aad_new", name="assembly-drawing", kind="assembly")
    assert created["structuredContent"]["ok"], created

    source = """
from aad import *
asm = assembly("sample")
asm.add("base", Box(20, 20, 2), part_number="BASE-1")
asm.add("post", Box(4, 4, 8), at=Pos(0, 0, 6), part_number="POST-1")
asm.add("pin", Cylinder(1, 8), at=Pos(6, 6, 6), part_number="PIN-1")
result = asm
"""
    built = call(mcp_process, "aad_build", project="assembly-drawing", source=source, wait_s=WAIT_S)
    assert built["structuredContent"]["ok"], built

    response = call(mcp_process, "aad_drawing", project="assembly-drawing", formats=["svg"])
    result = response["structuredContent"]
    assert not response.get("isError"), response
    assert result["ok"] is True
    assert "3 geometry-derived dimensions" in result["summary"]
    assert "3 BOM rows from assembly children" in result["summary"]
    assert set(result["artifacts"]) == {"svg"}


def test_layout_failure_suppresses_files_and_explains_how_to_retry(mcp_process: Any, aad_env: dict[str, str]) -> None:
    initialize(mcp_process)
    created = call(mcp_process, "aad_new", name="crowded-drawing")
    assert created["structuredContent"]["ok"], created
    built = call(
        mcp_process,
        "aad_build",
        project="crowded-drawing",
        source="from build123d import Box\nresult = Box(30, 20, 2)\n",
        wait_s=WAIT_S,
    )
    assert built["structuredContent"]["ok"], built

    output_dir = (
        Path(aad_env["HOME"])
        / "AAD"
        / "crowded-drawing"
        / "exports"
        / "crowded-drawing-r1-drawing"
    )
    initial = call(
        mcp_process,
        "aad_drawing",
        project="crowded-drawing",
        views=["front"],
        dimensions="none",
        formats=["svg"],
    )
    assert initial["structuredContent"]["ok"] is True, initial
    assert (output_dir / "drawing.svg").is_file()

    response = call(
        mcp_process,
        "aad_drawing",
        project="crowded-drawing",
        sheet="A4",
        views=["front"],
        dimensions="none",
        notes=["Keep this assembly note visible on the sheet."] * 100,
        formats=["svg"],
    )
    result = response["structuredContent"]
    assert response["isError"] is True
    assert result["ok"] is False
    assert "Drawing not written" in result["summary"]
    assert "larger sheet" in result["summary"]
    assert result.get("artifacts", {}) == {}
    assert "preview" not in result
    layout_failures = [
        check for check in result["checks"]
        if check["status"] == "fail" and check["id"] in {"layout_fit", "schedule_bounds", "schedule_text_overflow"}
    ]
    assert layout_failures, result
    assert all("fix_hint" in check for check in layout_failures)

    assert output_dir.is_dir()
    assert list(output_dir.glob("drawing.*")) == []
