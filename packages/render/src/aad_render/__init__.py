"""Tessellation and agent renders. Heavy imports happen on first call, never at import time."""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["finish_render", "make_scene", "prepare_render", "render", "render_mesh", "render_prepared"]


def make_scene(ctx: Any, out: Path, compare: Any | None = None) -> Any:
    """Writes the scene for ctx under out and returns its SceneManifest."""
    from .scene import make_scene as impl

    return impl(ctx, out, compare)


def render(ctx: Any, req: Any, out: Path) -> list[Path]:
    """Write agent images and inspection/label JSON under out and return their paths."""
    from .agent_render import render as impl

    return impl(ctx, req, out)


def prepare_render(ctx: Any, req: Any) -> dict[str, Any]:
    """Prepare B-rep inspection and a JSON mesh payload without importing VTK."""
    from .agent_render import prepare_render as impl

    return impl(ctx, req)


def render_mesh(payload: dict[str, Any], out: Path) -> dict[str, Any]:
    """Render the prepared mesh payload to PNGs and return image visibility metadata."""
    from .agent_render import render_mesh as impl

    return impl(payload, out)


def finish_render(prepared: dict[str, Any], rendered: dict[str, Any], out: Path) -> list[Path]:
    """Write the inspection report after a prepared mesh has been rendered."""
    from .agent_render import finish_render as impl

    return impl(prepared, rendered, out)


def render_prepared(prepared: dict[str, Any], out: Path) -> list[Path]:
    """Render and finalize a payload returned by :func:`prepare_render`."""
    from .agent_render import render_prepared as impl

    return impl(prepared, out)
