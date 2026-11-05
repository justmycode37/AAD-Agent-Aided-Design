"""Small public data types for AAD drawing requests and results.

These types deliberately have no build123d or engine imports. Callers may pass
engine objects, mapping-shaped requests, or these dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DrawingView:
    """A named orthographic, section, or detail view.

    direction points from the model toward the viewer. up selects the vertical
    direction on the sheet. The standard view names are front, top, right, and
    iso/isometric. Custom vectors are accepted for future views.

    A section uses plane and offset to define a cutting plane in model units.
    A detail uses center and radius in the projected coordinates of parent.
    scale is a multiplier applied to the projected view geometry.
    """

    name: str = "front"
    direction: tuple[float, float, float] | None = None
    up: tuple[float, float, float] | None = None
    label: str | None = None
    hidden_lines: bool = True
    kind: str = "orthographic"
    plane: str = "XY"
    offset: float = 0.0
    center: tuple[float, float] | None = None
    radius: float | None = None
    scale: float = 1.0
    parent: str | None = None

    @property
    def scale_multiplier(self) -> float:
        """The detail/section magnification multiplier (alias for scale)."""
        return self.scale


@dataclass(frozen=True)
class DimensionSpec:
    """Select a geometry-derived dimension to show.

    Supported kind values are overall, hole_diameter, hole_coordinate, and
    hole_spacing. axis is x, y, or z where applicable. index selects a
    zero-based hole for coordinate dimensions. source is a stable descriptive
    selector such as bbox.x or holes[0].diameter; it never carries the numeric
    dimension value.
    """

    kind: str
    axis: str | None = None
    index: int | None = None
    source: str | None = None
    precision: int = 2
    tolerance: float | None = None


@dataclass(frozen=True)
class DrawingRequest:
    sheet: str = "A4"
    views: Sequence[DrawingView | str | Mapping[str, Any]] = (
        DrawingView("front"),
        DrawingView("top"),
        DrawingView("right"),
        DrawingView("iso"),
    )
    dimensions: str | Sequence[DimensionSpec | Mapping[str, Any]] = "auto"
    style: str = "iso"
    title_block: Mapping[str, str] = field(default_factory=dict)
    notes: Sequence[str] = ()
    revisions: Sequence[Mapping[str, str]] = ()
    bom: bool | str = False
    formats: Sequence[str] = ("svg", "dxf", "pdf")


@dataclass
class DrawingResult:
    """Output of make_drawing with paths and geometry provenance."""

    artifacts: dict[str, Path]
    preview: Path | None
    dimensions: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    sheet: dict[str, Any]
    views: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True
    summary: str = ""
    notes: list[str] = field(default_factory=list)
    revisions: list[dict[str, str]] = field(default_factory=list)
    bom: list[dict[str, Any]] = field(default_factory=list)
