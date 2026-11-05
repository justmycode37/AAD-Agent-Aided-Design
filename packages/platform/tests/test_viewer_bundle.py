import base64
import gzip
import hashlib
import json
from xml.etree import ElementTree

import pytest

from aad_platform.viewer.bundle import export_bundle
from aad_platform.viewer import viewer_path


@pytest.fixture(params=["original", "Getriebe – Gehäuse"])
def saved_scene(tmp_path, request):
    source = tmp_path / "scene"
    source.mkdir()
    # Sub-millimetre coordinates and the original JSON bytes must survive export.
    payload = gzip.compress(b'{ "vertices": [0.000123456, 1.23456789, -0.98765432], "triangles": [0, 1, 2] }')
    manifest = {
        "scene_id": hashlib.sha256(payload).hexdigest(),
        "total_bytes": len(payload),
        "triangles": 1,
        "project": request.param,
        "rev": 3,
        "status": "draft",
        "parts": [{"name": "gear", "path": "/gear"}],
    }
    (source / "scene.bin.gz").write_bytes(payload)
    (source / "scene.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return source, payload, manifest


def test_bundle_preserves_original_scene_bytes_and_complete_manifest(saved_scene, tmp_path):
    source, payload, manifest = saved_scene
    viewer = export_bundle(source, tmp_path / "export")
    html = viewer.read_text(encoding="utf-8")
    config = json.loads(html.split("window.__AAD_EMBEDDED_SCENE__ = ", 1)[1].split(";</script>", 1)[0])
    assert config["manifest"] == manifest
    asset = ElementTree.parse(config["asset"])
    metadata = asset.find("{http://www.w3.org/2000/svg}metadata")
    assert metadata.attrib == {"id": "aad-scene", "encoding": "base64+gzip"}
    assert base64.b64decode(metadata.text) == payload
    assert (source / "scene.bin.gz").read_bytes() == payload
    runtime = next((tmp_path / "export").glob("viewer-*.svg"))
    runtime_metadata = ElementTree.parse(runtime).find("{http://www.w3.org/2000/svg}metadata")
    assert base64.b64decode(runtime_metadata.text) == viewer_path().read_bytes()
    assert len(html) < 512_000  # Scene and CAD runtime travel as assets, not tool arguments.
    standalone = (tmp_path / "export" / "standalone.html").read_text(encoding="utf-8")
    assert "data:image/svg+xml;base64," in standalone
    assert 'src="/src/main.ts"' not in standalone


@pytest.mark.parametrize("field,value,message", [
    ("total_bytes", 0, "size"),
    ("scene_id", "0" * 64, "hash"),
])
def test_bundle_rejects_corrupt_source_before_writing(saved_scene, tmp_path, field, value, message):
    source, _, manifest = saved_scene
    manifest[field] = value
    (source / "scene.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "export"
    with pytest.raises(ValueError, match=message):
        export_bundle(source, output)
    assert not output.exists()


def test_bundle_manifest_cannot_end_its_script(saved_scene, tmp_path):
    source, _, manifest = saved_scene
    manifest["project"] = '</script><script>alert("injected")</script>'
    (source / "scene.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    viewer = export_bundle(source, tmp_path / "export")
    config_text = viewer.read_text(encoding="utf-8").split("window.__AAD_EMBEDDED_SCENE__ = ", 1)[1].split(";</script>", 1)[0]
    assert "</script>" not in config_text
    assert json.loads(config_text)["manifest"] == manifest
