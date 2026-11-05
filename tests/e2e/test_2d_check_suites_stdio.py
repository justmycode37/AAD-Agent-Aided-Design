"""2D checks select sheet-aware evaluators through real MCP stdio."""

from __future__ import annotations

import json
from typing import Any

import pytest

from .test_smoke import WAIT_S, call, initialize


@pytest.mark.usefixtures("engine")
def test_2d_check_skips_3d_suites_and_checks_laser_slots(mcp_process: Any) -> None:
    initialize(mcp_process)
    created = call(mcp_process, "aad_new", name="laser-sheet", kind="2d", process="laser")
    assert created["structuredContent"]["ok"], created
    source = """from aad_drafting import sheet2d
sheet = sheet2d('laser-sheet')
sheet.rectangle('outline', 100, 50)
sheet.rectangle('slot', 3, 10, origin=(10, 10))
"""
    spec = """units: mm
process: laser
process_settings:
  kerf_mm: 0.5
  minimum_feature_mm: 5
"""
    built = call(
        mcp_process,
        "aad_build",
        project="laser-sheet",
        source=source,
        spec=spec,
        wait_s=WAIT_S,
    )
    assert built["structuredContent"]["ok"], built

    default = call(mcp_process, "aad_check", project="laser-sheet")["structuredContent"]
    assert default["ok"], default
    assert not any(check["id"] == "shape_present" for check in default["checks"])
    assert {check["suite"] for check in default["checks"]} <= {"2d"}
    minimum_slot = next(check for check in default["checks"] if check["id"] == "2d.laser.min_slot")
    assert minimum_slot["status"] == "fail", minimum_slot
    assert json.loads(minimum_slot["actual"])["openings"][0]["width"] == pytest.approx(3)

    mixed = call(
        mcp_process,
        "aad_check",
        project="laser-sheet",
        suites=["hard", "spec", "process", "interference", "2d"],
    )["structuredContent"]
    assert mixed["ok"], mixed
    skipped_3d = [
        check for check in mixed["checks"]
        if check["suite"] in {"hard", "process", "interference"}
    ]
    assert {check["suite"] for check in skipped_3d} == {"hard", "process", "interference"}
    assert all(check["status"] == "skip" for check in skipped_3d), skipped_3d
    assert all("2D sheet" in check["message"] for check in skipped_3d), skipped_3d
    assert not any("NoneType" in check.get("actual", "") for check in mixed["checks"])
    assert next(check for check in mixed["checks"] if check["id"] == "2d.laser.min_slot")["status"] == "fail"


@pytest.mark.usefixtures("engine")
def test_2d_suite_skips_on_a_3d_project(mcp_process: Any) -> None:
    initialize(mcp_process)
    created = call(mcp_process, "aad_new", name="solid-part", kind="part")
    assert created["structuredContent"]["ok"], created
    built = call(
        mcp_process,
        "aad_build",
        project="solid-part",
        source="from aad import *\nresult = Box(10, 10, 10)\n",
        wait_s=WAIT_S,
    )
    assert built["structuredContent"]["ok"], built
    checked = call(mcp_process, "aad_check", project="solid-part", suites=["2d"])["structuredContent"]
    assert checked["ok"], checked
    assert checked["checks"] == [
        {
            "id": "2d.requires_sheet",
            "suite": "2d",
            "level": "warn",
            "status": "skip",
            "message": "The 2d suite requires a 2D sheet and was skipped for this 3D project.",
            "fix_hint": "Use a project created with kind='2d' to check sheet contours.",
        }
    ]
