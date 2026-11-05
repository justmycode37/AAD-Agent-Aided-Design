"""Tessellation and agent renders. Heavy imports happen on first call, never at import time."""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["make_scene", "render"]


def make_scene(ctx: Any, out: Path, compare: Any | None = None) -> Any:
    """Writes the scene for ctx under out and returns its SceneManifest."""
    from .scene import make_scene as impl

    return impl(ctx, out, compare)


def render(ctx: Any, req: Any, out: Path) -> list[Path]:
    """Write agent images and inspection/label JSON under out and return their paths."""
    from .agent_render import render as impl

    return impl(ctx, req, out)
