"""Render a bounded mesh payload without opening or parsing the source B-rep."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .ops import RESULT_MARKER, _classify, _plain


def _read_request(stream: Any) -> tuple[dict[str, Any], int]:
    """Read the bounded packed mesh record before rendering."""
    from aad_render.render_limits import MAX_RENDER_PAYLOAD_BYTES
    from aad_render.render_wire import RENDER_BATCH_MAGIC, RENDER_WIRE_MAGIC, decode_render_payload

    wire_data = stream.read(MAX_RENDER_PAYLOAD_BYTES + 1)
    if not isinstance(wire_data, bytes):
        raise ValueError("render request must be read as bytes")
    if len(wire_data) > MAX_RENDER_PAYLOAD_BYTES:
        raise ValueError(f"render payload exceeds the {MAX_RENDER_PAYLOAD_BYTES}-byte limit")
    if wire_data.startswith(RENDER_WIRE_MAGIC):
        return decode_render_payload(wire_data)
    if wire_data.startswith(RENDER_BATCH_MAGIC):
        return {"review_batch": wire_data}, len(wire_data)
    raise ValueError("render payload has an invalid binary header")


def main() -> int:
    """Read render-only data from stdin and write PNGs beneath the supplied output directory."""
    protocol = os.dup(1)
    os.dup2(2, 1)
    try:
        out = Path(sys.argv[1])
        request, payload_size = _read_request(sys.stdin.buffer)
        from aad_render.agent_render import _render_mesh_from_bounded_payload, render_review_batch
        from aad_render.render_limits import MAX_RENDER_RESULT_BYTES, json_size_limited

        if set(request) == {"review_batch"}:
            rendered = render_review_batch(request["review_batch"], out / "review-packet")
        else:
            rendered = _render_mesh_from_bounded_payload(request["render"], out, payload_size)
        result = {"ok": True, "rendered": _plain(rendered)}
        json_size_limited(result, MAX_RENDER_RESULT_BYTES, "render result")
    except Exception as exc:
        failure = _classify(exc)
        failure["phase"] = "render"
        result = {"ok": False, "failure": failure}
    os.write(protocol, RESULT_MARKER + json.dumps(result, allow_nan=False, separators=(",", ":")).encode() + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
