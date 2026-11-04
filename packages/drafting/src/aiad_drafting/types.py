"""Small public data types for AIAD drawing requests and results.

These types deliberately have no build123d or engine imports. Callers may pass
engine objects, mapping-shaped requests, or these dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DrawingView:
    """A named orthographic or isometric view.

    direction points from the model toward the viewer. up selects the vertical
    direction on the sheet. The standard view names are front, top, right, and
    iso/isometric. Custom vectors are accepted for future views.
    """

    name: str = "front"
    direction: tuple[float, float, float] | None = None
    up: tuple[float, float, float] | None = None
    label: str | None = None
    hidden_lines: bool = True


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
    formats: Sequence[str] = ("svg", "dxf", "pdf")


@dataclass
class DrawingResult:
    """Output of make_drawing with paths and geometry provenance."""

    artifacts: dict[str, Path]
    preview: Path
    dimensions: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    sheet: dict[str, Any]
    views: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True
    summary: str = ""
