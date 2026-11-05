"""Resource limits for the JSON handoff to the out-of-sandbox mesh renderer."""

import json
from collections.abc import Callable
from typing import Any

MAX_RENDER_SIZE = 2048
MAX_RENDER_VIEWS = 9
MAX_RENDER_PANES = 4

# The 492-part hexarotor payload measured about 291 MB at 512 px. This leaves
# room for larger assemblies while keeping untrusted JSON transfers bounded.
MAX_RENDER_PAYLOAD_BYTES = 384 * 1024 * 1024
# The prepare worker writes the mesh to a bounded sidecar; stdout carries only
# inspection plus small request metadata.
MAX_PREPARED_RENDER_BYTES = 36 * 1024 * 1024
MAX_RENDER_RESULT_BYTES = 8 * 1024 * 1024
MAX_WORKER_STDOUT_BYTES = 16 * 1024 * 1024
MAX_WORKER_STDERR_BYTES = 2 * 1024 * 1024
MAX_RENDER_IMAGE_BYTES = 32 * 1024 * 1024

MAX_RENDER_FACES = 100_000
MAX_RENDER_LINES = 100_000
MAX_RENDER_VERTICES = 8_000_000
MAX_RENDER_TRIANGLES = 12_000_000
MAX_RENDER_LABELS = 50_000
MAX_RENDER_ANNOTATIONS = 50_000
MAX_RENDER_TEXT_LENGTH = 4096

MAX_LABELS_JSON_BYTES = 8 * 1024 * 1024
MAX_INSPECTION_JSON_BYTES = 32 * 1024 * 1024


def json_size_limited(
    value: Any,
    limit: int,
    description: str,
    *,
    compact: bool = True,
    default: Callable[[Any], Any] | None = None,
) -> int:
    """Count encoded JSON bytes without materializing another large string."""
    encoder = json.JSONEncoder(
        allow_nan=False,
        default=default,
        ensure_ascii=True,
        separators=(",", ":") if compact else None,
    )
    size = 0
    for fragment in encoder.iterencode(value):
        size += len(fragment.encode("utf-8"))
        if size > limit:
            raise ValueError(f"{description} exceeds the {limit}-byte limit")
    return size
