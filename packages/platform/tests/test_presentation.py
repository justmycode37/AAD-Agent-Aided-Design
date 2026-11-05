import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from aad_platform.presentation import normalize_presentation
from aad_platform.daemon import handlers


PARTS = [
    {"name": "housing", "path": "/Group/housing"},
    {"name": "gear", "path": "/Group/gears/gear"},
    {"name": "shaft", "path": "/Group/gears/shaft"},
]


def test_resolves_agent_names_and_group_paths_without_changing_the_request():
    request = {"isolate": ["/Group/gears", "gear"], "hide": ["shaft"], "style": "wireframe"}
    assert normalize_presentation(request, PARTS) == {
        "isolate": ["/Group/gears/gear", "/Group/gears/shaft"],
        "hide": ["/Group/gears/shaft"], "style": "wireframe",
    }
    assert request["hide"] == ["shaft"]


def test_rejects_ambiguous_names_instead_of_hiding_the_wrong_part():
    with pytest.raises(ValueError, match="ambiguous"):
        normalize_presentation({"hide": ["gear"]}, PARTS + [{"name": "gear", "path": "/Other/gear"}])


@pytest.mark.parametrize("settings", [
    {"isolate": ["ge"]}, {"hide": []}, {"isolate": [42]}, {"hide": ["/Group/ge"]},
    {"grid": 1}, {"explode": "true"}, {"style": "unknown"}, {"projection": "unknown"}, {"run": "anything"},
    {"section": {"planes": []}}, {"section": {"planes": [{"plane": "XY"}] * 4}},
    {"section": {"planes": [{"normal": [0, 0, 0]}]}},
    {"section": {"planes": [{"plane": "XY", "normal": [0, 0, 1]}]}},
    {"section": {"planes": [{"plane": "XY", "offset": float("nan")}] }},
    {"section": {"planes": [{"plane": "XY", "offset": 10 ** 400}] }},
    {"section": {"planes": [{"plane": "XY", "flipped": 1}] }},
    {"section": {"planes": [{"plane": "XY"}], "intersection": "yes"}},
])
def test_rejects_invalid_agent_view_settings(settings):
    with pytest.raises(ValueError):
        normalize_presentation(settings, PARTS)


def test_preserves_section_offsets_and_normalizes_custom_normals():
    assert normalize_presentation({"section": {"planes": [{"normal": [0, 0, 2], "offset": -1}]}}, PARTS) == {
        "section": {"planes": [{"normal": [0.0, 0.0, 1.0], "offset": -1, "flipped": False}], "intersection": False}
    }


def test_show_updates_an_open_window_and_keeps_settings_on_reload(tmp_path, monkeypatch):
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    payload = b"original scene bytes"
    (scene_dir / "scene.bin.gz").write_bytes(payload)
    scene = {"scene_id": hashlib.sha256(payload).hexdigest(), "total_bytes": len(payload), "parts": PARTS}
    (tmp_path / "meta.json").write_text(json.dumps({"scene": scene, "status": "draft"}), encoding="utf-8")
    project = SimpleNamespace(name="gearbox", path=tmp_path, head_rev=1, rev_dir=lambda rev: tmp_path)
    monkeypatch.setattr(handlers, "open_project", lambda *args: project)
    monkeypatch.setattr(handlers.scenes, "publish", lambda *args: None)
    sent = []

    class Window:
        def has_viewers(self, project): return True
        async def broadcast(self, project, envelope): sent.append(envelope)

    h = handlers.Handlers()
    h._window = Window()

    async def viewer_for(*args): return {"mode": "inline"}
    monkeypatch.setattr(h, "_viewer_for", viewer_for)

    shown = asyncio.run(h.aad_show({"project": "gearbox", "presentation": {"hide": ["housing"]}, "view": "left", "client": {"supports_ui": True}}))["structured"]
    assert shown["viewer"]["presentation"] == {"hide": ["/Group/housing"]}
    assert shown["viewer"]["view"] == "left"
    assert len(sent) == 1
    assert shown["scene"]["scene_id"] == scene["scene_id"]
    reloaded = asyncio.run(h.aad_show({"project": "gearbox"}))["structured"]
    assert reloaded["viewer"] == shown["viewer"]
    assert len(sent) == 1  # A window reading its initial view does not echo a broadcast.
    failed = asyncio.run(h.aad_show({"project": "gearbox", "presentation": {"isolate": ["unknown"]}}))["structured"]
    assert failed["failure"]["code"] == "BAD_INPUT"
    assert len(sent) == 1
    assert (scene_dir / "scene.bin.gz").read_bytes() == payload
