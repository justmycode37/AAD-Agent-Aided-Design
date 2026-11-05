"""Library relevance and empty-result hints through real MCP stdio."""

from __future__ import annotations

from typing import Any

import pytest

from .test_smoke import call, initialize


pytestmark = pytest.mark.usefixtures("engine")


def test_acrylic_sheet_does_not_return_unrelated_components(mcp_process: Any) -> None:
    initialize(mcp_process)
    response = call(
        mcp_process,
        "aad_library",
        action="search",
        kind="component",
        query="3 mm acrylic sheet",
    )
    assert not response.get("isError"), response
    result = response["structuredContent"]["library"]
    assert result["items"] == [], result
    assert result["total"] == 0, result
    assert "No component" in result["message"]
    assert "kind=material" in result["message"]

    partial = call(
        mcp_process,
        "aad_library",
        action="search",
        kind="component",
        query="10k 0603 thickfilm zzz",
    )["structuredContent"]["library"]
    assert partial["partial_match"] is True, partial
    assert partial["items"][0]["id"] == "passive.resistor.chip.0603", partial


def test_acrylic_material_search_finds_pmma_through_mcp(mcp_process: Any) -> None:
    initialize(mcp_process)
    response = call(
        mcp_process,
        "aad_library",
        action="search",
        kind="material",
        query="acrylic",
    )
    assert not response.get("isError"), response
    result = response["structuredContent"]["library"]
    assert "pmma" in {item["id"] for item in result["items"]}, result


def test_out_of_catalog_component_query_says_nothing_matched(mcp_process: Any) -> None:
    initialize(mcp_process)
    response = call(
        mcp_process,
        "aad_library",
        action="search",
        kind="component",
        query="clearly-not-in-the-catalog",
    )
    result = response["structuredContent"]["library"]
    assert result["total"] == 0, result
    assert "No component" in result["message"]
    assert "clearly-not-in-the-catalog" in result["message"]
