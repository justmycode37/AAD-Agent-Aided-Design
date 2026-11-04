"""AIAD drawing-sheet generation.

Heavy CAD, PDF, and raster dependencies are imported only when make_drawing
is called so importing the package's request/result types stays inexpensive.
"""

from .types import DimensionSpec, DrawingRequest, DrawingResult, DrawingView
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "DimensionSpec",
    "DrawingRequest",
    "DrawingResult",
    "DrawingView",
    "make_drawing",
    "project_drawing_view",
    "section_plane_trace",
]


def make_drawing(
    ctx: Any,
    req: DrawingRequest | Mapping[str, Any] | None,
    out: str | Path,
) -> DrawingResult:
    """Create a dimensioned drawing sheet from a structural model context."""
    from .sheet import make_drawing as _make_drawing

    return _make_drawing(ctx, req, out)


def project_drawing_view(shape: Any, view: DrawingView):
    """Project orthographic, section or circular detail geometry lazily."""
    from .views import project_drawing_view as project
    return project(shape, view)


def section_plane_trace(shape: Any, view: DrawingView):
    """Locate a section plane on its parent projection."""
    from .views import section_plane_trace as trace
    return trace(shape, view)
