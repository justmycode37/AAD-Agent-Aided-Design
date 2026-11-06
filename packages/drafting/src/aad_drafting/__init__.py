"""AAD drawing-sheet generation.

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
    "Point2D",
    "Entity2D",
    "Sheet2D",
    "sheet2d",
    "latest_sheet",
    "ConstraintSketch",
    "checks",
    "measurement_evidence",
    "make_scene",
    "export_2d",
    "parse_dxf",
    "parse_svg",
    "Imported2D",
]


def __getattr__(name: str):
    if name in {"Point2D", "Entity2D", "Sheet2D", "sheet2d", "latest_sheet"}:
        from . import model2d
        return getattr(model2d, name)
    if name == "ConstraintSketch":
        from .constraints import ConstraintSketch
        return ConstraintSketch
    if name == "Imported2D":
        from .import2d import Imported2D
        return Imported2D
    raise AttributeError(name)


def make_drawing(
    ctx: Any,
    req: DrawingRequest | Mapping[str, Any] | None,
    out: str | Path,
    *,
    revision_status: str | None = None,
) -> DrawingResult:
    """Create a dimensioned drawing sheet from a structural model context."""
    from .sheet import make_drawing as _make_drawing

    return _make_drawing(ctx, req, out, revision_status=revision_status)


def project_drawing_view(shape: Any, view: DrawingView):
    """Project orthographic, section or circular detail geometry lazily."""
    from .views import project_drawing_view as project
    return project(shape, view)


def section_plane_trace(shape: Any, view: DrawingView):
    """Locate a section plane on its parent projection."""
    from .views import section_plane_trace as trace
    return trace(shape, view)


def checks(ctx: Any, *, limits=None):
    """Validate native 2D geometry and explicit laser limits lazily."""
    from .validation2d import checks as validate
    return validate(ctx, limits=limits)


def measurement_evidence(ctx: Any):
    """Return actual cut-geometry measurements with provenance and coverage."""
    from .validation2d import measurement_evidence as measure
    return measure(ctx)


def make_scene(ctx: Any, out: str | Path):
    """Create a deterministic svg-1 package with draft status."""
    from .scene2d import make_scene as write
    return write(ctx, Path(out))


def export_2d(ctx: Any, fmt: str, out: str | Path, *, layers=None):
    """Export exact native geometry and layers to raw SVG or R2013 DXF."""
    from .io2d import export_2d as write
    return write(ctx, fmt, Path(out), layers=layers)


def parse_dxf(path: str | Path, units=None, layer_map=None):
    from .import2d import parse_dxf as parse
    return parse(path, units=units, layer_map=layer_map)


def parse_svg(path: str | Path, units="mm", layer_map=None):
    from .import2d import parse_svg as parse
    return parse(path, units=units, layer_map=layer_map)
