"""The capability guide must stay true: every example runs, and every tool it names exists."""
from __future__ import annotations

import re

import pytest

from aad_quality.catalog import library
from aad_quality.standard_parts import _FAMILIES, make_part

GUIDE = library("get", kind="playbook", id="workflow.capabilities.v1")["item"]["text"]
BLOCKS = re.findall(r"```python\n(.*?)```", GUIDE, re.S)


def test_guide_is_found_by_a_capability_search():
    found = library("search", kind="playbook", query="capabilities sheet metal assembly")["items"]
    assert "workflow.capabilities.v1" in [item["id"] for item in found]


def test_guide_has_examples():
    assert len(BLOCKS) >= 12


@pytest.mark.parametrize("source", BLOCKS, ids=[f"block{index}" for index in range(len(BLOCKS))])
def test_every_example_runs_and_builds_valid_geometry(source):
    pytest.importorskip("build123d")
    pytest.importorskip("bd_warehouse")
    from aad import cad
    from aad.modeling import configure_modeling, named_shape

    configure_modeling(source)
    namespace: dict = {}
    exec("from aad import *", namespace)
    exec(compile(source, "cad-capabilities.md", "exec"), namespace)
    result = namespace.get("result")
    if isinstance(result, cad.Assembly):
        assert result.bom()
        result = result.compound()
    if result is None and "plate" in namespace:
        result = namespace["plate"].to_shape()
    if result is None:
        result = named_shape()
    assert result is not None and result.is_valid


def test_guide_names_only_real_tool_values():
    pytest.importorskip("aad_platform")
    from aad_platform.tools import TOOLS

    schema = {tool["name"]: tool["inputSchema"]["properties"] for tool in TOOLS}
    for kind in re.findall(r"`(\w+)`", GUIDE.split("| Measure |")[1].split("\n")[0]):
        if kind != "kind" and not kind.startswith("aad_"):
            assert kind in schema["aad_measure"]["kind"]["enum"], kind
    for fmt in re.findall(r"`(\w+)`", GUIDE.split("| Write files |")[1].split("\n")[0]):
        if not fmt.startswith("aad_"):
            assert fmt in schema["aad_export"]["formats"]["items"]["enum"], fmt
    packages = {"aad_quality", "aad_drafting"}
    for name in set(re.findall(r"\baad_[a-z_]+\b", GUIDE)) - packages:
        assert name in schema, name


@pytest.mark.parametrize("entry", _FAMILIES, ids=[entry["id"] for entry in _FAMILIES])
def test_catalog_family_builds_its_default(entry):
    pytest.importorskip("bd_warehouse")
    part = make_part(entry["id"])
    assert part.is_valid and part.solids()


def test_catalog_refuses_unknown_parameters():
    pytest.importorskip("bd_warehouse")
    with pytest.raises(TypeError, match="unsupported"):
        make_part("nut.iso4032.hex_nut", diameter=6)
