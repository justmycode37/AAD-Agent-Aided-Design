"""Scene generation remains safe for shapes carrying library metadata over real MCP stdio."""

def _start_client(mcp_process):
    mcp_process.request(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "scene-metadata-regression", "version": "1"},
        },
    )
    mcp_process.send({"method": "notifications/initialized"})

    return lambda tool, **arguments: mcp_process.request(
        "tools/call", {"name": tool, "arguments": arguments}
    )["structuredContent"]


def test_every_standard_part_family_generates_a_scene_over_stdio(mcp_process):
    from aad_quality.standard_parts import list_parts

    call = _start_client(mcp_process)
    for index, entry in enumerate(list_parts()):
        project = f"library-part-{index:02d}"
        created = call("aad_new", name=project, kind="assembly")
        assert created["ok"], (entry["id"], created)

        params = {
            name: spec["default"]
            for name, spec in entry["parameters"].items()
        }
        arguments = ", ".join(f"{name}={value!r}" for name, value in params.items())
        source = f"""from aad_quality.standard_parts import make_part
result = make_part({entry['id']!r}{', ' if arguments else ''}{arguments})
"""
        built = call("aad_build", project=project, source=source, wait_s=200)
        assert built.get("ok"), (entry["id"], built)
        scene_failures = [
            check for check in built.get("checks", [])
            if check["id"] == "scene_generation_failed"
        ]
        assert not scene_failures, (entry["id"], scene_failures, built.get("summary"))
        shown = call("aad_show", project=project)
        assert shown.get("scene", {}).get("format") == "tcv-5", (entry["id"], shown)


def test_arbitrary_non_json_shape_metadata_generates_a_scene_over_stdio(mcp_process):
    call = _start_client(mcp_process)
    created = call("aad_new", name="custom-shape-scene", kind="part")
    assert created["ok"], created
    source = """from aad import *
shape = Box(10, 10, 10)
shape._material = object()
result = shape
"""
    built = call("aad_build", project="custom-shape-scene", source=source, wait_s=200)
    assert built["ok"], built
    scene_failures = [
        check for check in built.get("checks", [])
        if check["id"] == "scene_generation_failed"
    ]
    assert not scene_failures, scene_failures
    shown = call("aad_show", project="custom-shape-scene")
    assert shown.get("scene", {}).get("format") == "tcv-5", shown


def test_cyclic_shape_material_metadata_generates_a_scene_over_stdio(mcp_process):
    call = _start_client(mcp_process)
    created = call("aad_new", name="cyclic-shape-scene", kind="part")
    assert created["ok"], created
    source = """from aad import *
shape = Box(10, 10, 10)
material = {"name": "custom"}
material["cycle"] = material
shape._material = material
result = shape
"""
    built = call("aad_build", project="cyclic-shape-scene", source=source, wait_s=200)
    assert built["ok"], built
    scene_failures = [
        check for check in built.get("checks", [])
        if check["id"] == "scene_generation_failed"
    ]
    assert not scene_failures, scene_failures
    shown = call("aad_show", project="cyclic-shape-scene")
    assert shown.get("scene", {}).get("format") == "tcv-5", shown
