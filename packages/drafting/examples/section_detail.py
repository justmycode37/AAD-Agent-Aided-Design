"""Render a true section and a circular detail using the direct export helpers.

Run with:

    python examples/section_detail.py

The sample exports are written under ``samples/section-detail``. Sheet-level
layout integration remains in ``sheet.py`` and can consume the same
``ProjectedView`` results through its existing placement pipeline.
"""

from __future__ import annotations

from pathlib import Path

from build123d import Box, Cylinder, Pos

from aad_drafting.export import arc, circle, line, polyline, text, write_dxf, write_png, write_svg
from aad_drafting.geometry import Curve, ProjectedBounds, ProjectedView
from aad_drafting.types import DrawingView
from aad_drafting.views import project_drawing_view


OUTPUT = Path(__file__).resolve().parents[1] / "samples" / "section-detail"
SHEET_WIDTH_MM = 360.0
SHEET_HEIGHT_MM = 190.0


def build_drilled_plate():
    plate = Box(80.0, 50.0, 6.0)
    for x, y in ((-20.0, -10.0), (20.0, -10.0), (-20.0, 10.0), (20.0, 10.0)):
        plate = plate - Pos(x, y, 0.0) * Cylinder(3.0, 6.0)
    return plate


def _place_point(
    point: tuple[float, float],
    bounds: ProjectedBounds,
    center: tuple[float, float],
    factor: float,
) -> tuple[float, float]:
    return (
        center[0] + (point[0] - bounds.center[0]) * factor,
        center[1] + (point[1] - bounds.center[1]) * factor,
    )


def _emit_curve(curve: Curve, projection: ProjectedView, center, factor, layer):
    if curve.kind == "line":
        first = _place_point(curve.points[0], projection.bounds, center, factor)
        last = _place_point(curve.points[-1], projection.bounds, center, factor)
        return [line(*first, *last, layer=layer, width=0.28)]
    if curve.kind == "polyline":
        points = [
            _place_point(point, projection.bounds, center, factor)
            for point in curve.points
        ]
        return [polyline(points, layer=layer, width=0.24)]
    if curve.kind == "circle":
        circle_center = _place_point(curve.center, projection.bounds, center, factor)
        return [circle(circle_center, curve.radius * factor, layer=layer, width=0.28)]
    if curve.kind == "arc":
        arc_center = _place_point(curve.center, projection.bounds, center, factor)
        return [
            arc(
                arc_center,
                curve.radius * factor,
                curve.start_angle,
                curve.end_angle,
                curve.ccw,
                layer=layer,
                width=0.28,
            )
        ]
    return []


def _panel(projection: ProjectedView, center, label: str, max_width: float, max_height: float):
    factor = min(
        max_width / max(projection.bounds.width, 1e-9),
        max_height / max(projection.bounds.height, 1e-9),
    )
    entities = []
    if projection.metadata.get("kind") == "section":
        for curve in projection.metadata["boundary_curves"]:
            entities.extend(_emit_curve(curve, projection, center, factor, "VISIBLE"))
        for curve in projection.metadata["hatch_curves"]:
            entities.extend(_emit_curve(curve, projection, center, factor, "HATCH"))
    else:
        for curve in projection.curves:
            layer = "HIDDEN" if curve.hidden else "VISIBLE"
            entities.extend(_emit_curve(curve, projection, center, factor, layer))
    entities.append(text(label, center[0], center[1] + max_height / 2 + 7.0, height=4.0))
    return entities


def render_sample(output: Path = OUTPUT) -> tuple[Path, Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    plate = build_drilled_plate()
    section = project_drawing_view(
        plate,
        DrawingView(name="section A-A", kind="section", plane="XY", offset=0.0),
    )
    detail = project_drawing_view(
        plate,
        DrawingView(
            name="detail B",
            kind="detail",
            parent="top",
            center=(-30.0, -10.0),
            radius=15.0,
            scale=2.5,
        ),
    )

    entities = _panel(section, (90.0, 90.0), "SECTION A-A", 135.0, 100.0)
    entities.extend(_panel(detail, (270.0, 90.0), "DETAIL B", 125.0, 105.0))
    svg_path = output / "drawing.svg"
    png_path = output / "drawing.png"
    dxf_path = output / "drawing.dxf"
    write_svg(svg_path, SHEET_WIDTH_MM, SHEET_HEIGHT_MM, entities)
    write_png(png_path, SHEET_WIDTH_MM, SHEET_HEIGHT_MM, entities)
    write_dxf(dxf_path, SHEET_WIDTH_MM, SHEET_HEIGHT_MM, entities)
    return svg_path, png_path, dxf_path


if __name__ == "__main__":
    for artifact in render_sample():
        print(artifact)
