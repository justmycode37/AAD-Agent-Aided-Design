"""Collision-aware sheet composition and geometry-independent annotations.

The module only arranges projections and drawing text. It does not infer model
features; dimension values and assembly children are supplied by the caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
from typing import Any

from .export import Primitive, _font, line, text
from .geometry import ProjectedBounds, ProjectedView


MARGIN_MM = 10.0
TITLE_BLOCK_WIDTH_MM = 80.0
MIN_TEXT_HEIGHT_MM = 2.5
VIEW_GAP_MM = 5.0
TEXT_LINE_MM = 4.0


@dataclass(frozen=True)
class Rect:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def width(self) -> float:
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        return self.ymax - self.ymin

    @property
    def center(self) -> tuple[float, float]:
        return ((self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2)

    def contains(self, other: Rect, tolerance: float = 1e-7) -> bool:
        return (
            other.xmin >= self.xmin - tolerance
            and other.ymin >= self.ymin - tolerance
            and other.xmax <= self.xmax + tolerance
            and other.ymax <= self.ymax + tolerance
        )

    def intersects(self, other: Rect, gap: float = 0.0) -> bool:
        return (
            self.xmin < other.xmax + gap
            and other.xmin < self.xmax + gap
            and self.ymin < other.ymax + gap
            and other.ymin < self.ymax + gap
        )

    def as_list(self) -> list[float]:
        return [self.xmin, self.ymin, self.xmax, self.ymax]


@dataclass(frozen=True)
class PackedPlacement:
    center: tuple[float, float]
    bounds: ProjectedBounds
    cell: Rect
    factor: float


@dataclass(frozen=True)
class PackedViews:
    scale: float
    placements: dict[str, PackedPlacement]
    area: Rect
    fits: bool


@dataclass(frozen=True)
class AuxiliaryLayout:
    entities: list[Primitive]
    title_bounds: Rect
    schedule_bounds: Rect | None
    bottom_keepout_mm: float


@lru_cache(maxsize=32)
def _font_at_size(size: int) -> Any:
    return _font(size)


@lru_cache(maxsize=4096)
def _text_metrics(value: str, height: float) -> tuple[float, float, float, float, float]:
    """Return advance, glyph left/right, ascent, and descent in sheet mm.

    Pillow measures the same selected font and nominal raster size as the PNG
    writer. ReportLab Helvetica's measured advance is included as a conservative
    bound for the vector PDF output.
    """
    height = float(height)
    if not math.isfinite(height) or height < MIN_TEXT_HEIGHT_MM:
        raise ValueError(f"Drawing text height must be at least {MIN_TEXT_HEIGHT_MM:g} mm.")
    value = str(value)
    font_size = max(8, round(height * 4.0))
    font = _font_at_size(font_size)
    pixels_per_mm = font_size / height
    try:
        left_px, top_px, right_px, bottom_px = font.getbbox(value, anchor="ls")
    except (TypeError, ValueError):
        left_px, top_px, right_px, bottom_px = font.getbbox(value)
        top_px -= font.getmetrics()[0]
        bottom_px -= font.getmetrics()[0]
    advance = float(font.getlength(value)) / pixels_per_mm
    left = float(left_px) / pixels_per_mm
    right = float(right_px) / pixels_per_mm
    ascent = max(0.0, -float(top_px) / pixels_per_mm)
    descent = max(0.0, float(bottom_px) / pixels_per_mm)
    try:
        from reportlab.pdfbase.pdfmetrics import stringWidth, getAscentDescent

        advance = max(advance, float(stringWidth(value, "Helvetica", height)))
        pdf_ascent,pdf_descent=getAscentDescent("Helvetica",height)
        ascent=max(ascent,pdf_ascent)
        descent=max(descent,-pdf_descent)
    except (ImportError, KeyError, ValueError):
        pass
    # DXF TEXT height is a cap height; raster/vector fonts commonly interpret
    # it as an em size. Reserve a conservative CAD stroke-font envelope too,
    # so font substitution in an independent CAD reader does not put glyphs
    # through table rules. This is layout space, never a geometry measurement.
    advance = advance * 1.55 + len(value) * height * 0.05
    ascent = max(ascent, height)
    descent = max(descent, height * 0.35)
    right = max(right, advance)
    return advance, min(0.0, left), right, ascent, descent


def text_width_mm(value: str, height: float) -> float:
    advance, left, right, _, _ = _text_metrics(str(value), height)
    return max(0.0, right - left, advance)


def text_bounds(entity: Primitive) -> Rect | None:
    if entity.kind != "text":
        return None
    x, y = entity.points[0]
    advance, left, right, ascent, descent = _text_metrics(entity.text or "", entity.height)
    if entity.align == "center":
        x_offset = -advance / 2
    elif entity.align == "right":
        x_offset = -advance
    else:
        x_offset = 0.0
    corners = [
        (x + x_offset + left, y - descent),
        (x + x_offset + right, y - descent),
        (x + x_offset + right, y + ascent),
        (x + x_offset + left, y + ascent),
    ]
    angle = math.radians(float(entity.rotation))
    if abs(angle) > 1e-12:
        cosine, sine = math.cos(angle), math.sin(angle)
        corners = [
            (x + (cx - x) * cosine - (cy - y) * sine,
             y + (cx - x) * sine + (cy - y) * cosine)
            for cx, cy in corners
        ]
    return Rect(
        min(point[0] for point in corners),
        min(point[1] for point in corners),
        max(point[0] for point in corners),
        max(point[1] for point in corners),
    )


def wrap_text(value: str, width_mm: float, height_mm: float = MIN_TEXT_HEIGHT_MM) -> list[str]:
    """Wrap at measured glyph advances; long tokens are split, never dropped."""
    value = str(value)
    width_mm = float(width_mm)
    if width_mm <= 0:
        return [value]
    output: list[str] = []
    paragraphs = value.splitlines() or [""]
    for paragraph in paragraphs:
        words = paragraph.split()
        if not words:
            output.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if text_width_mm(candidate, height_mm) <= width_mm:
                current = candidate
                continue
            if current:
                output.append(current)
                current = ""
            fragment = ""
            for character in word:
                candidate_fragment = fragment + character
                if fragment and text_width_mm(candidate_fragment, height_mm) > width_mm:
                    output.append(fragment)
                    fragment = character
                else:
                    fragment = candidate_fragment
            current = fragment
        output.append(current)
    return output or [""]


def hole_callout_text(dimension: Mapping[str, Any]) -> str:
    count = dimension.get("count") or 1
    prefix = f"{count}X " if count > 1 else ""
    return f"{prefix}DIA {dimension['display']}" + (" THRU" if dimension.get("through", True) else "")


def ordinate_count_prefix(dimension: Mapping[str, Any]) -> str:
    count = dimension.get("count") or 1
    return f"{count}X " if count > 1 else ""


def _view_padding(name: str, dimensions: Sequence[Mapping[str, Any]]) -> tuple[float, float, float, float]:
    """Return left, bottom, right, top annotation reserves in millimetres."""
    name = str(name).lower()
    left = right = bottom = 4.0
    top = 8.0
    if name == "front":
        x_dims = [d for d in dimensions if d.get("kind") == "overall" and d.get("axis") == "x"]
        z_dims = [d for d in dimensions if d.get("kind") == "overall" and d.get("axis") == "z"]
        if x_dims:
            bottom = max(bottom, 17.0)
        if z_dims:
            right = max(right, 17.0 + max(text_width_mm(str(d["display"]), 2.8) for d in z_dims))
        return left, bottom, right, top

    if name == "top":
        x_coordinates = [
            d for d in dimensions if d.get("kind") == "hole_coordinate" and d.get("axis") == "x"
        ]
        y_coordinates = [
            d for d in dimensions if d.get("kind") == "hole_coordinate" and d.get("axis") == "y"
        ]
        x_spacings = [
            d for d in dimensions if d.get("kind") == "hole_spacing" and d.get("axis") == "x"
        ]
        y_spacings = [
            d for d in dimensions if d.get("kind") == "hole_spacing" and d.get("axis") == "y"
        ]
        diameter_dims = [d for d in dimensions if d.get("kind") == "hole_diameter"]
        overall_y = [d for d in dimensions if d.get("kind") == "overall" and d.get("axis") == "y"]

        if x_coordinates:
            bottom = max(bottom, 14.0 + 4.5 * (len(x_coordinates) - 1))
        if x_spacings:
            bottom = max(
                bottom,
                23.0
                + 4.5 * max(0, len(x_coordinates) - 1)
                + 8.0 * (len(x_spacings) - 1),
            )
        if y_coordinates:
            coordinate_width = max(
                text_width_mm(f"{ordinate_count_prefix(d)}Y{d['display']}", 2.6)
                for d in y_coordinates
            )
            left = max(left, 17.0 + len(y_coordinates) * (coordinate_width + 2.0))
        if y_spacings:
            spacing_width = max(text_width_mm(str(d["display"]), 2.8) for d in y_spacings)
            left = max(
                left,
                23.0
                + (len(y_coordinates) * (max((text_width_mm(f"{ordinate_count_prefix(d)}Y{d['display']}", 2.6) for d in y_coordinates), default=0.0) + 2.0)
                   if y_coordinates else 0.0)
                + spacing_width
                + 8.0 * (len(y_spacings) - 1),
            )
        if overall_y:
            overall_y_reserve = 17.0 + max(text_width_mm(str(d["display"]), 2.8) for d in overall_y)
            if not y_coordinates and not y_spacings:
                left = max(left, overall_y_reserve)
            else:
                left += overall_y_reserve
        if diameter_dims:
            right = max(
                right,
                27.0 + max(text_width_mm(hole_callout_text(d), 2.8) for d in diameter_dims),
            )
        if any(d.get("planar") and d.get("kind") == "overall" and d.get("axis") == "x" for d in dimensions):
            bottom = max(17.0, bottom + 17.0)
        return left, bottom, right, top

    return left, bottom, right, top


def _bounds_at_scale(
    projection: ProjectedView,
    center: tuple[float, float],
    factor: float,
) -> ProjectedBounds:
    source = projection.bounds
    source_center = source.center
    return ProjectedBounds(
        center[0] + (source.xmin - source_center[0]) * factor,
        center[1] + (source.ymin - source_center[1]) * factor,
        center[0] + (source.xmax - source_center[0]) * factor,
        center[1] + (source.ymax - source_center[1]) * factor,
    )


def _slot(
    projection: ProjectedView,
    center: tuple[float, float],
    factor: float,
    padding: tuple[float, float, float, float],
) -> PackedPlacement:
    bounds = _bounds_at_scale(projection, center, factor)
    pad_left, pad_bottom, pad_right, pad_top = padding
    return PackedPlacement(
        center=center,
        bounds=bounds,
        cell=Rect(
            bounds.xmin - pad_left,
            bounds.ymin - pad_bottom,
            bounds.xmax + pad_right,
            bounds.ymax + pad_top,
        ),
        factor=factor,
    )


def _slot_size(
    projection: ProjectedView,
    factor: float,
    padding: tuple[float, float, float, float],
) -> tuple[float, float]:
    left, bottom, right, top = padding
    return (
        projection.bounds.width * factor + left + right,
        projection.bounds.height * factor + bottom + top,
    )


def _free_slot(
    projection: ProjectedView,
    factor: float,
    padding: tuple[float, float, float, float],
    area: Rect,
    occupied: Sequence[Rect],
    *,
    gap: float = VIEW_GAP_MM,
    allow_annotation_overlap: bool = False,
) -> PackedPlacement | None:
    cell_width, cell_height = _slot_size(projection, factor, padding)
    if cell_width > area.width + 1e-7 or cell_height > area.height + 1e-7:
        return None
    xs = {area.xmin, area.xmax - cell_width}
    ys = {area.ymin, area.ymax - cell_height}
    for rect in occupied:
        xs.update(
            (
                rect.xmin - cell_width - gap,
                rect.xmin,
                rect.center[0] - cell_width / 2,
                rect.xmax - cell_width,
                rect.xmax + gap,
            )
        )
        ys.update(
            (
                rect.ymin - cell_height - gap,
                rect.ymin,
                rect.center[1] - cell_height / 2,
                rect.ymax - cell_height,
                rect.ymax + gap,
            )
        )
    candidates: list[tuple[float, PackedPlacement]] = []
    pad_left, pad_bottom, _, _ = padding
    geom_width = projection.bounds.width * factor
    geom_height = projection.bounds.height * factor
    for x0 in sorted(xs):
        for y0 in sorted(ys):
            cell = Rect(x0, y0, x0 + cell_width, y0 + cell_height)
            if not area.contains(cell):
                continue
            placement_bounds = _bounds_at_scale(projection, center=(x0 + pad_left + geom_width / 2, y0 + pad_bottom + geom_height / 2), factor=factor)
            geometry_rect = Rect(
                placement_bounds.xmin,
                placement_bounds.ymin,
                placement_bounds.xmax,
                placement_bounds.ymax,
            )
            checked_rect = geometry_rect if allow_annotation_overlap else cell
            if any(checked_rect.intersects(other, max(0.0, gap - 1e-7)) for other in occupied):
                continue
            center = (x0 + pad_left + geom_width / 2, y0 + pad_bottom + geom_height / 2)
            placement = _slot(projection, center, factor, padding)
            group = [*occupied, cell]
            group_bounds = Rect(
                min(rect.xmin for rect in group),
                min(rect.ymin for rect in group),
                max(rect.xmax for rect in group),
                max(rect.ymax for rect in group),
            )
            distance = math.dist(group_bounds.center, area.center)
            score = -(group_bounds.width * group_bounds.height + distance * distance * 0.05)
            candidates.append((score, placement))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


def _pack_at_scale(
    projections: tuple[ProjectedView, ...],
    width: float,
    height: float,
    style: str,
    unit_factor: float,
    dimensions: Sequence[Mapping[str, Any]],
    bottom_keepout_mm: float,
    scale: float,
) -> PackedViews:
    area = Rect(
        MARGIN_MM + 3.0,
        MARGIN_MM + bottom_keepout_mm,
        width - MARGIN_MM - 3.0,
        height - MARGIN_MM - 4.0,
    )
    by_name = {projection.name: projection for projection in projections}
    padding = {name: _view_padding(name, dimensions) for name in by_name}
    factor = scale * unit_factor
    placements: dict[str, PackedPlacement] = {}

    if area.width > 0 and area.height > 0 and "front" in by_name:
        front = _slot(by_name["front"], (0.0, 0.0), factor, padding["front"])
        placements["front"] = front
        left_edge = front.cell.xmin
        right_edge = front.cell.xmax
        left_extent = right_extent = 0.0

        def add_side(name: str, side: str) -> None:
            nonlocal left_edge, right_edge, left_extent, right_extent
            projection = by_name[name]
            pad_left, _, pad_right, _ = padding[name]
            geom_width = projection.bounds.width * factor
            if side == "left":
                center_x = left_edge - VIEW_GAP_MM - pad_right - geom_width / 2
                placed = _slot(projection, (center_x, 0.0), factor, padding[name])
                left_edge = placed.cell.xmin
                left_extent = front.cell.xmin - left_edge
            else:
                center_x = right_edge + VIEW_GAP_MM + pad_left + geom_width / 2
                placed = _slot(projection, (center_x, 0.0), factor, padding[name])
                right_edge = placed.cell.xmax
                right_extent = right_edge - front.cell.xmax
            placements[name] = placed

        if style == "iso":
            left_order, right_order = ["right"], ["left"]
        else:
            left_order, right_order = ["left"], ["right"]
        for name in left_order:
            if name in by_name:
                add_side(name, "left")
        for name in right_order:
            if name in by_name:
                add_side(name, "right")
        if "back" in by_name:
            add_side("back", "left" if left_extent <= right_extent else "right")

        row_bottom = min(item.cell.ymin for item in placements.values())
        row_top = max(item.cell.ymax for item in placements.values())
        for name, above in (
            ("top", style == "asme"),
            ("bottom", style == "iso"),
        ):
            if name not in by_name:
                continue
            projection = by_name[name]
            _, pad_bottom, _, pad_top = padding[name]
            geom_height = projection.bounds.height * factor
            if above:
                center_y = row_top + VIEW_GAP_MM + pad_bottom + geom_height / 2
            else:
                center_y = row_bottom - VIEW_GAP_MM - pad_top - geom_height / 2
            placements[name] = _slot(projection, (0.0, center_y), factor, padding[name])

    # Pack in a sheet-centered local coordinate system, then translate the
    # complete composition. This lets an extra view claim space by shifting the
    # aligned group as a whole when that still fits the usable area.
    packing_area = Rect(-area.width / 2, -area.height / 2, area.width / 2, area.height / 2)
    occupied = [item.cell for item in placements.values()]
    remaining = [projection for projection in projections if projection.name not in placements]
    if "front" not in by_name:
        remaining.sort(
            key=lambda projection: _slot_size(projection, factor, padding[projection.name])[0]
            * _slot_size(projection, factor, padding[projection.name])[1],
            reverse=True,
        )
    for projection in remaining:
        # Dimension reserves are occupied space. Geometry in those gutters
        # can obscure arrows even when no text happens to cross a curve.
        extra_padding = (2.0, 2.0, 2.0, 8.0)
        placement = _free_slot(
            projection,
            factor,
            extra_padding,
            packing_area,
            occupied,
            gap=VIEW_GAP_MM,
            allow_annotation_overlap=False,
        )
        if placement is None:
            # Keep producing a reviewable sheet. The fit check reports the
            # unresolved overlap or overflow instead of hiding the condition.
            placement = _slot(projection, packing_area.center, factor, extra_padding)
        placements[projection.name] = placement
        occupied.append(placement.cell)

    if placements:
        cluster = Rect(
            min(item.cell.xmin for item in placements.values()),
            min(item.cell.ymin for item in placements.values()),
            max(item.cell.xmax for item in placements.values()),
            max(item.cell.ymax for item in placements.values()),
        )
        dx, dy = area.center[0] - cluster.center[0], area.center[1] - cluster.center[1]
        placements = {
            name: PackedPlacement(
                center=(item.center[0] + dx, item.center[1] + dy),
                bounds=ProjectedBounds(
                    item.bounds.xmin + dx,
                    item.bounds.ymin + dy,
                    item.bounds.xmax + dx,
                    item.bounds.ymax + dy,
                ),
                cell=Rect(
                    item.cell.xmin + dx,
                    item.cell.ymin + dy,
                    item.cell.xmax + dx,
                    item.cell.ymax + dy,
                ),
                factor=item.factor,
            )
            for name, item in placements.items()
        }

    fits = area.width > 0 and area.height > 0
    cells = list(placements.values())
    fits = fits and all(
        area.contains(item.cell)
        for item in cells
    )
    for index, first in enumerate(cells):
        first_bounds = first.cell
        if any(
            first_bounds.intersects(second.cell)
            for second in cells[index + 1 :]
        ):
            fits = False
            break
    return PackedViews(scale, placements, area, fits)


def pack_views(
    projections: tuple[ProjectedView, ...],
    width: float,
    height: float,
    style: str,
    unit_factor: float,
    dimensions: Sequence[Mapping[str, Any]],
    bottom_keepout_mm: float,
) -> PackedViews:
    """Select a physical scale and collision-aware view positions."""
    standard_scales = (10.0, 5.0, 2.0, 1.0, 0.75, 0.5, 0.4, 1 / 3, 0.25, 0.2, 1 / 6, 0.125, 0.1, 0.05)
    for scale in standard_scales:
        plan = _pack_at_scale(
            projections, width, height, style, unit_factor, dimensions, bottom_keepout_mm, scale
        )
        if plan.fits:
            return plan

    low, high = 0.0, 10.0
    zero = _pack_at_scale(
        projections, width, height, style, unit_factor, dimensions, bottom_keepout_mm, 0.0
    )
    if not zero.fits:
        return _pack_at_scale(
            projections, width, height, style, unit_factor, dimensions, bottom_keepout_mm, 1e-4
        )
    for _ in range(36):
        middle = (low + high) / 2
        if _pack_at_scale(
            projections, width, height, style, unit_factor, dimensions, bottom_keepout_mm, middle
        ).fits:
            low = middle
        else:
            high = middle
    selected = max((scale for scale in standard_scales if scale <= low + 1e-8), default=low)
    return _pack_at_scale(
        projections, width, height, style, unit_factor, dimensions, bottom_keepout_mm, max(selected, 1e-4)
    )


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _child_entries(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return [
            child
            if _value(child, "name") is not None
            else {"name": str(name), "shape": child}
            for name, child in value.items()
        ]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return None


def _children(node: Any) -> list[Any] | None:
    for name in ("children", "components", "parts"):
        value = _value(node, name)
        if value is not None:
            return _child_entries(value)
    return None


def bom_from_context(context: Any) -> list[dict[str, Any]]:
    """Count only component instances present in an exposed assembly tree."""
    root = _value(context, "assembly")
    if root is None:
        root = context
    entries = _children(root)
    if entries is None and root is not context:
        entries = _children(context)
    if entries is None:
        shape = _value(context, "shape", _value(context, "part"))
        entries = _children(shape)
    if not entries:
        raise ValueError(
            "A BOM was requested, but the drawing context exposes no assembly children. "
            "Quantities are counted from child instances and are not inferred from a combined shape."
        )

    grouped: list[dict[str, Any]] = []

    def visit(child: Any) -> None:
        nested = _children(child)
        if nested:
            for item in nested:
                visit(item)
            return
        metadata = _value(child, "metadata", {}) or {}
        shape = _value(child, "shape", _value(child, "obj", _value(child, "part")))
        if shape is None:
            shape = child
        name = (
            _value(child, "name")
            or _value(child, "label")
            or _value(metadata, "name")
            or _value(shape, "label")
            or _value(shape, "name")
        )
        part_number = (
            _value(child, "part_number")
            or _value(child, "part_no")
            or _value(child, "sku")
            or _value(metadata, "part_number")
            or _value(metadata, "sku")
        )
        component_id = _value(child, "component_id") or _value(metadata, "component_id")
        description = (
            _value(child, "description")
            or _value(metadata, "description")
            or name
            or "Unnamed component"
        )
        if part_number:
            key = ("part_number", str(part_number), id(shape))
        elif component_id:
            key = ("component_id", str(component_id), id(shape))
        else:
            key = ("object", id(shape))
        existing = next((row for row in grouped if row["_key"] == key), None)
        if existing is None:
            grouped.append(
                {
                    "_key": key,
                    "part_number": str(part_number) if part_number else "",
                    "description": str(description),
                    "quantity": 1,
                }
            )
        else:
            existing["quantity"] += 1

    for entry in entries:
        visit(entry)
    return [
        {
            "item": index,
            "part_number": row["part_number"],
            "description": row["description"],
            "quantity": row["quantity"],
        }
        for index, row in enumerate(grouped, start=1)
    ]


def _context_name(context: Any) -> str:
    name = _value(context, "name") or _value(context, "project")
    if not name and _value(context, "project_dir"):
        name = Path(_value(context, "project_dir")).name
    return str(name or "UNNAMED MODEL")


def _title_values(
    title_block: Mapping[str, str],
    context: Any,
    units: str,
    sheet: str,
    style: str,
    scale_label: str,
    revisions: Sequence[Mapping[str, str]],
) -> list[tuple[str, str]]:
    latest_revision = ""
    if revisions:
        latest_revision = revisions[-1].get("revision", "")
    if not latest_revision:
        latest_revision = str(_value(context, "revision", "") or "")
    values = {
        "title": str(title_block.get("title", _context_name(context))),
        "drawing_no": str(title_block.get("drawing_no", "-")),
        "revision": str(title_block.get("revision", latest_revision or "-")),
        "scale": scale_label,
        "units": units.upper(),
        "sheet": sheet.upper(),
        "style": style.upper(),
    }
    labels = {
        "title": "TITLE",
        "drawing_no": "DRAWING NO.",
        "revision": "REVISION",
        "scale": "SCALE",
        "units": "UNITS",
        "sheet": "SHEET",
        "style": "PROJECTION",
    }
    result = [(labels[key], values[key]) for key in ("title", "drawing_no", "revision", "scale", "units", "sheet", "style")]
    for key, value in title_block.items():
        if key in values:
            continue
        result.append((key.replace("_", " ").upper(), str(value)))
    return result


def _draw_rect(entities: list[Primitive], bounds: Rect, layer: str) -> None:
    entities.extend(
        [
            line(bounds.xmin, bounds.ymin, bounds.xmax, bounds.ymin, layer, 0.18),
            line(bounds.xmax, bounds.ymin, bounds.xmax, bounds.ymax, layer, 0.18),
            line(bounds.xmax, bounds.ymax, bounds.xmin, bounds.ymax, layer, 0.18),
            line(bounds.xmin, bounds.ymax, bounds.xmin, bounds.ymin, layer, 0.18),
        ]
    )


def _table_rows(
    headers: Sequence[str], rows: Sequence[Sequence[str]], widths: Sequence[float]
) -> tuple[list[list[list[str]]], float]:
    all_rows = [list(headers), *[list(row) for row in rows]]
    wrapped: list[list[list[str]]] = []
    height = 0.0
    for row in all_rows:
        cells = [
            wrap_text(value, max(1.0, width - 2.0), MIN_TEXT_HEIGHT_MM)
            for value, width in zip(row, widths)
        ]
        wrapped.append(cells)
        height += max(3.4, max(len(cell) for cell in cells) * TEXT_LINE_MM + 1.1)
    return wrapped, height


def _draw_table(
    entities: list[Primitive],
    x: float,
    top: float,
    widths: Sequence[float],
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    layer: str,
    dimension_ids=None,
) -> float:
    wrapped, _ = _table_rows(headers, rows, widths)
    x_edges = [x]
    for width in widths:
        x_edges.append(x_edges[-1] + width)
    cursor = top
    for row_index, cells in enumerate(wrapped):
        row_height = max(3.4, max(len(cell) for cell in cells) * TEXT_LINE_MM + 1.1)
        entities.append(line(x, cursor, x_edges[-1], cursor, layer, 0.12))
        for column_index, cell_lines in enumerate(cells):
            for line_index, value in enumerate(cell_lines):
                entities.append(
                    text(
                        value,
                        x_edges[column_index] + 1.0,
                        cursor - 2.7 - line_index * TEXT_LINE_MM,
                        MIN_TEXT_HEIGHT_MM,
                        layer,
                        dimension_id=(dimension_ids[row_index-1][column_index] if dimension_ids and row_index else None),
                    )
                )
        cursor -= row_height
    entities.append(line(x, cursor, x_edges[-1], cursor, layer, 0.12))
    for edge in x_edges:
        entities.append(line(edge, top, edge, cursor, layer, 0.12))
    return cursor


def compose_auxiliary_layout(
    width: float,
    height: float,
    *,
    title_block: Mapping[str, str],
    notes: Sequence[str],
    revisions: Sequence[Mapping[str, str]],
    bom: Sequence[Mapping[str, Any]],
    context: Any,
    units: str,
    sheet: str,
    style: str,
    scale_label: str,
    cylinders=(),
    profiles=(),
) -> AuxiliaryLayout:
    """Create a complete, wrapped title block and requested schedule tables."""
    title_width = min(TITLE_BLOCK_WIDTH_MM, width - 2 * MARGIN_MM)
    title_x0 = width - MARGIN_MM - title_width
    title_y0 = MARGIN_MM
    title_fields = _title_values(title_block, context, units, sheet, style, scale_label, revisions)
    title_lines = [
        (label, wrap_text(f"{label}: {value}", title_width - 5.0, MIN_TEXT_HEIGHT_MM))
        for label, value in title_fields
    ]
    title_height = 4.8 + sum(len(lines) * TEXT_LINE_MM + 0.9 for _, lines in title_lines)
    title_bounds = Rect(title_x0, title_y0, title_x0 + title_width, title_y0 + title_height)
    entities: list[Primitive] = []
    _draw_rect(entities, title_bounds, "TITLE")
    cursor = title_bounds.ymax - 3.0
    for index, (_, lines) in enumerate(title_lines):
        for line_index, value in enumerate(lines):
            entities.append(
                text(value, title_x0 + 2.0, cursor - line_index * TEXT_LINE_MM, MIN_TEXT_HEIGHT_MM, "TITLE")
            )
        cursor -= len(lines) * TEXT_LINE_MM + 0.9
        if index + 1 < len(title_lines):
            entities.append(line(title_x0, cursor + 2.9, title_x0 + title_width, cursor + 2.9, "TITLE", 0.12))

    schedule_sections: list[tuple[str, str, Any]] = []
    if notes:
        note_rows = [wrap_text(f"{index}. {note}", 0.0, MIN_TEXT_HEIGHT_MM) for index, note in enumerate(notes, start=1)]
        schedule_sections.append(("notes", "NOTES", note_rows))
    if revisions:
        schedule_sections.append(("table", "REVISION HISTORY", list(revisions)))
    if bom:
        schedule_sections.append(("table", "BILL OF MATERIALS", list(bom)))
    if cylinders:
        schedule_sections.append(("table", "CYLINDRICAL FEATURES - STATIONS AND CENTERS FROM MINIMUM BOUNDS",list(cylinders)))
    if profiles:
        schedule_sections.append(("table", "PLANAR FEATURES - X/Y FROM MINIMUM CUT BOUNDS; ANGLE OF LONG AXIS", list(profiles)))

    schedule_bounds: Rect | None = None
    if schedule_sections:
        schedule_x0 = MARGIN_MM
        schedule_width = max(20.0, title_x0 - 6.0 - schedule_x0)
        section_heights: list[float] = []
        table_specs: list[tuple[list[str], list[list[str]], list[float]] | None] = []
        for kind, _, data in schedule_sections:
            if kind == "notes":
                rows = [wrap_text(row[0], schedule_width - 4.0, MIN_TEXT_HEIGHT_MM) for row in data]
                body_height = sum(max(3.4, len(row) * TEXT_LINE_MM + 1.0) for row in rows)
                section_heights.append(5.0 + body_height + 1.5)
                table_specs.append(None)
            elif _.startswith("PLANAR FEATURES"):
                widths = [(schedule_width - 2) * fraction for fraction in (.12, .16, .13, .13, .15, .16, .15)]
                headers = ("FEATURE", "KIND", "X", "Y", "WIDTH", "LENGTH", "ANGLE")
                rows = [[str(row[key]) for key in ("feature", "kind", "x", "y", "width", "length", "angle")] for row in data]
                section_heights.append(5 + _table_rows(headers, rows, widths)[1] + 1.5)
                table_specs.append((headers, rows, widths, [row["dimension_ids"] for row in data]))
            elif _.startswith("CYLINDRICAL FEATURES"):
                widths=[(schedule_width-2)*fraction for fraction in (0.18,0.13,0.08,0.23,0.11,0.11,0.16)]
                rows=[[str(row[key]) for key in ("surface","size","axis","center","from","to","length")] for row in data]
                headers=("SURFACE","SIZE","AXIS","CENTER PERP.","FROM","TO","LENGTH")
                section_heights.append(5.0+_table_rows(headers,rows,widths)[1]+1.5)
                table_specs.append((headers,rows,widths,[row["dimension_ids"] for row in data]))
            elif _ == "REVISION HISTORY":
                widths = [14.0, 28.0, max(20.0, schedule_width - 62.0), 18.0]
                rows = [
                    [row.get("revision", ""), row.get("date", ""), row.get("description", ""), row.get("by", "")]
                    for row in data
                ]
                wrapped, body_height = _table_rows(("REV", "DATE", "DESCRIPTION", "BY"), rows, widths)
                section_heights.append(5.0 + body_height + 1.5)
                table_specs.append((("REV", "DATE", "DESCRIPTION", "BY"), rows, widths))
            else:
                widths = [14.0, 28.0, max(20.0, schedule_width - 58.0), 14.0]
                rows = [
                    [str(row.get("item", "")), str(row.get("part_number", "")), str(row.get("description", "")), str(row.get("quantity", ""))]
                    for row in data
                ]
                section_heights.append(
                    5.0 + _table_rows(("ITEM", "PART NO.", "DESCRIPTION", "QTY"), rows, widths)[1] + 1.5
                )
                table_specs.append((("ITEM", "PART NO.", "DESCRIPTION", "QTY"), rows, widths))
        schedule_height = 4.0 + sum(section_heights)
        schedule_bounds = Rect(schedule_x0, title_y0, schedule_x0 + schedule_width, title_y0 + schedule_height)
        _draw_rect(entities, schedule_bounds, "SCHEDULE")
        cursor = schedule_bounds.ymax - 2.0
        for (kind, heading, data), spec, section_height in zip(schedule_sections, table_specs, section_heights):
            entities.append(text(heading, schedule_x0 + 1.5, cursor - 2.5, 2.8, "SCHEDULE"))
            cursor -= 4.8
            if kind == "notes":
                for note_lines in data:
                    wrapped_lines = wrap_text(note_lines[0], schedule_width - 4.0, MIN_TEXT_HEIGHT_MM)
                    row_height = max(3.4, len(wrapped_lines) * TEXT_LINE_MM + 1.0)
                    for line_index, value in enumerate(wrapped_lines):
                        entities.append(
                            text(value, schedule_x0 + 1.5, cursor - 2.5 - line_index * TEXT_LINE_MM, MIN_TEXT_HEIGHT_MM, "SCHEDULE")
                        )
                    cursor -= row_height
            elif spec is not None:
                headers, rows, widths = spec[:3]
                cursor = _draw_table(entities, schedule_x0 + 1.0, cursor, widths, headers, rows, "SCHEDULE",
                                     dimension_ids=spec[3] if len(spec)>3 else None)
            cursor -= 1.2
        # Table and note heights are measured before emission. Keep this
        # explicit so a future content type cannot silently overrun its panel.
    schedule_height = schedule_bounds.height if schedule_bounds else 0.0
    bottom_keepout = max(title_bounds.height, schedule_height) + 7.0
    return AuxiliaryLayout(entities, title_bounds, schedule_bounds, bottom_keepout)


def entity_bounds(entity: Primitive) -> Rect | None:
    if entity.kind == "text":
        return text_bounds(entity)
    if entity.kind == "line":
        xs = [point[0] for point in entity.points]
        ys = [point[1] for point in entity.points]
        return Rect(min(xs), min(ys), max(xs), max(ys))
    if entity.kind == "polyline":
        if not entity.points:
            return None
        xs = [point[0] for point in entity.points]
        ys = [point[1] for point in entity.points]
        return Rect(min(xs), min(ys), max(xs), max(ys))
    if entity.kind == "circle":
        cx, cy = entity.center
        radius = float(entity.radius)
        return Rect(cx - radius, cy - radius, cx + radius, cy + radius)
    if entity.kind == "arc":
        cx, cy = entity.center
        radius = float(entity.radius)
        start = float(entity.start_angle)
        end = float(entity.end_angle)
        sweep = (end - start) % 360 if entity.ccw else -((start - end) % 360)
        magnitude = abs(sweep)
        angles = [start, end]
        for cardinal in (0.0, 90.0, 180.0, 270.0):
            distance = (cardinal - start) % 360 if sweep >= 0 else (start - cardinal) % 360
            if distance <= magnitude + 1e-8:
                angles.append(cardinal)
        points = [
            (cx + radius * math.cos(math.radians(angle)), cy + radius * math.sin(math.radians(angle)))
            for angle in angles
        ]
        return Rect(
            min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points),
        )
    return None


def _expanded(rect: Rect, amount: float) -> Rect:
    return Rect(rect.xmin - amount, rect.ymin - amount, rect.xmax + amount, rect.ymax + amount)


def _segment_intersects_rect(start: tuple[float, float], end: tuple[float, float], rect: Rect) -> bool:
    dx, dy = end[0] - start[0], end[1] - start[1]
    lower, upper = 0.0, 1.0
    for p, q in (
        (-dx, start[0] - rect.xmin),
        (dx, rect.xmax - start[0]),
        (-dy, start[1] - rect.ymin),
        (dy, rect.ymax - start[1]),
    ):
        if abs(p) <= 1e-15:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def _arc_points(entity: Primitive) -> list[tuple[float, float]]:
    start, end = float(entity.start_angle), float(entity.end_angle)
    sweep = (end - start) % 360 if entity.ccw else -((start - end) % 360)
    steps = max(1, math.ceil(abs(sweep) / 4.0))
    cx, cy = entity.center
    radius = float(entity.radius)
    return [
        (
            cx + radius * math.cos(math.radians(start + sweep * index / steps)),
            cy + radius * math.sin(math.radians(start + sweep * index / steps)),
        )
        for index in range(steps + 1)
    ]


def primitive_intersects_rect(entity: Primitive, rect: Rect) -> bool:
    """Test whether a projected stroke intersects a text rectangle."""
    pad = max(0.0, float(entity.width)) / 2
    target = _expanded(rect, pad)
    bounds = entity_bounds(entity)
    if bounds is None or not bounds.intersects(target):
        return False
    if entity.kind == "line":
        return _segment_intersects_rect(entity.points[0], entity.points[1], target)
    if entity.kind == "polyline":
        return any(_segment_intersects_rect(a, b, target) for a, b in zip(entity.points, entity.points[1:]))
    if entity.kind == "circle":
        cx, cy = entity.center
        radius = float(entity.radius)
        near_x = min(max(cx, target.xmin), target.xmax)
        near_y = min(max(cy, target.ymin), target.ymax)
        minimum = math.hypot(cx - near_x, cy - near_y)
        farthest = max(
            math.hypot(cx - x, cy - y)
            for x in (target.xmin, target.xmax)
            for y in (target.ymin, target.ymax)
        )
        return minimum <= radius + pad and farthest >= max(0.0, radius - pad)
    if entity.kind == "arc":
        points = _arc_points(entity)
        return any(_segment_intersects_rect(a, b, target) for a, b in zip(points, points[1:]))
    return False


def text_text_collisions(entities: Sequence[Primitive]) -> list[tuple[int, int]]:
    items = [(index, text_bounds(entity)) for index, entity in enumerate(entities) if entity.kind == "text"]
    collisions: list[tuple[int, int]] = []
    for offset, (first_index, first) in enumerate(items):
        for second_index, second in items[offset + 1 :]:
            if first and second and first.intersects(second, 0.15):
                collisions.append((first_index, second_index))
    return collisions


def text_view_collisions(
    entities: Sequence[Primitive], view_geometry: Mapping[str, Sequence[Primitive]]
) -> list[tuple[int, str, str]]:
    text_items = [
        (index, entity.text or "", text_bounds(entity))
        for index, entity in enumerate(entities)
        if entity.kind == "text"
    ]
    collisions: list[tuple[int, str, str]] = []
    for text_index, value, bounds in text_items:
        if bounds is None:
            continue
        for view_name, geometry in view_geometry.items():
            if any(primitive_intersects_rect(item, bounds) for item in geometry):
                collisions.append((text_index, view_name, value))
    return collisions


def text_margin_overflow(entities: Sequence[Primitive], width: float, height: float) -> list[int]:
    overflow: list[int] = []
    for index, entity in enumerate(entities):
        if entity.kind != "text":
            continue
        bounds = text_bounds(entity)
        if bounds and (
            bounds.xmin < MARGIN_MM - 1e-6
            or bounds.ymin < MARGIN_MM - 1e-6
            or bounds.xmax > width - MARGIN_MM + 1e-6
            or bounds.ymax > height - MARGIN_MM + 1e-6
        ):
            overflow.append(index)
    return overflow


def drawing_geometry_margin_overflow(
    entities: Sequence[Primitive], width: float, height: float
) -> list[int]:
    overflow: list[int] = []
    for index, entity in enumerate(entities):
        if entity.kind == "text" or entity.layer == "BORDER":
            continue
        bounds = entity_bounds(entity)
        if bounds and (
            bounds.xmin < MARGIN_MM - 1e-6
            or bounds.ymin < MARGIN_MM - 1e-6
            or bounds.xmax > width - MARGIN_MM + 1e-6
            or bounds.ymax > height - MARGIN_MM + 1e-6
        ):
            overflow.append(index)
    return overflow


def bounds_collide(first: ProjectedBounds, second: ProjectedBounds) -> bool:
    return Rect(first.xmin, first.ymin, first.xmax, first.ymax).intersects(
        Rect(second.xmin, second.ymin, second.xmax, second.ymax)
    )
