"""Render a JSON mesh payload without opening or parsing the source B-rep."""

from __future__ import annotations

import json
import os
import sys
import zlib
from pathlib import Path
from typing import Any

from .ops import RESULT_MARKER, _classify, _plain


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token} is not allowed")


def _read_request(stream: Any) -> tuple[dict[str, Any], int]:
    """Read bounded, strict JSON from the parent before constructing Python objects."""
    from aad_render.render_limits import MAX_RENDER_PAYLOAD_BYTES, RENDER_PAYLOAD_ZLIB_PREFIX

    wire_data = stream.read(MAX_RENDER_PAYLOAD_BYTES + 1)
    if not isinstance(wire_data, bytes):
        raise ValueError("render request must be read as bytes")
    if len(wire_data) > MAX_RENDER_PAYLOAD_BYTES:
        raise ValueError(f"render payload exceeds the {MAX_RENDER_PAYLOAD_BYTES}-byte limit")
    if wire_data.startswith(RENDER_PAYLOAD_ZLIB_PREFIX):
        decompressor = zlib.decompressobj()
        try:
            raw = decompressor.decompress(wire_data[len(RENDER_PAYLOAD_ZLIB_PREFIX) :], MAX_RENDER_PAYLOAD_BYTES + 1)
        except zlib.error as exc:
            raise ValueError("render payload compression is invalid") from exc
        if len(raw) > MAX_RENDER_PAYLOAD_BYTES or decompressor.unconsumed_tail:
            raise ValueError(f"render payload exceeds the {MAX_RENDER_PAYLOAD_BYTES}-byte limit")
        if not decompressor.eof or decompressor.unused_data:
            raise ValueError("render payload compression is incomplete or has trailing data")
    else:
        raw = wire_data
    request = json.loads(raw, parse_constant=_reject_json_constant)
    if not isinstance(request, dict) or set(request) != {"render"} or not isinstance(request["render"], dict):
        raise ValueError("mesh renderer request must contain one render object")
    return request, len(raw)


def main() -> int:
    """Read render-only data from stdin and write PNGs beneath the supplied output directory."""
    protocol = os.dup(1)
    os.dup2(2, 1)
    try:
        out = Path(sys.argv[1])
        request, payload_size = _read_request(sys.stdin.buffer)
        from aad_render.agent_render import _render_mesh_from_bounded_json
        from aad_render.render_limits import MAX_RENDER_RESULT_BYTES, json_size_limited

        rendered = _render_mesh_from_bounded_json(request["render"], out, payload_size)
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
