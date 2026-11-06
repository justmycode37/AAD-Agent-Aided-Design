"""Bounded image reads before encoding renderer files into MCP responses."""

from __future__ import annotations

from pathlib import Path

# This parent-side cap is independent of aad-render's writer limit. The renderer
# is a separate process, so its output checks cannot bound these reads by themselves.
MAX_RENDER_IMAGE_READ_BYTES = 32 * 1024 * 1024


def read_render_image(path: str | Path) -> bytes:
    """Read at most one byte beyond the image cap, then reject oversized output."""
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_RENDER_IMAGE_READ_BYTES + 1)
    if len(data) > MAX_RENDER_IMAGE_READ_BYTES:
        raise ValueError(f"render image exceeds the {MAX_RENDER_IMAGE_READ_BYTES}-byte parent read limit")
    return data
