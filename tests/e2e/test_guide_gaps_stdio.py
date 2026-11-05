"""Guide-critical recovery paths exercised through real MCP stdio."""

from __future__ import annotations

from typing import Any

import pytest

from .test_smoke import call, initialize


WAIT_S = 120
pytestmark = pytest.mark.usefixtures("engine")


def test_empty_project_rejects_base_rev_with_first_build_instructions(mcp_process: Any) -> None:
    initialize(mcp_process)
    created = call(mcp_process, "aad_new", name="empty-edit")
    assert created["structuredContent"]["ok"], created

    source = "from build123d import Box\nresult = Box(10, 10, 10)\n"
    rejected = call(mcp_process, "aad_build", project="empty-edit", base_rev=1, source=source)
    result = rejected["structuredContent"]
    assert rejected["isError"] is True
    assert result["failure"]["code"] == "BASE_REV_CONFLICT"
    message = result["failure"]["message"].casefold()
    assert "no head revision" in message or "build once without base_rev" in message, result

    first = call(mcp_process, "aad_build", project="empty-edit", source=source, wait_s=WAIT_S)
    assert first["structuredContent"]["ok"], first
    assert first["structuredContent"]["rev"] == 1

    edited = call(
        mcp_process,
        "aad_build",
        project="empty-edit",
        base_rev=1,
        source="from build123d import Box\nresult = Box(12, 10, 10)\n",
        wait_s=WAIT_S,
    )
    assert edited["structuredContent"]["ok"], edited
    assert edited["structuredContent"]["rev"] == 2


def test_2d_text_render_floor_and_3d_export_limit_are_clear(mcp_process: Any) -> None:
    initialize(mcp_process)
    created = call(mcp_process, "aad_new", name="text-floor", kind="2d")
    assert created["structuredContent"]["ok"], created
    source = """
from aad_drafting import sheet2d
sheet = sheet2d("plate", units="mm")
sheet.rectangle("outline", 80, 40)
sheet.text("small-label", "STOCK", (5, 5), 0.1)
"""
    built = call(mcp_process, "aad_build", project="text-floor", source=source, wait_s=WAIT_S)
    assert built["structuredContent"]["ok"], built

    rendered = call(mcp_process, "aad_render", project="text-floor", labels="none", size=2048)
    assert rendered["isError"] is True
    render_message = rendered["structuredContent"]["failure"]["message"].casefold()
    assert "below 2.5 mm" in render_message

    for format_name in ("step", "stl"):
        exported = call(
            mcp_process,
            "aad_export",
            project="text-floor",
            formats=[format_name],
            allow_unverified=True,
        )
        assert exported["isError"] is True
        export_message = exported["structuredContent"]["failure"]["message"].casefold()
        assert "2d sheet exports" in export_message
        assert format_name in export_message
