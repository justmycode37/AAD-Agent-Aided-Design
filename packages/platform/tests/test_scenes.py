import base64

import pytest

from aiad_platform.daemon import scenes


def test_chunks_reassemble(tmp_path, monkeypatch):
    monkeypatch.setenv("AIAD_HOME", str(tmp_path))
    source = tmp_path / "scene.gz"
    source.write_bytes(bytes(range(256)) * 2000)
    scene_id = "a" * 64
    scenes.publish(scene_id, source)

    data = b""
    while True:
        chunk = scenes.read_chunk(scene_id, len(data))
        assert chunk["byte_count"] <= scenes.CHUNK_BYTES
        data += base64.b64decode(chunk["data_b64"])
        if not chunk["has_more"]:
            break
    assert data == source.read_bytes()


def test_scene_id_cannot_escape_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AIAD_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        scenes.read_chunk("../../etc/passwd", 0)


def test_requested_length_is_capped(tmp_path, monkeypatch):
    monkeypatch.setenv("AIAD_HOME", str(tmp_path))
    source = tmp_path / "scene.gz"
    source.write_bytes(b"x" * (scenes.CHUNK_BYTES * 2))
    scenes.publish("b" * 64, source)
    assert scenes.read_chunk("b" * 64, 0, length=10**9)["byte_count"] == scenes.CHUNK_BYTES
