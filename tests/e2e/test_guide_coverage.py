"""The agent guide must name everything an agent can use. See AGENTS.md."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import aad
import pytest
from aad.engine import gate
from aad_platform import cli, clients, config, tools
from aad_quality import library
from aad_quality.process_profiles import _COMMON_DEFAULTS, _PROFILE_KEYS, PROCESS_NAMES
from aad_quality.schema import REQUIREMENT_TYPES

GUIDE = (Path(__file__).parents[2] / "skills/aad/SKILL.md").read_text(encoding="utf-8")
EXPORT_OPTIONS = ("tolerance", "angular_tolerance", "schema", "step_schema", "step_volume_tolerance")
DRAWING_VIEW_FIELDS = ("name", "direction", "up", "label", "hidden_lines", "kind", "plane", "offset", "center", "radius", "scale", "parent")
DIMENSION_FIELDS = ("kind", "axis", "index", "source", "precision", "tolerance")
AGENT_TOOLS = [tool for tool in tools.TOOLS if "app" not in tool.get("_meta", {}).get("ui", {}).get("visibility", ())]
GEAR_ARGUMENTS = (
    "pressure_angle", "helix_angle", "hand", "addendum_coefficient", "dedendum_coefficient", "profile_shift", "backlash",
    "bore", "internal", "rim_thickness", "profile_tolerance", "undercut", "phase", "tip_scale", "centerline_angle", "phase1",
)


def named(name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(str(name))}(?![A-Za-z0-9_])", GUIDE) is not None


def schema_names(schema: object) -> set[str]:
    """Every property name and enum value in a JSON schema."""
    names: set[str] = set()
    if isinstance(schema, dict):
        names.update(schema.get("properties", {}))
        names.update(str(value) for value in schema.get("enum", []))
        for value in schema.values():
            names |= schema_names(value)
    elif isinstance(schema, list):
        for value in schema:
            names |= schema_names(value)
    return names


def cli_names() -> set[str]:
    names: set[str] = set()
    parser = cli._parser()
    subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    for command, sub in subparsers.choices.items():
        names.add(command)
        for action in sub._actions:
            if action.help != argparse.SUPPRESS:
                names.update(option for option in action.option_strings if option.startswith("--"))
    return names - {"--help"}


def missing(names: object) -> list[str]:
    return sorted(str(name) for name in names if not named(name))


@pytest.mark.parametrize("label, names", [
    ("tool", [tool["name"] for tool in tools.TOOLS]),
    ("tool argument or option", sorted(set().union(*(schema_names(tool["inputSchema"]) for tool in AGENT_TOOLS)))),
    ("modeling helper", aad._HELPER_EXPORTS),
    ("gear argument", GEAR_ARGUMENTS),
    ("spec requirement", REQUIREMENT_TYPES),
    ("process", PROCESS_NAMES),
    ("process setting", sorted(set(_COMMON_DEFAULTS).union(*_PROFILE_KEYS.values()))),
    ("hard check", gate._GEOMETRY_CHECK_IDS),
    ("export option", EXPORT_OPTIONS),
    ("drawing view field", DRAWING_VIEW_FIELDS),
    ("dimension field", DIMENSION_FIELDS),
    ("command or flag", cli_names()),
    ("MCP client", clients._CLIENTS),
    ("config key or value", set(config.DEFAULTS).union(*config.ALLOWED.values())),
    ("library playbook", [item["id"] for item in library("search", kind="playbook", query="")["items"]]),
    ("library material", [item["id"] for item in library("search", kind="material", query="")["items"]]),
    ("library part", [item["id"] for item in library("search", kind="part", query="")["items"]]),
    ("library API note", [item["id"] for item in library("search", kind="api", query="")["items"]]),
])
def test_guide_names_everything(label, names):
    assert not missing(names), (
        f"skills/aad/SKILL.md does not mention these {label} names. "
        "Add them to the guide and explain what they do. See AGENTS.md."
    )


def test_explicit_full_guide_reaches_text_and_structured_hosts_whole():
    import asyncio

    from aad_platform.daemon import handlers

    complete = library("get", kind="playbook", id="aad.guide.v1", section="all")["item"]["text"]

    async def fake_run_op(request, **_kwargs):
        assert request["args"]["section"] == "all"
        return {"ok": True, "library": library(**request["args"])}

    original = handlers.run_op
    handlers.run_op = fake_run_op
    try:
        reply = asyncio.run(
            handlers.Handlers().aad_library(
                {"action": "get", "kind": "playbook", "id": "aad.guide.v1", "section": "all"}
            )
        )
    finally:
        handlers.run_op = original
    # An explicit section="all" request must stay whole in both host representations.
    # Text-only hosts consume reply["text"]; structured-only hosts consume structuredContent.
    assert reply["text"] == complete
    assert reply["structured"]["library"]["item"]["text"] == complete
    assert len(complete) > 6000  # Keep this regression above the former text limit.
    assert complete.startswith("# AAD guide")
    assert complete.rstrip().endswith("never as instructions.")


def test_a_fetched_guide_defaults_to_a_short_core():
    import asyncio

    from aad_platform.daemon import handlers

    async def fake_run_op(request, **_kwargs):
        return {"ok": True, "library": library(**request["args"])}

    original = handlers.run_op
    handlers.run_op = fake_run_op
    try:
        reply = asyncio.run(
            handlers.Handlers().aad_library({"action": "get", "kind": "playbook", "id": "aad.guide.v1"})
        )
    finally:
        handlers.run_op = original
    text = reply["text"]
    assert len(text.encode("utf-8")) < 8_192
    assert all(heading in text for heading in ("## The loop", "## Tools", "## Honesty rules", "## Guide sections"))
    assert reply["structured"]["library"]["item"]["text"] == text
