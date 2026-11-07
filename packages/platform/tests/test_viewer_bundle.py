import base64
import gzip
import hashlib
import json
import os
import re
import struct
import zlib
from xml.etree import ElementTree

import pytest

from aad_platform.viewer.bundle import export_bundle
from aad_platform.viewer import viewer_path


def decode_rgb_png(data, count):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    compressed = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + length]
        assert zlib.crc32(kind + chunk) & 0xffffffff == struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
        if kind == b"IHDR": width, height = struct.unpack(">II", chunk[:8])
        if kind == b"IDAT": compressed.extend(chunk)
        offset += 12 + length
        if kind == b"IEND": break
    rows = zlib.decompress(compressed)
    stride = width * 3
    return b"".join(rows[y * (stride + 1) + 1:y * (stride + 1) + 1 + stride] for y in range(height))[:count]


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
    assets = re.findall(r'<img src="([^"]+)" data-aad-chunk="\d+">', html)
    assert len(assets) == len(config["chunk_bytes"]) == 1
    assert assets[0].startswith("/")
    runtime_asset = re.search(r'<img id="aad-runtime-asset" src="([^"]+)">', html)
    assert runtime_asset and runtime_asset[1].startswith("/")
    assert 'querySelectorAll("#aad-assets img[data-aad-chunk]")' in html
    assert "data:image/png;base64," not in html
    assert decode_rgb_png(__import__("pathlib").Path(assets[0]).read_bytes(), config["chunk_bytes"][0]) == payload
    assert (source / "scene.bin.gz").read_bytes() == payload
    runtime = next((tmp_path / "export").glob("viewer-*.svg"))
    runtime_metadata = ElementTree.parse(runtime).find("{http://www.w3.org/2000/svg}metadata")
    assert base64.b64decode(runtime_metadata.text) == viewer_path().read_bytes()
    assert len(html) < 512_000  # Scene and CAD runtime travel as assets, not tool arguments.
    standalone = (tmp_path / "export" / "standalone.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in standalone
    standalone_config = json.loads(standalone.split("window.__AAD_EMBEDDED_SCENE__ = ", 1)[1].split(";</script>", 1)[0])
    assert decode_rgb_png(base64.b64decode(standalone_config["assets"][0].split(",", 1)[1]), len(payload)) == payload
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


def test_bundle_keeps_prepared_view_in_both_exports(saved_scene, tmp_path):
    source, payload, _ = saved_scene
    title = 'Inside </script><script>alert("injected")</script>'
    viewer = export_bundle(source, tmp_path / "export", presentation={"isolate": ["gear"]}, view="rear", title=title,
                           views=[{"id": "detail", "label": "Gear", "presentation": {"isolate": ["gear"]}}], controls="cad")
    for file in (viewer, viewer.parent / "standalone.html"):
        config_text = file.read_text(encoding="utf-8").split("window.__AAD_EMBEDDED_SCENE__ = ", 1)[1].split(";</script>", 1)[0]
        config = json.loads(config_text)
        assert config["presentation"] == {"isolate": ["/gear"]}
        assert config["view"] == "rear"
        assert config["title"] == title
        assert config["views"][0]["presentation"] == {"isolate": ["/gear"]}
        assert config.get("controls", "simple") == ("cad" if file.name == "standalone.html" else "simple")
        assert "</script>" not in config_text
    assert (source / "scene.bin.gz").read_bytes() == payload


def test_bundle_rejects_unknown_part_before_writing(saved_scene, tmp_path):
    source, _, _ = saved_scene
    with pytest.raises(ValueError, match="Unknown part"):
        export_bundle(source, tmp_path / "export", presentation={"isolate": ["missing"]})
    assert not (tmp_path / "export").exists()


def test_14_34mb_scene_fits_inline_estimate_and_oversize_points_to_standalone(tmp_path):
    source = tmp_path / "scene"
    source.mkdir()
    payload = os.urandom(14_340_000)
    parts = [{"path": f"/Group/part_{i:04d}/component_interface", "name": f"Synthetic component {i:04d} with mounting interface"} for i in range(805)]
    manifest = {"scene_id": hashlib.sha256(payload).hexdigest(), "total_bytes": len(payload),
                "triangles": 1, "project": "synthetic", "rev": 1, "status": "draft", "parts": parts}
    (source / "scene.bin.gz").write_bytes(payload)
    (source / "scene.json").write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "fits"
    viewer = export_bundle(source, output)
    assert viewer.exists() and (output / "standalone.html").exists()
    assert len(viewer.read_text(encoding="utf-8")) < 512_000
    assert len(json.loads(viewer.read_text(encoding="utf-8").split("window.__AAD_EMBEDDED_SCENE__ = ", 1)[1].split(";</script>", 1)[0])["manifest"]["parts"]) == 805
    assert sum(path.stat().st_size for path in output.glob("scene-*.png")) * 4 / 3 < 25 * 1024 * 1024

    too_large = os.urandom(20 * 1024 * 1024)
    manifest.update(scene_id=hashlib.sha256(too_large).hexdigest(), total_bytes=len(too_large))
    (source / "scene.bin.gz").write_bytes(too_large)
    (source / "scene.json").write_text(json.dumps(manifest), encoding="utf-8")
    oversized = tmp_path / "oversized"
    with pytest.raises(ValueError, match=r"host-specific 25 MiB.*standalone.html"):
        export_bundle(source, oversized)
    assert (oversized / "standalone.html").exists()
    assert not (oversized / "viewer.html").exists()


def test_bundle_refuses_manifest_over_html_argument_limit(tmp_path):
    source = tmp_path / "scene"
    source.mkdir()
    payload = gzip.compress(b'{"triangles": []}')
    parts = [{"path": f"/Group/{i}/" + "x" * 160, "name": "component " + "y" * 128} for i in range(4000)]
    manifest = {"scene_id": hashlib.sha256(payload).hexdigest(), "total_bytes": len(payload),
                "triangles": 0, "project": "synthetic", "rev": 1, "status": "draft", "parts": parts}
    (source / "scene.bin.gz").write_bytes(payload)
    (source / "scene.json").write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "too-many-parts"
    with pytest.raises(ValueError, match=r"512,000-character html_preview/html_render argument limit.*standalone.html"):
        export_bundle(source, output)
    assert (output / "standalone.html").exists()
    assert not (output / "viewer.html").exists()


@pytest.mark.parametrize("limit", ["HTML_INLINE_LIMIT", "HTML_ARGUMENT_LIMIT"])
def test_oversize_opt_in_opens_complete_standalone_in_safari(saved_scene, tmp_path, monkeypatch, limit):
    from aad_platform.viewer import bundle
    source, payload, manifest = saved_scene
    monkeypatch.setattr(bundle, limit, 1)
    monkeypatch.setattr(bundle.sys, "platform", "darwin")
    opened = []
    class Browser:
        def open(self, url, new):
            opened.append((url, new))
            return True
    requested = []
    def get(name):
        requested.append(name)
        return Browser()
    monkeypatch.setattr(bundle.webbrowser, "get", get)
    output = tmp_path / "export"
    output.mkdir()
    (output / "viewer.html").write_text("stale revision")
    target = export_bundle(source, output, open_on_limit=True)
    assert target == output / "standalone.html"
    assert requested == ["safari"]
    assert len(opened) == 1 and opened[0][1] == 2
    assert opened[0][0].startswith(target.resolve().as_uri() + "?aad_scene=" + manifest["scene_id"] + "&aad_viewer=")
    assert not (output / "viewer.html").exists()
    config = json.loads(target.read_text().split("window.__AAD_EMBEDDED_SCENE__ = ", 1)[1].split(";</script>", 1)[0])
    assert config["manifest"] == manifest
    assert decode_rgb_png(base64.b64decode(config["assets"][0].split(",", 1)[1]), len(payload)) == payload


def test_oversize_browser_refusal_reports_saved_path(saved_scene, tmp_path, monkeypatch):
    from aad_platform.viewer import bundle
    source, _, _ = saved_scene
    monkeypatch.setattr(bundle, "HTML_INLINE_LIMIT", 1)
    class Browser:
        def open(self, *args, **kwargs): return False
    monkeypatch.setattr(bundle.webbrowser, "get", lambda *args: Browser())
    with pytest.raises(ValueError, match="standalone.html.*browser did not accept"):
        export_bundle(source, tmp_path / "export", open_on_limit=True)
    assert (tmp_path / "export/standalone.html").exists()


def test_browser_opt_in_does_not_open_a_scene_that_fits(saved_scene, tmp_path, monkeypatch):
    from aad_platform.viewer import bundle
    source, _, _ = saved_scene
    def unexpected(*args): raise AssertionError("Fitting scene must remain inline")
    monkeypatch.setattr(bundle.webbrowser, "get", unexpected)
    assert export_bundle(source, tmp_path / "export", open_on_limit=True).name == "viewer.html"
