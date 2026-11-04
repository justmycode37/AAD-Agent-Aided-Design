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
]


def make_drawing(
    ctx: Any,
    req: DrawingRequest | Mapping[str, Any] | None,
    out: str | Path,
) -> DrawingResult:
    """Create a dimensioned drawing sheet from a structural model context."""
    from .sheet import make_drawing as _make_drawing

    return _make_drawing(ctx, req, out)
