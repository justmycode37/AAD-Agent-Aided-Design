from aiad_platform.tools import TOOL_NAMES, TOOLS, VIEWER_URI


def test_thirteen_model_visible_and_four_app_only_tools():
    app_only = {t["name"] for t in TOOLS if t.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]}
    assert app_only == {"aiad_scene_read", "aiad_set_param", "aiad_pick_info", "aiad_file_read"}
    assert len(TOOL_NAMES) == 17


def test_only_show_has_a_ui_resource():
    with_ui = [t["name"] for t in TOOLS if "resourceUri" in t.get("_meta", {}).get("ui", {})]
    assert with_ui == ["aiad_show"]
    assert next(t for t in TOOLS if t["name"] == "aiad_show")["_meta"]["ui"]["resourceUri"] == VIEWER_URI


def test_app_only_descriptions_warn_the_model():
    for tool in TOOLS:
        if tool.get("_meta", {}).get("ui", {}).get("visibility") == ["app"]:
            assert tool["description"].startswith("Internal viewer tool. Do not call.")


def test_builds_are_not_marked_read_only():
    annotations = {t["name"]: t["annotations"] for t in TOOLS}
    assert annotations["aiad_build"]["readOnlyHint"] is False
    assert annotations["aiad_scene_read"]["readOnlyHint"] is True
