import base64

import pytest

from aad_platform import adapter, image_payload
from aad_platform.daemon.window import WindowServer


def test_parent_image_read_limit_is_independent_and_32_mib():
    assert image_payload.MAX_RENDER_IMAGE_READ_BYTES == 32 * 1024 * 1024


@pytest.mark.parametrize("consumer", ["adapter", "window"])
def test_image_response_consumers_read_only_within_the_parent_limit(tmp_path, monkeypatch, consumer):
    monkeypatch.setattr(image_payload, "MAX_RENDER_IMAGE_READ_BYTES", 3)
    image = tmp_path / "render.png"

    def convert() -> bytes:
        reply = {"text": "render", "images": [{"path": str(image), "mime": "image/png"}]}
        if consumer == "adapter":
            return base64.b64decode(adapter._result(reply).content[1].data)
        result = WindowServer._tool_result(reply)
        return base64.b64decode(result["content"][1]["data"])

    image.write_bytes(b"png")
    assert convert() == b"png"

    image.write_bytes(b"png!")
    with pytest.raises(ValueError, match="3-byte parent read limit"):
        convert()
